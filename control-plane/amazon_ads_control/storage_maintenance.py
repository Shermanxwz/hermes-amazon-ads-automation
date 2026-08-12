from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import threading
from typing import Any

from . import db as db_module
from .policy import redact, redact_text
from .reporting import TERMINAL_REPORT_STATES

UTC = timezone.utc
_INSTALLED = False
_MAINTENANCE_LOCK = threading.Lock()

_IDENTIFIER_KEYS = {
    "reportid": "report_id",
    "campaignid": "campaign_id",
    "adgroupid": "ad_group_id",
    "targetid": "target_id",
    "keywordid": "keyword_id",
    "recommendationid": "recommendation_id",
    "profileid": "profile_id",
}
_STATUS_KEYS = {"status", "state", "reportstatus"}


def _norm_key(value: str) -> str:
    return "".join(character for character in str(value).lower() if character.isalnum())


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _compact_envelope(value: Any, max_bytes: int) -> tuple[Any, str | None, bool, int]:
    if value is None:
        return None, None, False, 0
    safe = redact(value)
    encoded = json.dumps(
        safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    if len(encoded) <= max_bytes:
        return safe, digest, False, len(encoded)

    identifiers: dict[str, list[str]] = {}
    statuses: list[str] = []
    for obj in _walk(safe):
        for key, item in obj.items():
            normalized = _norm_key(key)
            canonical = _IDENTIFIER_KEYS.get(normalized)
            if canonical and isinstance(item, (str, int)):
                values = identifiers.setdefault(canonical, [])
                text = str(item)
                if text not in values and len(values) < 16:
                    values.append(text)
            if normalized in _STATUS_KEYS and isinstance(item, (str, int)):
                text = str(item)
                if text not in statuses and len(statuses) < 16:
                    statuses.append(text)

    compact: dict[str, Any] = {
        "_compacted": True,
        "sha256": digest,
        "original_bytes": len(encoded),
        "identifiers": identifiers,
    }
    report_ids = identifiers.get("report_id", [])
    if len(report_ids) == 1:
        compact["report_id"] = report_ids[0]
    elif report_ids:
        compact["report_ids"] = report_ids
    if len(statuses) == 1:
        compact["status"] = statuses[0]
    elif statuses:
        compact["statuses"] = statuses
    if isinstance(safe, dict):
        compact["top_level_keys"] = sorted(str(key) for key in safe)[:100]
    elif isinstance(safe, list):
        compact["item_count"] = len(safe)
    return compact, digest, True, len(encoded)


def _path_sizes(path: Path) -> dict[str, int]:
    sizes: dict[str, int] = {}
    for label, candidate in (
        ("database", path),
        ("wal", Path(str(path) + "-wal")),
        ("shm", Path(str(path) + "-shm")),
    ):
        try:
            sizes[label] = candidate.stat().st_size
        except FileNotFoundError:
            sizes[label] = 0
    sizes["total"] = sum(sizes.values())
    return sizes


def _cutoff(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=max(1, int(days)))).isoformat(timespec="seconds")


def _pressure(before: dict[str, Any], settings: Any) -> str:
    total_mb = float(before["files"]["total_mb"])
    free_mb = float(before["filesystem"]["free_mb"])
    soft_limit = float(getattr(settings, "storage_soft_limit_mb", 512))
    hard_limit = float(getattr(settings, "storage_hard_limit_mb", 1024))
    minimum_free = float(getattr(settings, "min_free_disk_mb", 1024))
    if total_mb >= hard_limit or free_mb < max(128.0, minimum_free / 4.0):
        return "hard"
    if total_mb >= soft_limit or free_mb < minimum_free:
        return "soft"
    return "normal"


def _effective_days(configured: int, pressure: str, *, soft: int, hard: int) -> int:
    configured = max(1, int(configured))
    if pressure == "hard":
        return min(configured, hard)
    if pressure == "soft":
        return min(configured, soft)
    return configured


