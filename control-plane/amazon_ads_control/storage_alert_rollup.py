from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any

from . import db as db_module
from .policy import redact_text

UTC = timezone.utc
_INSTALLED = False


def _ensure_schema(store: Any) -> None:
    with store.connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS alert_rollups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bucket_start TEXT NOT NULL,
                severity TEXT NOT NULL,
                code TEXT NOT NULL,
                profile_id TEXT NOT NULL DEFAULT '',
                alert_count INTEGER NOT NULL,
                first_at TEXT NOT NULL,
                last_at TEXT NOT NULL,
                sample_message TEXT,
                last_data_hash TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(bucket_start,severity,code,profile_id)
            );
            CREATE INDEX IF NOT EXISTS idx_alert_rollups_last
                ON alert_rollups(last_at DESC);
            """
        )


def _month_bucket(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        parsed = datetime.now(UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0,
    ).isoformat(timespec="seconds")


def _archive_orphan_alerts(store: Any, retention_days: int, limit: int = 5000) -> int:
    cutoff = (datetime.now(UTC) - timedelta(days=max(1, int(retention_days)))).isoformat(timespec="seconds")
    with store.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            rows = conn.execute(
                "SELECT a.* FROM alerts a "
                "LEFT JOIN tasks t ON t.id=a.task_id "
                "LEFT JOIN decisions d ON d.id=a.decision_id "
                "WHERE a.status='open' AND a.created_at<? "
                "AND (a.task_id IS NOT NULL OR a.decision_id IS NOT NULL) "
                "AND (a.task_id IS NULL OR t.id IS NULL) "
                "AND (a.decision_id IS NULL OR d.id IS NULL) "
                "ORDER BY a.id LIMIT ?",
                (cutoff, max(1, int(limit))),
            ).fetchall()
            now = db_module.now_iso()
            for row in rows:
                data_hash = hashlib.sha256(str(row["data_json"] or "{}").encode("utf-8")).hexdigest()
                conn.execute(
                    "INSERT INTO alert_rollups(bucket_start,severity,code,profile_id,alert_count,first_at,last_at,"
                    "sample_message,last_data_hash,updated_at) VALUES(?,?,?,?,1,?,?,?,?,?) "
                    "ON CONFLICT(bucket_start,severity,code,profile_id) DO UPDATE SET "
                    "alert_count=alert_count+1,first_at=MIN(first_at,excluded.first_at),"
                    "last_at=MAX(last_at,excluded.last_at),sample_message=excluded.sample_message,"
                    "last_data_hash=excluded.last_data_hash,updated_at=excluded.updated_at",
                    (
                        _month_bucket(row["created_at"]),
                        str(row["severity"] or "unknown")[:20],
                        str(row["code"] or "UNKNOWN")[:120],
                        str(row["profile_id"] or ""),
                        str(row["created_at"]),
                        str(row["created_at"]),
                        redact_text(str(row["message"] or ""))[:1000] or None,
                        data_hash,
                        now,
                    ),
                )
            if rows:
                placeholders = ",".join("?" for _ in rows)
                conn.execute(
                    f"DELETE FROM alerts WHERE id IN ({placeholders})",
                    [row["id"] for row in rows],
                )
            conn.execute(
                "DELETE FROM alert_rollups WHERE id NOT IN ("
                "SELECT id FROM alert_rollups ORDER BY last_at DESC,id DESC LIMIT 5000)"
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return len(rows)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    Store = db_module.Store
    original_init = Store.__init__
    original_maintain = Store.maintain_storage
    original_storage_status = Store.storage_status
    original_dashboard = Store.dashboard

    def init(self, path):
        original_init(self, path)
        _ensure_schema(self)

    def maintain_storage(self, settings):
        result = original_maintain(self, settings)
        if result.get("skipped"):
            return result
        retention = int(result.get("effective_retention_days") or getattr(settings, "retention_days", 180))
        result["archived_orphan_alerts"] = _archive_orphan_alerts(self, retention)
        return result

    def storage_status(self):
        result = original_storage_status(self)
        with self.connection() as conn:
            result["row_counts"]["alert_rollups"] = int(
                conn.execute("SELECT COUNT(*) FROM alert_rollups").fetchone()[0]
            )
        return result

    def dashboard(self):
        result = original_dashboard(self)
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT bucket_start,severity,code,profile_id,alert_count,first_at,last_at,sample_message "
                "FROM alert_rollups ORDER BY last_at DESC LIMIT 30"
            ).fetchall()
        result["alert_rollups"] = [dict(row) for row in rows]
        return result

    Store.__init__ = init
    Store.maintain_storage = maintain_storage
    Store.storage_status = storage_status
    Store.dashboard = dashboard
    _INSTALLED = True
