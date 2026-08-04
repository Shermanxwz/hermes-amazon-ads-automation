from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import secrets
from typing import Any

from . import catalog as catalog_module
from . import db as db_module
from . import service as service_module
from .evidence import canonical_hash, expected_differences, select_entity_object, verify_before_state
from .outcome import parse_tool_outcome
from .reporting import (
    TERMINAL_REPORT_STATES,
    lineage_payload,
    normalize_report_spec,
    report_key,
    validate_ingested_payload,
    validate_transition,
)

UTC = timezone.utc
_INSTALLED = False


def _action_family(action_type: str, payload: dict[str, Any]) -> str:
    action = str(action_type or "").lower()
    field = str(payload.get("field") or "").lower()
    if action in {"increase_budget", "decrease_budget"} or field == "budget":
        return "budget"
    if action == "update_placement" or field in {"percentage", "adjustment_percent"}:
        return "placement"
    if action in {"update_bid"} or field == "bid":
        return "bid"
    if "negative" in action:
        return "negative"
    if "harvest" in action or action.startswith("create_"):
        return "create"
    if "state" in action or action in {"pause", "resume", "enable", "disable"}:
        return "state"
    return action or "other"


def _entity_key(profile_id: str, entity_type: str, entity_id: str, payload: dict[str, Any]) -> str:
    placement = str(payload.get("placement") or "").upper()
    scope = str(payload.get("scope_key") or payload.get("campaign_id") or "")
    raw = "|".join((str(profile_id), str(entity_type), str(entity_id), placement, scope))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _configure_settings() -> None:
    db_module.SAFETY_LOCKED_SETTINGS.update({
        "require_snapshot_lineage": True,
        "require_prewrite_read": True,
        "require_entity_local_verification": True,
        "server_authoritative_catalog": True,
    })
    db_module.DEFAULT_SETTINGS.update({
        "require_snapshot_lineage": True,
        "require_prewrite_read": True,
        "require_entity_local_verification": True,
        "server_authoritative_catalog": True,
        "prewrite_read_max_age_seconds": 300,
        "max_profile_daily_budget_increase_pct": 20,
        "max_profile_daily_budget_increase_amount": 0,
        "budget_decrease_pct": 12,
        "placement_increase_points": 10,
        "placement_decrease_points": 10,
        "allow_budget_decreases": True,
        "allow_state_changes": False,
        "cooldown_hours": 24,
        "learning_min_clicks": 12,
        "stable_min_orders": 4,
        "scale_min_orders": 5,
        "min_confidence_to_reduce": 0.55,
        "min_confidence_to_scale": 0.70,
        "max_decisions_per_cycle": 25,
        "min_bid": 0.02,
        "max_bid": 1000,
        "min_budget": 1,
        "max_budget": 1000000,
        "auto_write_ad_products": ["SPONSORED_PRODUCTS", "SPONSORED_BRANDS", "SPONSORED_DISPLAY"],
    })
    db_module.BOOLEAN_SETTINGS.update({
        "require_snapshot_lineage", "require_prewrite_read", "require_entity_local_verification",
        "server_authoritative_catalog", "allow_budget_decreases", "allow_state_changes",
    })
    db_module.INTEGER_SETTING_RANGES.update({
        "prewrite_read_max_age_seconds": (30, 3600),
        "cooldown_hours": (0, 8760),
        "learning_min_clicks": (1, 1000000),
        "stable_min_orders": (1, 1000000),
        "scale_min_orders": (1, 1000000),
        "max_decisions_per_cycle": (1, 10000),
    })
    db_module.NUMERIC_SETTING_RANGES.update({
        "max_profile_daily_budget_increase_pct": (0.01, 100.0),
        "max_profile_daily_budget_increase_amount": (0.0, 1000000000.0),
        "budget_decrease_pct": (0.0, 100.0),
        "placement_increase_points": (0.0, 900.0),
        "placement_decrease_points": (0.0, 900.0),
        "min_confidence_to_reduce": (0.0, 1.0),
        "min_confidence_to_scale": (0.0, 1.0),
        "min_bid": (0.01, 1000000.0),
        "max_bid": (0.02, 1000000000.0),
        "min_budget": (0.01, 1000000000.0),
        "max_budget": (1.0, 1000000000000.0),
    })
    db_module.STRATEGY_SETTING_KEYS.update({
        "budget_decrease_pct", "placement_increase_points", "placement_decrease_points",
        "allow_budget_decreases", "allow_state_changes", "cooldown_hours", "learning_min_clicks",
        "stable_min_orders", "scale_min_orders", "min_confidence_to_reduce", "min_confidence_to_scale",
        "max_decisions_per_cycle", "min_bid", "max_bid", "min_budget", "max_budget",
        "auto_write_ad_products",
    })