def _ensure_schema(store: Any) -> None:
    with store.connection() as conn:
        for table, column, definition in (
            ("actions", "args_hash", "TEXT"),
            ("actions", "stored_result_hash", "TEXT"),
            ("actions", "compacted_at", "TEXT"),
            ("report_jobs", "normalized_snapshot_gzip", "BLOB"),
        ):
            db_module.Store._ensure_column(conn, table, column, definition)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS maintenance_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                pressure TEXT NOT NULL,
                result_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_maintenance_runs_finished
                ON maintenance_runs(finished_at DESC);
            """
        )


def _storage_status(store: Any) -> dict[str, Any]:
    sizes = _path_sizes(store.path)
    usage = shutil.disk_usage(store.path.parent)
    with store.connection() as conn:
        page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
        page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
        freelist_count = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
        journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0])
        counts = {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "cycles", "metric_rows", "tasks", "decisions", "actions", "verifications",
                "events", "alerts", "workers", "stream_events", "report_jobs",
                "report_transitions", "callback_events",
            )
            if db_module.Store._table_exists(conn, table)
        }
        active_tasks = int(conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status NOT IN ('completed','completed_with_issues','failed','cancelled')"
        ).fetchone()[0])
        latest = conn.execute(
            "SELECT finished_at,pressure,result_json FROM maintenance_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    latest_run = None
    if latest:
        latest_run = {
            "finished_at": latest["finished_at"],
            "pressure": latest["pressure"],
            "result": json.loads(latest["result_json"]),
        }
    return {
        "files": {
            **{key: value for key, value in sizes.items()},
            "database_mb": round(sizes["database"] / 1048576, 3),
            "wal_mb": round(sizes["wal"] / 1048576, 3),
            "total_mb": round(sizes["total"] / 1048576, 3),
        },
        "filesystem": {
            "total_mb": round(usage.total / 1048576, 1),
            "used_mb": round(usage.used / 1048576, 1),
            "free_mb": round(usage.free / 1048576, 1),
            "free_percent": round((usage.free / usage.total) * 100, 2) if usage.total else 0,
        },
        "sqlite": {
            "journal_mode": journal_mode,
            "page_size": page_size,
            "page_count": page_count,
            "freelist_count": freelist_count,
            "reclaimable_mb": round((page_size * freelist_count) / 1048576, 3),
        },
        "row_counts": counts,
        "active_tasks": active_tasks,
        "latest_maintenance": latest_run,
        "generated_at": db_module.now_iso(),
    }


def _compact_actions(conn: sqlite3.Connection, cutoff: str, limit: int) -> int:
    rows = conn.execute(
        "SELECT a.id,a.args_json,a.result_json,a.result_summary "
        "FROM actions a LEFT JOIN tasks t ON t.id=a.task_id "
        "WHERE a.created_at<? AND a.compacted_at IS NULL "
        "AND (a.task_id IS NULL OR t.completed_at IS NOT NULL) "
        "ORDER BY a.id LIMIT ?",
        (cutoff, max(1, limit)),
    ).fetchall()
    now = db_module.now_iso()
    for row in rows:
        args_raw = str(row["args_json"] or "{}")
        result_raw = str(row["result_json"] or "")
        args_hash = hashlib.sha256(args_raw.encode("utf-8")).hexdigest()
        result_hash = hashlib.sha256(result_raw.encode("utf-8")).hexdigest() if result_raw else None
        summary = str(row["result_summary"] or "")
        marker = "[raw payload compacted; hashes retained]"
        if marker not in summary:
            summary = (summary + " " + marker).strip()
        conn.execute(
            "UPDATE actions SET args_json='{}',result_json=NULL,args_hash=?,stored_result_hash=?,"
            "result_summary=?,compacted_at=? WHERE id=?",
            (args_hash, result_hash, summary[:4000], now, row["id"]),
        )
    return len(rows)


def _compact_legacy_report_transitions(conn: sqlite3.Connection, cutoff: str, limit: int) -> int:
    rows = conn.execute(
        "SELECT id,data_json FROM report_transitions WHERE created_at<? "
        "AND instr(data_json,'\"snapshot\"')>0 ORDER BY id LIMIT ?",
        (cutoff, max(1, limit)),
    ).fetchall()
    changed = 0
    for row in rows:
        try:
            data = json.loads(row["data_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        snapshot = data.pop("snapshot", None)
        if snapshot is None:
            continue
        encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        data["snapshot_removed_by_maintenance"] = True
        data["snapshot_sha256"] = hashlib.sha256(encoded).hexdigest()
        data["snapshot_original_bytes"] = len(encoded)
        conn.execute(
            "UPDATE report_transitions SET data_json=? WHERE id=?",
            (json.dumps(data, ensure_ascii=False, sort_keys=True, default=str), row["id"]),
        )
        changed += 1
    return changed


def _checkpoint(store: Any) -> dict[str, Any]:
    try:
        with store.connection() as conn:
            row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            conn.execute("PRAGMA optimize")
        return {"busy": int(row[0]), "log_frames": int(row[1]), "checkpointed_frames": int(row[2])}
    except sqlite3.DatabaseError as exc:
        return {"error": str(exc)}


def _maybe_vacuum(store: Any, settings: Any, status: dict[str, Any]) -> bool:
    reclaim_mb = float(status["sqlite"]["reclaimable_mb"])
    threshold = float(getattr(settings, "vacuum_min_reclaim_mb", 64))
    page_count = int(status["sqlite"]["page_count"])
    freelist = int(status["sqlite"]["freelist_count"])
    if status["active_tasks"] or reclaim_mb < threshold or not page_count or freelist / page_count < 0.20:
        return False
    database_bytes = int(status["files"]["database"])
    free_bytes = int(float(status["filesystem"]["free_mb"]) * 1048576)
    minimum_free = int(float(getattr(settings, "min_free_disk_mb", 1024)) * 1048576)
    if free_bytes < database_bytes * 2 + minimum_free:
        return False
    with store.connection() as conn:
        conn.execute("VACUUM")
    return True


def _maintain_storage(store: Any, settings: Any) -> dict[str, Any]:
    if not _MAINTENANCE_LOCK.acquire(blocking=False):
        return {"skipped": "maintenance_already_running"}
    started_at = db_module.now_iso()
    try:
        before = store.storage_status()
        pressure = _pressure(before, settings)
        retention_days = _effective_days(getattr(settings, "retention_days", 180), pressure, soft=90, hard=30)
        payload_days = _effective_days(getattr(settings, "payload_retention_days", 30), pressure, soft=14, hard=7)
        metric_days = _effective_days(getattr(settings, "metric_retention_days", 60), pressure, soft=30, hard=14)
        snapshot_days = _effective_days(getattr(settings, "snapshot_retention_days", 45), pressure, soft=14, hard=7)
        batch_limit = 20000 if pressure == "hard" else 5000

        deleted = store.purge_old(retention_days)
        compacted: dict[str, int] = {}
        with store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                compacted["action_payloads"] = _compact_actions(conn, _cutoff(payload_days), batch_limit)
                compacted["legacy_report_transitions"] = _compact_legacy_report_transitions(
                    conn, _cutoff(payload_days), batch_limit,
                )
                compacted["metric_rows"] = conn.execute(
                    "DELETE FROM metric_rows WHERE cycle_id IN ("
                    "SELECT id FROM cycles WHERE completed_at IS NOT NULL AND completed_at<?)",
                    (_cutoff(metric_days),),
                ).rowcount
                terminal = tuple(TERMINAL_REPORT_STATES)
                placeholders = ",".join("?" for _ in terminal)
                compacted["report_snapshots"] = conn.execute(
                    f"UPDATE report_jobs SET normalized_snapshot_json=NULL,normalized_snapshot_gzip=NULL "
                    f"WHERE status IN ({placeholders}) AND updated_at<? "
                    "AND (normalized_snapshot_json IS NOT NULL OR normalized_snapshot_gzip IS NOT NULL)",
                    (*terminal, _cutoff(snapshot_days)),
                ).rowcount
                compacted["stopped_workers"] = conn.execute(
                    "DELETE FROM workers WHERE status<>'running' AND stopped_at IS NOT NULL AND stopped_at<? "
                    "AND (task_id IS NULL OR NOT EXISTS (SELECT 1 FROM tasks WHERE tasks.id=workers.task_id))",
                    (_cutoff(retention_days),),
                ).rowcount
                compacted["duplicate_open_alerts"] = conn.execute(
                    "DELETE FROM alerts WHERE status='open' AND created_at<? AND id NOT IN ("
                    "SELECT MAX(id) FROM alerts WHERE status='open' GROUP BY code,COALESCE(profile_id,''),"
                    "COALESCE(task_id,''),COALESCE(decision_id,''))",
                    (_cutoff(retention_days),),
                ).rowcount
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        checkpoint = _checkpoint(store)
        mid = store.storage_status()
        vacuumed = _maybe_vacuum(store, settings, mid)
        if vacuumed:
            checkpoint = _checkpoint(store)
        after = store.storage_status()
        remaining_pressure = _pressure(after, settings)

        if remaining_pressure == "hard":
            current = store.get_settings()
            if current.get("mode") != "paused" or current.get("execution_enabled"):
                store.update_settings({"mode": "paused", "execution_enabled": False})
            store.alert_once(
                "critical", "STORAGE_HARD_LIMIT", None, None, None,
                "Storage hard limit reached; autonomous writes were paused before disk exhaustion",
                {"storage": after}, window_seconds=86400,
            )
        elif remaining_pressure == "soft":
            store.alert_once(
                "warning", "STORAGE_SOFT_LIMIT", None, None, None,
                "Storage soft limit reached; aggressive retention and compaction were applied",
                {"storage": after}, window_seconds=86400,
            )

        result = {
            "started_at": started_at,
            "finished_at": db_module.now_iso(),
            "pressure_before": pressure,
            "pressure_after": remaining_pressure,
            "effective_retention_days": retention_days,
            "effective_payload_retention_days": payload_days,
            "effective_metric_retention_days": metric_days,
            "effective_snapshot_retention_days": snapshot_days,
            "deleted": deleted,
            "compacted": compacted,
            "checkpoint": checkpoint,
            "vacuumed": vacuumed,
            "before_total_mb": before["files"]["total_mb"],
            "after_total_mb": after["files"]["total_mb"],
            "free_disk_mb": after["filesystem"]["free_mb"],
        }
        with store.connection() as conn:
            conn.execute(
                "INSERT INTO maintenance_runs(started_at,finished_at,pressure,result_json) VALUES(?,?,?,?)",
                (started_at, result["finished_at"], remaining_pressure,
                 json.dumps(redact(result), ensure_ascii=False, sort_keys=True, default=str)),
            )
            conn.execute(
                "DELETE FROM maintenance_runs WHERE id NOT IN (SELECT id FROM maintenance_runs ORDER BY id DESC LIMIT 100)"
            )
        return result
    finally:
        _MAINTENANCE_LOCK.release()


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    db_module.DEFAULT_SETTINGS.setdefault("max_action_payload_bytes", 262144)
    db_module.INTEGER_SETTING_RANGES.setdefault("max_action_payload_bytes", (16384, 1048576))

    Store = db_module.Store
    original_init = Store.__init__
    original_record_action = Store.record_action
    original_dashboard = Store.dashboard

    def init(self, path):
        original_init(self, path)
        _ensure_schema(self)

    def record_action(self, **kwargs):
        max_bytes = int(self.get_settings().get("max_action_payload_bytes", 262144))
        safe_args, args_hash, args_compacted, args_bytes = _compact_envelope(kwargs.get("args", {}), max_bytes)
        safe_result, result_hash, result_compacted, result_bytes = _compact_envelope(kwargs.get("result"), max_bytes)
        prepared = dict(kwargs)
        prepared["args"] = safe_args if isinstance(safe_args, dict) else {"_compacted": True, "value": safe_args}
        prepared["result"] = safe_result
        if args_compacted or result_compacted:
            summary = str(prepared.get("result_summary") or "")
            detail = f"payload bounded (args={args_bytes}B,result={result_bytes}B)"
            prepared["result_summary"] = redact_text((summary + " " + detail).strip())[:4000]
        action_id = original_record_action(self, **prepared)
        with self.connection() as conn:
            conn.execute(
                "UPDATE actions SET args_hash=?,stored_result_hash=?,compacted_at=? WHERE id=?",
                (args_hash, result_hash, db_module.now_iso() if (args_compacted or result_compacted) else None, action_id),
            )
        return action_id

    def dashboard(self):
        result = original_dashboard(self)
        result["storage"] = self.storage_status()
        return result

    Store.__init__ = init
    Store.record_action = record_action
    Store.storage_status = _storage_status
    Store.maintain_storage = _maintain_storage
    Store.dashboard = dashboard
    _INSTALLED = True