def _ensure_schema(store) -> None:
    with store.connection() as conn:
        for table, column, definition in (
            ("cycles", "lineage_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("decisions", "entity_key", "TEXT"),
            ("decisions", "action_family", "TEXT"),
            ("decisions", "precondition_action_id", "INTEGER"),
            ("decisions", "precondition_hash", "TEXT"),
            ("decisions", "result_event_id", "TEXT"),
            ("decisions", "result_hash", "TEXT"),
        ):
            db_module.Store._ensure_column(conn, table, column, definition)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS report_jobs (
                id TEXT PRIMARY KEY,
                report_key TEXT NOT NULL UNIQUE,
                profile_id TEXT NOT NULL,
                report_type TEXT NOT NULL,
                ad_product TEXT,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                timezone TEXT NOT NULL,
                status TEXT NOT NULL,
                request_json TEXT NOT NULL,
                report_id TEXT UNIQUE,
                content_hash TEXT,
                normalized_hash TEXT,
                schema_hash TEXT,
                row_count INTEGER,
                attempt_count INTEGER NOT NULL DEFAULT 1,
                poll_count INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                submitted_at TEXT,
                completed_at TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_report_jobs_profile_status ON report_jobs(profile_id,status,updated_at DESC);
            CREATE TABLE IF NOT EXISTS report_transitions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_job_id TEXT NOT NULL,
                from_status TEXT,
                to_status TEXT NOT NULL,
                data_json TEXT NOT NULL,
                actor TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(report_job_id) REFERENCES report_jobs(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_report_transitions_job ON report_transitions(report_job_id,id);
            CREATE TABLE IF NOT EXISTS snapshot_lineage (
                cycle_id TEXT PRIMARY KEY,
                normalized_hash TEXT NOT NULL,
                report_job_ids_json TEXT NOT NULL,
                action_ids_json TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(cycle_id) REFERENCES cycles(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS callback_events (
                event_id TEXT PRIMARY KEY,
                decision_id TEXT NOT NULL,
                reservation_token_hash TEXT NOT NULL,
                outcome TEXT NOT NULL,
                result_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(decision_id) REFERENCES decisions(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_callback_decision ON callback_events(decision_id,created_at DESC);
            CREATE TABLE IF NOT EXISTS runtime_status (
                component TEXT PRIMARY KEY,
                state_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_decisions_entity_cooldown ON decisions(profile_id,entity_key,action_family,reserved_at DESC);
            """
        )
        rows = conn.execute(
            "SELECT id,profile_id,entity_type,entity_id,action_type,payload_json FROM decisions "
            "WHERE entity_key IS NULL OR action_family IS NULL"
        ).fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"] or "{}")
            conn.execute(
                "UPDATE decisions SET entity_key=?,action_family=? WHERE id=?",
                (_entity_key(row["profile_id"], row["entity_type"], row["entity_id"], payload),
                 _action_family(row["action_type"], payload), row["id"]),
            )
        for key, value in db_module.DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key,value,updated_at) VALUES(?,?,?)",
                (key, json.dumps(value), db_module.now_iso()),
            )


def _install_store() -> None:
    Store = db_module.Store
    original_init = Store.__init__
    original_create_cycle = Store.create_cycle
    original_record_verification = Store.record_verification
    original_dashboard = Store.dashboard
    original_purge_old = Store.purge_old

    def init(self, path):
        original_init(self, path)
        _ensure_schema(self)

    def create_cycle(self, **kwargs):
        result = original_create_cycle(self, **kwargs)
        cycle_id = result.get("id")
        if cycle_id:
            with self.connection() as conn:
                rows = conn.execute(
                    "SELECT id,profile_id,entity_type,entity_id,action_type,payload_json FROM decisions WHERE cycle_id=?",
                    (cycle_id,),
                ).fetchall()
                for row in rows:
                    payload = json.loads(row["payload_json"] or "{}")
                    conn.execute(
                        "UPDATE decisions SET entity_key=?,action_family=? WHERE id=?",
                        (_entity_key(row["profile_id"], row["entity_type"], row["entity_id"], payload),
                         _action_family(row["action_type"], payload), row["id"]),
                    )
        return self.get_cycle(cycle_id) if cycle_id else result

    def create_report_job(self, spec: dict[str, Any], actor: str = "hermes-main") -> dict[str, Any]:
        normalized = normalize_report_spec(spec)
        key = report_key(normalized)
        now = db_module.now_iso()
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM report_jobs WHERE report_key=?", (key,)).fetchone()
            if row:
                return _report_dict(row)
            job_id = secrets.token_hex(10)
            conn.execute(
                "INSERT INTO report_jobs(id,report_key,profile_id,report_type,ad_product,start_date,end_date,timezone,status,request_json,created_by,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?, 'REQUESTED',?,?,?,?,?)",
                (job_id, key, normalized["profile_id"], normalized["report_type"], normalized["ad_product"],
                 normalized["start_date"], normalized["end_date"], normalized["timezone"], _json(normalized),
                 actor[:80], now, now),
            )
            conn.execute(
                "INSERT INTO report_transitions(report_job_id,from_status,to_status,data_json,actor,created_at) VALUES(?,NULL,'REQUESTED','{}',?,?)",
                (job_id, actor[:80], now),
            )
        return self.get_report_job(job_id)

    def get_report_job(self, identifier: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM report_jobs WHERE id=? OR report_key=? OR report_id=?",
                (identifier, identifier, identifier),
            ).fetchone()
        return _report_dict(row) if row else None

    def list_report_jobs(self, limit: int = 100, profile_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        clauses, params = [], []
        if profile_id:
            clauses.append("profile_id=?"); params.append(profile_id)
        if status:
            clauses.append("status=?"); params.append(status.upper())
        sql = "SELECT * FROM report_jobs" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY updated_at DESC LIMIT ?"
        params.append(min(1000, max(1, int(limit))))
        with self.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_report_dict(row) for row in rows]

    def transition_report(self, identifier: str, new_status: str, data: dict[str, Any], actor: str = "hermes-main") -> dict[str, Any]:
        new_status = str(new_status).upper()
        data = data if isinstance(data, dict) else {}
        now = db_module.now_iso()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM report_jobs WHERE id=? OR report_key=? OR report_id=?",
                    (identifier, identifier, identifier),
                ).fetchone()
                if not row:
                    raise KeyError("report job not found")
                validate_transition(row["status"], new_status)
                if new_status == "INGESTED":
                    validate_ingested_payload(data)
                report_id = str(data.get("report_id") or row["report_id"] or "") or None
                if new_status in {"SUBMITTED", "IN_PROGRESS", "SUCCEEDED"} and not report_id:
                    raise ValueError(f"{new_status} report requires report_id")
                submitted_at = row["submitted_at"] or (now if new_status == "SUBMITTED" else None)
                completed_at = now if new_status in TERMINAL_REPORT_STATES else row["completed_at"]
                poll_count = int(row["poll_count"] or 0) + int(new_status in {"IN_PROGRESS", "SUCCEEDED"})
                conn.execute(
                    "UPDATE report_jobs SET status=?,report_id=?,content_hash=COALESCE(?,content_hash),normalized_hash=COALESCE(?,normalized_hash),"
                    "schema_hash=COALESCE(?,schema_hash),row_count=COALESCE(?,row_count),poll_count=?,error=?,submitted_at=?,completed_at=?,updated_at=? WHERE id=?",
                    (new_status, report_id, data.get("content_hash"), data.get("normalized_hash"), data.get("schema_hash"),
                     data.get("row_count"), poll_count, str(data.get("error") or "")[:2000] or None,
                     submitted_at, completed_at, now, row["id"]),
                )
                conn.execute(
                    "INSERT INTO report_transitions(report_job_id,from_status,to_status,data_json,actor,created_at) VALUES(?,?,?,?,?,?)",
                    (row["id"], row["status"], new_status, _json(data), actor[:80], now),
                )
                conn.commit()
            except Exception:
                conn.rollback(); raise
        return self.get_report_job(str(row["id"])) or {}

    def validate_snapshot_lineage(self, snapshot: dict[str, Any], lineage: dict[str, Any]) -> dict[str, Any]:
        normalized = lineage_payload(snapshot, lineage)
        profile = snapshot.get("profile") if isinstance(snapshot.get("profile"), dict) else {}
        profile_id = str(profile.get("profile_id") or profile.get("id") or "")
        window = snapshot.get("window") if isinstance(snapshot.get("window"), dict) else {}
        with self.connection() as conn:
            for job_id in normalized["report_job_ids"]:
                row = conn.execute("SELECT * FROM report_jobs WHERE id=?", (job_id,)).fetchone()
                if not row or row["status"] != "INGESTED":
                    raise ValueError(f"snapshot lineage report {job_id} is not INGESTED")
                if row["profile_id"] != profile_id:
                    raise ValueError("snapshot lineage profile does not match report profile")
                if window.get("start") and row["start_date"] > str(window["start"]):
                    raise ValueError("snapshot window begins before its report lineage")
                if window.get("end") and row["end_date"] < str(window["end"]):
                    raise ValueError("snapshot window ends after its report lineage")
            for action_id in normalized["action_ids"]:
                row = conn.execute(
                    "SELECT allowed,phase,operation,structured_result FROM actions WHERE id=?", (action_id,)
                ).fetchone()
                if not row or not row["allowed"] or row["phase"] != "after" or row["operation"] not in {"read", "job"}:
                    raise ValueError(f"snapshot lineage action {action_id} is not trusted read/job evidence")
        return normalized

    def attach_cycle_lineage(self, cycle_id: str, lineage: dict[str, Any]) -> None:
        with self.connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO snapshot_lineage(cycle_id,normalized_hash,report_job_ids_json,action_ids_json,source,created_at) VALUES(?,?,?,?,?,?)",
                (cycle_id, lineage["normalized_hash"], _json(lineage["report_job_ids"]), _json(lineage["action_ids"]),
                 lineage["source"], db_module.now_iso()),
            )
            conn.execute("UPDATE cycles SET lineage_json=? WHERE id=?", (_json(lineage), cycle_id))

    def bind_precondition(self, decision_id: str, action_id: int, entity_hash: str) -> dict[str, Any]:
        with self.connection() as conn:
            cursor = conn.execute(
                "UPDATE decisions SET precondition_action_id=?,precondition_hash=? WHERE id=? AND status='planned'",
                (int(action_id), entity_hash, decision_id),
            )
        if cursor.rowcount != 1:
            raise ValueError("decision is not eligible for a fresh precondition")
        return self.get_decision(decision_id) or {}

    def reserve_decision(self, decision_id: str, task_id: str, session_id: str, ttl_seconds: int,
                         cooldown_seconds: int = 86400, *, max_actions_per_task: int = 50,
                         max_actions_per_day: int = 250, max_campaign_creates_per_day: int = 2):
        self.reconcile_expired_reservations()
        now = db_module.now_iso()
        expires = db_module.future_iso(ttl_seconds)
        token = secrets.token_urlsafe(24)
        cutoff = (datetime.now(UTC) - timedelta(seconds=max(0, cooldown_seconds))).isoformat(timespec="seconds")
        day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
        settings = self.get_settings()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute("SELECT * FROM decisions WHERE id=? AND task_id=?", (decision_id, task_id)).fetchone()
                if not row:
                    raise KeyError("decision not found for task")
                if row["status"] != "planned":
                    raise ValueError(f"decision is not reservable from status {row['status']}")
                if conn.execute("SELECT COUNT(*) FROM decisions WHERE task_id=? AND reserved_at IS NOT NULL", (task_id,)).fetchone()[0] >= max_actions_per_task:
                    raise ValueError("task write limit reached")
                if conn.execute("SELECT COUNT(*) FROM decisions WHERE reserved_at>=?", (day_start,)).fetchone()[0] >= max_actions_per_day:
                    raise ValueError("daily write limit reached")
                if row["action_type"] == "create_campaign" and conn.execute(
                    "SELECT COUNT(*) FROM decisions WHERE action_type='create_campaign' AND reserved_at>=?", (day_start,)
                ).fetchone()[0] >= max_campaign_creates_per_day:
                    raise ValueError("daily campaign creation limit reached")
                family = row["action_family"] or "other"
                duplicate = conn.execute(
                    "SELECT id,status FROM decisions WHERE id<>? AND profile_id=? AND entity_key=? AND action_family=? "
                    "AND COALESCE(reserved_at,created_at)>=? AND status IN ('reserved','executed','pending','uncertain','verified','mismatch') "
                    "ORDER BY COALESCE(reserved_at,created_at) DESC LIMIT 1",
                    (decision_id, row["profile_id"], row["entity_key"], family, cutoff),
                ).fetchone()
                if duplicate:
                    raise ValueError(f"entity action family is inside cooldown ({duplicate['status']})")
                if settings.get("require_prewrite_read", True) and family in {"bid", "budget", "placement", "state"}:
                    if not row["precondition_action_id"] or not row["precondition_hash"]:
                        raise ValueError("fresh executor read and compare-and-set preparation are required")
                    action = conn.execute("SELECT * FROM actions WHERE id=?", (row["precondition_action_id"],)).fetchone()
                    if not action or action["session_id"] != session_id or action["task_id"] != task_id or action["phase"] != "after" or action["operation"] != "read" or not action["allowed"] or not action["structured_result"]:
                        raise ValueError("write precondition is not a trusted read by the current executor")
                    action_at = datetime.fromisoformat(action["created_at"])
                    if action_at.tzinfo is None:
                        action_at = action_at.replace(tzinfo=UTC)
                    if (datetime.now(UTC) - action_at.astimezone(UTC)).total_seconds() > int(settings.get("prewrite_read_max_age_seconds", 300)):
                        raise ValueError("write precondition read is too old")
                if family == "budget":
                    payload = json.loads(row["payload_json"] or "{}")
                    before, after = payload.get("before"), payload.get("after")
                    if isinstance(before, (int, float)) and isinstance(after, (int, float)) and after > before:
                        current_delta = float(after) - float(before)
                        reserved = conn.execute(
                            "SELECT payload_json FROM decisions WHERE profile_id=? AND action_family='budget' AND reserved_at>=? "
                            "AND status IN ('reserved','executed','pending','uncertain','verified')",
                            (row["profile_id"], day_start),
                        ).fetchall()
                        used = 0.0
                        for prior in reserved:
                            p = json.loads(prior["payload_json"] or "{}")
                            if isinstance(p.get("before"), (int, float)) and isinstance(p.get("after"), (int, float)):
                                used += max(0.0, float(p["after"]) - float(p["before"]))
                        cycle_rows = conn.execute("SELECT payload_json FROM decisions WHERE cycle_id=? AND action_family='budget'", (row["cycle_id"],)).fetchall()
                        observed = sum(float((json.loads(item["payload_json"] or "{}")).get("before") or 0) for item in cycle_rows)
                        pct_limit = float(settings.get("max_profile_daily_budget_increase_pct", 20))
                        amount_limit = float(settings.get("max_profile_daily_budget_increase_amount", 0))
                        if observed > 0 and used + current_delta > observed * pct_limit / 100 + 1e-9:
                            raise ValueError("profile daily cumulative budget increase percentage limit reached")
                        if amount_limit > 0 and used + current_delta > amount_limit + 1e-9:
                            raise ValueError("profile daily cumulative budget increase amount limit reached")
                updated = conn.execute(
                    "UPDATE decisions SET status='reserved',reserved_by=?,reservation_token=?,reservation_expires_at=?,reserved_at=? WHERE id=? AND status='planned'",
                    (session_id, token, expires, now, decision_id),
                ).rowcount
                if updated != 1:
                    raise ValueError("decision reservation race lost")
                conn.commit()
            except Exception:
                conn.rollback(); raise
        decision = self.get_decision(decision_id) or {}
        decision["reservation_token"] = token
        return decision

    def record_callback(self, event_id: str, decision_id: str, token: str, outcome: str, result_hash: str) -> bool:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM callback_events WHERE event_id=?", (event_id,)).fetchone()
            if row:
                if (row["decision_id"] != decision_id or row["reservation_token_hash"] != token_hash
                        or row["outcome"] != outcome or row["result_hash"] != result_hash):
                    raise ValueError("callback event_id conflicts with a previously recorded result")
                return False
            decision = conn.execute("SELECT result_event_id,result_hash,reservation_token FROM decisions WHERE id=?", (decision_id,)).fetchone()
            if not decision:
                raise KeyError("decision not found")
            if not hmac.compare_digest(str(decision["reservation_token"] or ""), token):
                raise ValueError("reservation token mismatch")
            if decision["result_event_id"]:
                if decision["result_event_id"] == event_id and decision["result_hash"] == result_hash:
                    return False
                raise ValueError("decision already has a different result event")
            conn.execute(
                "INSERT INTO callback_events(event_id,decision_id,reservation_token_hash,outcome,result_hash,created_at) VALUES(?,?,?,?,?,?)",
                (event_id, decision_id, token_hash, outcome, result_hash, db_module.now_iso()),
            )
        return True

    def finalize_callback(self, event_id: str, decision_id: str, result_hash: str) -> None:
        with self.connection() as conn:
            conn.execute("UPDATE decisions SET result_event_id=?,result_hash=? WHERE id=?", (event_id, result_hash, decision_id))

    def record_verification(self, **kwargs):
        result = original_record_verification(self, **kwargs)
        decision_id = kwargs.get("decision_id")
        if decision_id:
            with self.connection() as conn:
                row = conn.execute("SELECT entity_key,action_family FROM decisions WHERE id=?", (decision_id,)).fetchone()
                if row:
                    conn.execute(
                        "UPDATE decisions SET entity_key=COALESCE(entity_key,?),action_family=COALESCE(action_family,?) WHERE id=?",
                        (row["entity_key"], row["action_family"], decision_id),
                    )
        return result

    def record_runtime_status(self, component: str, state: dict[str, Any]) -> dict[str, Any]:
        now = db_module.now_iso()
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO runtime_status(component,state_json,updated_at) VALUES(?,?,?) ON CONFLICT(component) DO UPDATE SET state_json=excluded.state_json,updated_at=excluded.updated_at",
                (component[:80], _json(state), now),
            )
        return {"component": component, "state": state, "updated_at": now}

    def runtime_status(self) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute("SELECT * FROM runtime_status ORDER BY component").fetchall()
        return [{"component": row["component"], "state": json.loads(row["state_json"]), "updated_at": row["updated_at"]} for row in rows]

    def dashboard(self):
        result = original_dashboard(self)
        result["reports"] = {
            "recent": self.list_report_jobs(30),
            "counts": _report_counts(self),
        }
        result["runtime_status"] = self.runtime_status()
        with self.connection() as conn:
            result["lineage_cycles"] = int(conn.execute("SELECT COUNT(*) FROM snapshot_lineage").fetchone()[0])
            result["pending_callbacks"] = int(conn.execute(
                "SELECT COUNT(*) FROM decisions WHERE result_event_id IS NULL AND status IN ('executed','pending','uncertain')"
            ).fetchone()[0])
        return result

    def purge_old(self, retention_days: int):
        result = original_purge_old(self, retention_days)
        cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                old_tasks = [row[0] for row in conn.execute(
                    "SELECT id FROM tasks WHERE completed_at IS NOT NULL AND completed_at<?", (cutoff,)
                ).fetchall()]
                if old_tasks:
                    placeholders = ",".join("?" for _ in old_tasks)
                    conn.execute(f"DELETE FROM verifications WHERE task_id IN ({placeholders})", old_tasks)
                    conn.execute(f"DELETE FROM actions WHERE task_id IN ({placeholders})", old_tasks)
                    conn.execute(f"DELETE FROM workers WHERE task_id IN ({placeholders})", old_tasks)
                    conn.execute(f"DELETE FROM decisions WHERE task_id IN ({placeholders})", old_tasks)
                    deleted = conn.execute(f"DELETE FROM tasks WHERE id IN ({placeholders})", old_tasks).rowcount
                    result["tasks"] = deleted
                old_cycles = [row[0] for row in conn.execute(
                    "SELECT c.id FROM cycles c LEFT JOIN tasks t ON t.cycle_id=c.id WHERE c.completed_at IS NOT NULL AND c.completed_at<? GROUP BY c.id HAVING COUNT(t.id)=0",
                    (cutoff,),
                ).fetchall()]
                if old_cycles:
                    placeholders = ",".join("?" for _ in old_cycles)
                    conn.execute(f"DELETE FROM metric_rows WHERE cycle_id IN ({placeholders})", old_cycles)
                    conn.execute(f"DELETE FROM snapshot_lineage WHERE cycle_id IN ({placeholders})", old_cycles)
                    conn.execute(f"DELETE FROM decisions WHERE cycle_id IN ({placeholders})", old_cycles)
                    result["cycles"] = conn.execute(f"DELETE FROM cycles WHERE id IN ({placeholders})", old_cycles).rowcount
                terminal = tuple(TERMINAL_REPORT_STATES)
                placeholders = ",".join("?" for _ in terminal)
                old_reports = [row[0] for row in conn.execute(
                    f"SELECT id FROM report_jobs WHERE status IN ({placeholders}) AND updated_at<?", (*terminal, cutoff)
                ).fetchall()]
                if old_reports:
                    rp = ",".join("?" for _ in old_reports)
                    conn.execute(f"DELETE FROM report_transitions WHERE report_job_id IN ({rp})", old_reports)
                    result["report_jobs"] = conn.execute(f"DELETE FROM report_jobs WHERE id IN ({rp})", old_reports).rowcount
                result["alerts"] = conn.execute("DELETE FROM alerts WHERE status<>'open' AND COALESCE(resolved_at,created_at)<?", (cutoff,)).rowcount
                conn.commit()
            except Exception:
                conn.rollback(); raise
            conn.execute("PRAGMA optimize")
        return result

    Store.__init__ = init
    Store.create_cycle = create_cycle
    Store.create_report_job = create_report_job
    Store.get_report_job = get_report_job
    Store.list_report_jobs = list_report_jobs
    Store.transition_report = transition_report
    Store.validate_snapshot_lineage = validate_snapshot_lineage
    Store.attach_cycle_lineage = attach_cycle_lineage
    Store.bind_precondition = bind_precondition
    Store.reserve_decision = reserve_decision
    Store.record_callback = record_callback
    Store.finalize_callback = finalize_callback
    Store.record_verification = record_verification
    Store.record_runtime_status = record_runtime_status
    Store.runtime_status = runtime_status
    Store.dashboard = dashboard
    Store.purge_old = purge_old


def _report_dict(row) -> dict[str, Any]:
    item = dict(row)
    item["request"] = json.loads(item.pop("request_json"))
    return item


def _report_counts(store) -> dict[str, int]:
    with store.connection() as conn:
        rows = conn.execute("SELECT status,COUNT(*) count FROM report_jobs GROUP BY status").fetchall()
    return {row["status"]: int(row["count"]) for row in rows}


def _install_service() -> None:
    Service = service_module.ControlService
    original_plan = Service.plan_cycle
    original_finish = Service.finish_tool
    original_context = Service.context

    def sync_catalog(self, payload: dict[str, Any]):
        raw_tools = payload.get("tools")
        if not isinstance(raw_tools, list) or not raw_tools:
            raise ValueError("tools must be a non-empty array")
        tools = []
        for raw in raw_tools:
            if not isinstance(raw, dict):
                raise ValueError("each catalog tool must be an object")
            sanitized = {
                "registered_name": raw.get("registered_name") or raw.get("name"),
                "native_name": raw.get("native_name"),
                "schema": raw.get("schema") if isinstance(raw.get("schema"), dict) else {},
                "enabled": bool(raw.get("enabled", True)),
            }
            descriptor = catalog_module.descriptor_from_payload(sanitized)
            if not catalog_module.is_registered_amazon_tool(descriptor.registered_name):
                raise ValueError(f"tool is outside mcp-amazon-ads: {descriptor.registered_name}")
            tools.append(descriptor)
        return self.store.sync_catalog(tools)

    def plan_cycle(self, payload: dict[str, Any], actor: str):
        snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else payload
        settings = self.store.get_settings()
        lineage = payload.get("lineage") if isinstance(payload.get("lineage"), dict) else snapshot.get("lineage")
        validated = None
        if settings.get("require_snapshot_lineage", True):
            validated = self.store.validate_snapshot_lineage(snapshot, lineage)
        result = original_plan(self, payload, actor)
        if validated and result.get("id"):
            self.store.attach_cycle_lineage(result["id"], validated)
            result = self.store.get_cycle(result["id"]) or result
        return result

    def create_report(self, payload: dict[str, Any]):
        spec = payload.get("spec") if isinstance(payload.get("spec"), dict) else payload
        return self.store.create_report_job(spec, str(payload.get("actor") or "hermes-main"))

    def transition_report(self, payload: dict[str, Any]):
        identifier = str(payload.get("report_job_id") or payload.get("report_key") or payload.get("report_id") or "")
        if not identifier:
            raise ValueError("report_job_id, report_key or report_id is required")
        return self.store.transition_report(
            identifier,
            str(payload.get("status") or ""),
            payload.get("data") if isinstance(payload.get("data"), dict) else payload,
            str(payload.get("actor") or "hermes-main"),
        )

    def prepare_write(self, payload: dict[str, Any]):
        decision_id = str(payload.get("decision_id") or "").strip()
        session_id = str(payload.get("session_id") or "").strip()
        try:
            action_id = int(payload.get("evidence_action_id"))
        except (TypeError, ValueError) as exc:
            raise ValueError("evidence_action_id is required") from exc
        worker = self.store.worker_for_session(session_id)
        if not worker or worker.get("role") != "executor":
            raise ValueError("only the bound executor may prepare a write")
        decision = self.store.get_decision(decision_id)
        if not decision or decision.get("task_id") != worker.get("task_id"):
            raise ValueError("decision does not belong to executor task")
        action = self.store.get_action(action_id)
        if not action or action.get("session_id") != session_id or action.get("task_id") != worker.get("task_id"):
            raise ValueError("precondition read must belong to the current executor and task")
        if action.get("phase") != "after" or action.get("operation") != "read" or not action.get("allowed") or action.get("structured_result") is not True:
            raise ValueError("precondition must be a successful structured cataloged read")
        tool = self.store.get_tool(str(action.get("tool_name") or ""))
        if not tool or not service_module._family_matches(decision, str(tool.get("family") or "")):
            raise ValueError("precondition read tool family does not match the decision")
        read_at = datetime.fromisoformat(str(action.get("created_at") or ""))
        if read_at.tzinfo is None:
            read_at = read_at.replace(tzinfo=UTC)
        max_age = int(self.store.get_settings().get("prewrite_read_max_age_seconds", 300))
        if (datetime.now(UTC) - read_at.astimezone(UTC)).total_seconds() > max_age:
            raise ValueError("precondition read is too old")
        entity, entity_hash = verify_before_state(action.get("result"), decision)
        result = self.store.bind_precondition(decision_id, action_id, entity_hash)
        result["precondition_entity"] = entity
        return result

    def finish_tool(self, payload: dict[str, Any]):
        tool_name = str(payload.get("tool_name") or "")
        tool = self.store.get_tool(tool_name)
        operation = str(tool.get("semantic") or "unknown") if tool else "unknown"
        if operation != "write":
            return original_finish(self, payload)
        event_id = str(payload.get("event_id") or "").strip()
        decision_id = str(payload.get("decision_id") or "").strip()
        token = str(payload.get("reservation_token") or "").strip()
        if not event_id or not decision_id or not token:
            raise ValueError("write result requires event_id, decision_id and reservation_token")
        outcome = parse_tool_outcome(payload.get("result"), operation="write")
        result_hash = canonical_hash({"status": outcome.status, "payload": outcome.payload})
        first = self.store.record_callback(event_id, decision_id, token, outcome.status, result_hash)
        if not first:
            return {"recorded": True, "duplicate": True, "event_id": event_id, "action_id": None, "outcome": outcome.__dict__}
        try:
            result = original_finish(self, payload)
        except Exception:
            with self.store.connection() as conn:
                conn.execute("DELETE FROM callback_events WHERE event_id=? AND decision_id=?", (event_id, decision_id))
            raise
        self.store.finalize_callback(event_id, decision_id, result_hash)
        result["event_id"] = event_id
        result["duplicate"] = False
        return result

    def verify_decision(self, payload: dict[str, Any]):
        decision_id = str(payload.get("decision_id") or "").strip()
        session_id = str(payload.get("session_id") or "").strip()
        try:
            evidence_action_id = int(payload.get("evidence_action_id"))
        except (TypeError, ValueError) as exc:
            raise ValueError("evidence_action_id is required") from exc
        worker = self.store.worker_for_session(session_id)
        if not worker or worker.get("role") != "verifier":
            raise ValueError("only a bound verifier may verify a decision")
        task = self.store.get_task(worker["task_id"])
        if not task or task.get("verifier_session_id") != session_id:
            raise ValueError("session is not the task's current verifier")
        decision = self.store.get_decision(decision_id)
        if not decision or decision.get("task_id") != worker.get("task_id"):
            raise ValueError("decision does not belong to verifier task")
        action = self.store.get_action(evidence_action_id)
        if not action or action.get("session_id") != session_id or action.get("task_id") != worker.get("task_id"):
            raise ValueError("verification evidence must belong to the current verifier and task")
        if action.get("phase") != "after" or action.get("operation") != "read" or not action.get("allowed") or action.get("structured_result") is not True or not isinstance(action.get("result"), (dict, list)):
            raise ValueError("evidence must be a successful structured cataloged read")
        tool = self.store.get_tool(str(action.get("tool_name") or ""))
        if not tool or not service_module._family_matches(decision, str(tool.get("family") or "")):
            raise ValueError("read evidence tool family does not match the decision")
        read_at = datetime.fromisoformat(str(action.get("created_at") or ""))
        executed_at = datetime.fromisoformat(str(decision.get("executed_at") or decision.get("reserved_at") or ""))
        if read_at.tzinfo is None:
            read_at = read_at.replace(tzinfo=UTC)
        if executed_at.tzinfo is None:
            executed_at = executed_at.replace(tzinfo=UTC)
        if read_at < executed_at:
            raise ValueError("read evidence predates the write attempt")
        if (datetime.now(UTC) - read_at.astimezone(UTC)).total_seconds() > int(self.store.get_settings().get("read_evidence_max_age_seconds", 600)):
            raise ValueError("read evidence is too old")
        expected = decision.get("payload", {}).get("expected_state")
        if not isinstance(expected, dict) or not expected:
            payload_data = decision.get("payload", {})
            expected = {str(payload_data.get("field")): payload_data.get("after")} if payload_data.get("field") else {}
        entity = select_entity_object(action["result"], decision, expected)
        differences = expected_differences(expected, entity)
        status = "verified" if expected and not differences else "mismatch"
        return self.store.record_verification(
            decision_id=decision_id,
            task_id=worker["task_id"],
            verifier_session_id=session_id,
            evidence_action_id=evidence_action_id,
            expected=expected,
            actual=entity,
            differences=differences,
            status=status,
            message=str(payload.get("message") or ("entity state matches" if status == "verified" else "entity state does not match")),
        )

    def runtime_status(self, payload: dict[str, Any]):
        return self.store.record_runtime_status(
            str(payload.get("component") or "hermes-plugin"),
            payload.get("state") if isinstance(payload.get("state"), dict) else {},
        )

    def context(self, session_id):
        result = original_context(self, session_id)
        result["reports"] = {"counts": _report_counts(self.store), "recent": self.store.list_report_jobs(10)}
        result["runtime_status"] = self.store.runtime_status()
        return result

    Service.sync_catalog = sync_catalog
    Service.plan_cycle = plan_cycle
    Service.create_report = create_report
    Service.transition_report = transition_report
    Service.prepare_write = prepare_write
    Service.finish_tool = finish_tool
    Service.verify_decision = verify_decision
    Service.runtime_status = runtime_status
    Service.context = context


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _configure_settings()
    _install_store()
    _install_service()
    _INSTALLED = True
