from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import secrets
import sqlite3
import threading
import uuid
from typing import Any, Iterator

from .catalog import ToolDescriptor, catalog_digest
from .policy import redact, redact_text

UTC = timezone.utc


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def future_iso(seconds: int) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


SAFETY_LOCKED_SETTINGS: dict[str, Any] = {
    "catalog_required": True,
    "catalog_drift_blocks_writes": True,
    "require_planned_writes": True,
    "require_independent_verification": True,
    "max_write_batch_size": 1,
    "block_deletes": True,
    "block_account_admin": True,
    "block_high_risk_writes": True,
    "require_read_evidence_verification": True,
}


DEFAULT_SETTINGS: dict[str, Any] = {
    "mode": "observe",
    "execution_enabled": False,
    "catalog_required": True,
    "catalog_drift_blocks_writes": True,
    "require_planned_writes": True,
    "require_independent_verification": True,
    "reservation_ttl_seconds": 900,
    "decision_cooldown_hours": 24,
    "max_write_batch_size": 1,
    "allow_data_jobs": True,
    "max_data_jobs_per_day": 30,
    "max_bid_change_pct": 20,
    "max_budget_change_pct": 25,
    "max_actions_per_task": 50,
    "max_actions_per_day": 250,
    "max_campaign_creates_per_day": 2,
    "block_deletes": True,
    "block_account_admin": True,
    "block_high_risk_writes": True,
    "max_placement_change_points": 25,
    "target_acos": 30,
    "max_acos": 45,
    "min_clicks": 8,
    "min_orders": 2,
    "min_spend": 10,
    "waste_clicks": 12,
    "waste_spend": 20,
    "harvest_orders": 2,
    "harvest_max_acos": 30,
    "bid_increase_pct": 10,
    "bid_decrease_pct": 12,
    "severe_bid_decrease_pct": 20,
    "budget_increase_pct": 15,
    "attribution_lag_days": 2,
    "min_window_days": 7,
    "max_data_age_days": 7,
    "allow_bid_changes": True,
    "allow_budget_changes": True,
    "allow_negatives": True,
    "allow_harvest": True,
    "allow_placement_changes": True,
    "allow_campaign_creation": False,
    "allow_official_recommendation_apply": False,
    "recommendation_types": ["BID", "BUDGET", "KEYWORD", "TARGET"],
    "max_decision_age_minutes": 180,
    "read_evidence_max_age_seconds": 600,
    "require_read_evidence_verification": True,
}


BOOLEAN_SETTINGS = {
    "execution_enabled", "allow_data_jobs", "allow_bid_changes", "allow_budget_changes",
    "allow_negatives", "allow_harvest", "allow_placement_changes", "allow_campaign_creation",
    "allow_official_recommendation_apply", *SAFETY_LOCKED_SETTINGS.keys(),
}
INTEGER_SETTING_RANGES: dict[str, tuple[int, int]] = {
    "reservation_ttl_seconds": (30, 86400),
    "decision_cooldown_hours": (1, 8760),
    "max_write_batch_size": (1, 1),
    "max_data_jobs_per_day": (1, 10000),
    "max_actions_per_task": (1, 10000),
    "max_actions_per_day": (1, 100000),
    "max_campaign_creates_per_day": (1, 1000),
    "min_clicks": (0, 1000000),
    "min_orders": (0, 1000000),
    "waste_clicks": (0, 1000000),
    "harvest_orders": (0, 1000000),
    "attribution_lag_days": (0, 90),
    "min_window_days": (1, 365),
    "max_data_age_days": (0, 365),
    "max_decision_age_minutes": (1, 10080),
    "read_evidence_max_age_seconds": (30, 86400),
}
NUMERIC_SETTING_RANGES: dict[str, tuple[float, float]] = {
    "max_bid_change_pct": (0.01, 100.0),
    "max_budget_change_pct": (0.01, 100.0),
    "max_placement_change_points": (0.01, 900.0),
    "target_acos": (0.01, 1000.0),
    "max_acos": (0.01, 1000.0),
    "min_spend": (0.0, 1000000000.0),
    "waste_spend": (0.0, 1000000000.0),
    "harvest_max_acos": (0.01, 1000.0),
    "bid_increase_pct": (0.0, 100.0),
    "bid_decrease_pct": (0.0, 100.0),
    "severe_bid_decrease_pct": (0.0, 100.0),
    "budget_increase_pct": (0.0, 100.0),
}

STRATEGY_SETTING_KEYS = {
    "target_acos", "max_acos", "min_clicks", "min_orders", "min_spend", "waste_clicks",
    "waste_spend", "harvest_orders", "harvest_max_acos", "bid_increase_pct", "bid_decrease_pct",
    "severe_bid_decrease_pct", "budget_increase_pct", "max_bid_change_pct", "max_budget_change_pct",
    "attribution_lag_days", "min_window_days", "max_data_age_days", "allow_bid_changes",
    "allow_budget_changes", "allow_negatives", "allow_harvest", "allow_placement_changes",
    "allow_campaign_creation", "allow_official_recommendation_apply", "recommendation_types",
}


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        return row is not None

    @classmethod
    def _ensure_column(cls, conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        if not cls._table_exists(conn, table):
            return
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @classmethod
    def _preflight_v1_migration(cls, conn: sqlite3.Connection) -> None:
        """Add v2 columns before CREATE INDEX statements reference them.

        SQLite skips CREATE TABLE IF NOT EXISTS for an existing v1 table, but
        it still evaluates later CREATE INDEX statements.  The preflight keeps
        an already-deployed v1 database bootable without requiring a reset.
        Foreign-key clauses cannot be added with ALTER TABLE; application-level
        validation remains authoritative for migrated rows.
        """
        for column, definition in (
            ("cycle_id", "TEXT"),
            ("verifier_session_id", "TEXT"),
            ("verifier_subagent_id", "TEXT"),
        ):
            cls._ensure_column(conn, "tasks", column, definition)
        for column, definition in (
            ("decision_id", "TEXT"),
            ("tool_call_id", "TEXT"),
            ("plan_key", "TEXT"),
            ("reservation_token", "TEXT"),
            ("outcome_status", "TEXT"),
            ("structured_result", "INTEGER"),
            ("result_json", "TEXT"),
        ):
            cls._ensure_column(conn, "actions", column, definition)
        cls._ensure_column(conn, "verifications", "evidence_action_id", "INTEGER")

    def _init_schema(self) -> None:
        with self.connection() as conn:
            self._preflight_v1_migration(conn)
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS profiles (
                    profile_id TEXT PRIMARY KEY,
                    name TEXT,
                    marketplace TEXT,
                    country_code TEXT,
                    currency TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    strategy_json TEXT NOT NULL DEFAULT '{}',
                    last_seen_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS mcp_tools (
                    registered_name TEXT PRIMARY KEY,
                    native_name TEXT NOT NULL,
                    server_name TEXT NOT NULL,
                    semantic TEXT NOT NULL,
                    family TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    schema_json TEXT NOT NULL,
                    schema_hash TEXT NOT NULL,
                    source TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    drifted INTEGER NOT NULL DEFAULT 0,
                    discovered_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_mcp_semantic_family ON mcp_tools(semantic,family,enabled);
                CREATE TABLE IF NOT EXISTS cycles (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source TEXT NOT NULL,
                    window_start TEXT,
                    window_end TEXT,
                    grain TEXT NOT NULL,
                    data_quality_json TEXT NOT NULL,
                    kpi_json TEXT NOT NULL,
                    snapshot_hash TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY(profile_id) REFERENCES profiles(profile_id)
                );
                CREATE INDEX IF NOT EXISTS idx_cycles_profile_created ON cycles(profile_id,created_at DESC);
                CREATE TABLE IF NOT EXISTS metric_rows (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cycle_id TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    ad_product TEXT,
                    level TEXT NOT NULL,
                    entity_id TEXT,
                    parent_id TEXT,
                    row_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(cycle_id) REFERENCES cycles(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_metrics_cycle_level ON metric_rows(cycle_id,level);
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    cycle_id TEXT,
                    title TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    parent_session_id TEXT,
                    worker_session_id TEXT,
                    worker_subagent_id TEXT,
                    verifier_session_id TEXT,
                    verifier_subagent_id TEXT,
                    write_allowed INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    FOREIGN KEY(cycle_id) REFERENCES cycles(id)
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_status_created ON tasks(status, created_at DESC);
                CREATE TABLE IF NOT EXISTS decisions (
                    id TEXT PRIMARY KEY,
                    cycle_id TEXT NOT NULL,
                    task_id TEXT,
                    profile_id TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    rule_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    expected_family TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    plan_key TEXT NOT NULL,
                    reserved_by TEXT,
                    reservation_token TEXT,
                    reservation_expires_at TEXT,
                    execution_tool TEXT,
                    execution_outcome TEXT,
                    result_json TEXT,
                    failure TEXT,
                    created_at TEXT NOT NULL,
                    reserved_at TEXT,
                    executed_at TEXT,
                    verified_at TEXT,
                    FOREIGN KEY(cycle_id) REFERENCES cycles(id) ON DELETE CASCADE,
                    FOREIGN KEY(task_id) REFERENCES tasks(id)
                );
                CREATE INDEX IF NOT EXISTS idx_decisions_task_status ON decisions(task_id,status,priority DESC);
                CREATE INDEX IF NOT EXISTS idx_decisions_cycle ON decisions(cycle_id,priority DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_decisions_cycle_plan ON decisions(cycle_id,plan_key);
                CREATE INDEX IF NOT EXISTS idx_decisions_plan_status ON decisions(plan_key,status,created_at DESC);
                CREATE TABLE IF NOT EXISTS actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    decision_id TEXT,
                    task_id TEXT,
                    session_id TEXT,
                    tool_call_id TEXT,
                    plan_key TEXT,
                    reservation_token TEXT,
                    actor_role TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    allowed INTEGER NOT NULL,
                    success INTEGER,
                    outcome_status TEXT,
                    structured_result INTEGER,
                    reason TEXT,
                    args_json TEXT NOT NULL,
                    result_summary TEXT,
                    result_json TEXT,
                    duration_ms INTEGER,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id),
                    FOREIGN KEY(decision_id) REFERENCES decisions(id)
                );
                CREATE INDEX IF NOT EXISTS idx_actions_created ON actions(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_actions_task ON actions(task_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_actions_decision ON actions(decision_id, id DESC);
                CREATE TABLE IF NOT EXISTS verifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    decision_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    verifier_session_id TEXT NOT NULL,
                    evidence_action_id INTEGER,
                    status TEXT NOT NULL,
                    expected_json TEXT NOT NULL,
                    actual_json TEXT NOT NULL,
                    differences_json TEXT NOT NULL,
                    message TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(decision_id) REFERENCES decisions(id),
                    FOREIGN KEY(task_id) REFERENCES tasks(id)
                );
                CREATE INDEX IF NOT EXISTS idx_verifications_task ON verifications(task_id,created_at DESC);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    level TEXT NOT NULL,
                    type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    task_id TEXT,
                    message TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at DESC);
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    severity TEXT NOT NULL,
                    code TEXT NOT NULL,
                    profile_id TEXT,
                    task_id TEXT,
                    decision_id TEXT,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_alerts_status_created ON alerts(status,created_at DESC);
                CREATE TABLE IF NOT EXISTS workers (
                    session_id TEXT PRIMARY KEY,
                    subagent_id TEXT,
                    parent_session_id TEXT,
                    task_id TEXT,
                    role TEXT NOT NULL,
                    status TEXT NOT NULL,
                    model TEXT,
                    goal TEXT,
                    last_seen_at TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    stopped_at TEXT,
                    FOREIGN KEY(task_id) REFERENCES tasks(id)
                );
                CREATE TABLE IF NOT EXISTS stream_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_id TEXT,
                    dataset_id TEXT NOT NULL,
                    event_time TEXT,
                    dedupe_key TEXT UNIQUE,
                    payload_json TEXT NOT NULL,
                    received_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_stream_profile_time ON stream_events(profile_id,event_time DESC);
                """
            )
            # Re-run defensively for partially upgraded databases.
            self._preflight_v1_migration(conn)
            for key, value in DEFAULT_SETTINGS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings(key,value,updated_at) VALUES(?,?,?)",
                    (key, json.dumps(value), now_iso()),
                )

    # Settings and profiles -------------------------------------------------
    def get_settings(self) -> dict[str, Any]:
        with self.connection() as conn:
            return {row["key"]: json.loads(row["value"]) for row in conn.execute("SELECT key,value FROM settings")}

    @staticmethod
    def validate_strategy_overrides(values: dict[str, Any], current: dict[str, Any] | None = None) -> dict[str, Any]:
        if not isinstance(values, dict):
            raise ValueError("strategy overrides must be an object")
        unknown = set(values) - STRATEGY_SETTING_KEYS
        if unknown:
            raise ValueError(f"unknown strategy settings: {', '.join(sorted(unknown))}")
        normalized = dict(values)
        for key in BOOLEAN_SETTINGS & values.keys():
            if not isinstance(values[key], bool):
                raise ValueError(f"{key} must be a boolean")
        for key, (minimum, maximum) in INTEGER_SETTING_RANGES.items():
            if key not in values:
                continue
            value = values[key]
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{key} must be an integer")
            if not minimum <= value <= maximum:
                raise ValueError(f"{key} must be between {minimum} and {maximum}")
        for key, (minimum, maximum) in NUMERIC_SETTING_RANGES.items():
            if key not in values:
                continue
            value = values[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{key} must be numeric")
            if not minimum <= float(value) <= maximum:
                raise ValueError(f"{key} is outside its safe range")
        if "recommendation_types" in values:
            items = values["recommendation_types"]
            if not isinstance(items, list) or not items or len(items) > 100 or any(
                not isinstance(item, str) or not item.strip() or len(item) > 80 for item in items
            ):
                raise ValueError("recommendation_types must be a non-empty list of short strings")
            normalized["recommendation_types"] = sorted({item.strip().upper() for item in items})
        baseline = dict(current or DEFAULT_SETTINGS)
        baseline.update(normalized)
        if float(baseline["target_acos"]) > float(baseline["max_acos"]):
            raise ValueError("target_acos cannot exceed max_acos")
        return normalized

    def update_settings(self, updates: dict[str, Any]) -> dict[str, Any]:
        allowed = set(DEFAULT_SETTINGS)
        unknown = set(updates) - allowed
        if unknown:
            raise ValueError(f"unknown settings: {', '.join(sorted(unknown))}")
        if "mode" in updates and updates["mode"] not in {"autopilot", "observe", "paused"}:
            raise ValueError("mode must be autopilot, observe, or paused")
        for key, required in SAFETY_LOCKED_SETTINGS.items():
            if key in updates and updates[key] != required:
                raise ValueError(f"{key} is a locked safety invariant")
        for key in BOOLEAN_SETTINGS & updates.keys():
            if not isinstance(updates[key], bool):
                raise ValueError(f"{key} must be a boolean")
        for key, (minimum, maximum) in INTEGER_SETTING_RANGES.items():
            if key not in updates:
                continue
            value = updates[key]
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{key} must be an integer")
            if not minimum <= value <= maximum:
                raise ValueError(f"{key} must be between {minimum} and {maximum}")
        for key, (minimum, maximum) in NUMERIC_SETTING_RANGES.items():
            if key not in updates:
                continue
            value = updates[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{key} must be numeric")
            numeric = float(value)
            if not minimum <= numeric <= maximum:
                raise ValueError(f"{key} is outside its safe range")
        if "recommendation_types" in updates:
            values = updates["recommendation_types"]
            if not isinstance(values, list) or not values or len(values) > 100 or any(
                not isinstance(item, str) or not item.strip() or len(item) > 80 for item in values
            ):
                raise ValueError("recommendation_types must be a non-empty list of short strings")
            updates = dict(updates)
            updates["recommendation_types"] = sorted({item.strip().upper() for item in values})
        current = self.get_settings()
        effective_mode = updates.get("mode", current.get("mode"))
        if updates.get("execution_enabled") is True and effective_mode != "autopilot":
            raise ValueError("execution can only be enabled in autopilot mode")
        if effective_mode != "autopilot" and "mode" in updates:
            updates = dict(updates)
            updates["execution_enabled"] = False
        target_acos = float(updates.get("target_acos", current.get("target_acos", 30)))
        max_acos = float(updates.get("max_acos", current.get("max_acos", 45)))
        if target_acos > max_acos:
            raise ValueError("target_acos cannot exceed max_acos")
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                timestamp = now_iso()
                for key, value in updates.items():
                    conn.execute(
                        "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                        (key, json.dumps(value), timestamp),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        self.event("info", "settings.updated", "operator", None, "Control settings updated", updates)
        return self.get_settings()

    def upsert_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        profile_id = str(profile.get("profile_id") or profile.get("id") or "").strip()
        if not profile_id:
            raise ValueError("profile_id is required")
        now = now_iso()
        strategy = profile.get("strategy") if isinstance(profile.get("strategy"), dict) else {}
        current_profile = self.get_profile(profile_id)
        current_strategy = current_profile.get("strategy", {}) if current_profile else {}
        strategy = self.validate_strategy_overrides(strategy, {**DEFAULT_SETTINGS, **current_strategy}) if strategy else {}
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO profiles(profile_id,name,marketplace,country_code,currency,enabled,strategy_json,last_seen_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(profile_id) DO UPDATE SET "
                "name=excluded.name,marketplace=excluded.marketplace,country_code=excluded.country_code,currency=excluded.currency,"
                "enabled=excluded.enabled,strategy_json=CASE WHEN excluded.strategy_json='{}' THEN profiles.strategy_json ELSE excluded.strategy_json END,"
                "last_seen_at=excluded.last_seen_at,updated_at=excluded.updated_at",
                (profile_id, profile.get("name"), profile.get("marketplace"), profile.get("country_code"),
                 profile.get("currency"), int(profile.get("enabled", True)), json.dumps(redact(strategy), ensure_ascii=False), now, now),
            )
        return self.get_profile(profile_id) or {}

    def get_profile(self, profile_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM profiles WHERE profile_id=?", (profile_id,)).fetchone()
        if not row:
            return None
        item = dict(row); item["enabled"] = bool(item["enabled"]); item["strategy"] = json.loads(item.pop("strategy_json"))
        return item

    def list_profiles(self) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute("SELECT * FROM profiles ORDER BY marketplace,name,profile_id").fetchall()
        return [self.get_profile(row["profile_id"]) for row in rows]

    # MCP catalog -----------------------------------------------------------
    def sync_catalog(self, tools: list[ToolDescriptor]) -> dict[str, Any]:
        now = now_iso(); drifted: list[str] = []; seen: set[str] = set(); removed: list[str] = []
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                previous_enabled = {
                    row["registered_name"] for row in conn.execute(
                        "SELECT registered_name FROM mcp_tools WHERE server_name='amazon-ads' AND enabled=1"
                    )
                }
                for tool in tools:
                    seen.add(tool.registered_name)
                    old = conn.execute("SELECT native_name,schema_hash,semantic,family,risk FROM mcp_tools WHERE registered_name=?", (tool.registered_name,)).fetchone()
                    drift = bool(old and any((
                        old["native_name"] != tool.native_name,
                        old["schema_hash"] != tool.schema_hash,
                        old["semantic"] != tool.semantic,
                        old["family"] != tool.family,
                        old["risk"] != tool.risk,
                    )))
                    if drift:
                        drifted.append(tool.registered_name)
                    conn.execute(
                        "INSERT INTO mcp_tools(registered_name,native_name,server_name,semantic,family,risk,schema_json,schema_hash,source,enabled,drifted,discovered_at,last_seen_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(registered_name) DO UPDATE SET "
                        "native_name=excluded.native_name,server_name=excluded.server_name,semantic=excluded.semantic,family=excluded.family,risk=excluded.risk,"
                        "schema_json=excluded.schema_json,schema_hash=excluded.schema_hash,source=excluded.source,enabled=excluded.enabled,"
                        "drifted=MAX(mcp_tools.drifted,excluded.drifted),last_seen_at=excluded.last_seen_at",
                        (tool.registered_name, tool.native_name, tool.server_name, tool.semantic, tool.family, tool.risk,
                         json.dumps(tool.schema, ensure_ascii=False), tool.schema_hash, tool.source, int(tool.enabled), int(drift), now, now),
                    )
                if seen:
                    removed = sorted(previous_enabled - seen)
                    placeholders = ",".join("?" for _ in seen)
                    conn.execute(f"UPDATE mcp_tools SET enabled=0 WHERE server_name='amazon-ads' AND registered_name NOT IN ({placeholders})", tuple(seen))
                conn.commit()
            except Exception:
                conn.rollback(); raise
        if drifted:
            self.alert_once("critical", "MCP_SCHEMA_DRIFT", None, None, None,
                            "Amazon Ads MCP schema changed; affected tools remain blocked until reviewed",
                            {"tools": drifted})
        if removed:
            self.alert_once("critical", "MCP_TOOL_REMOVED", None, None, None,
                            "Previously available Amazon Ads MCP tools disappeared from discovery",
                            {"tools": removed})
        self.event("info", "mcp.catalog.synced", "hermes", None, "Amazon Ads MCP catalog synchronized",
                   {"tool_count": len(tools), "drifted": drifted, "removed": removed, "digest": catalog_digest(tools)})
        return {"tool_count": len(tools), "drifted": drifted, "removed": removed, "digest": catalog_digest(tools)}

    def get_tool(self, registered_name: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM mcp_tools WHERE registered_name=?", (registered_name,)).fetchone()
        if not row:
            return None
        item = dict(row); item["enabled"] = bool(item["enabled"]); item["drifted"] = bool(item["drifted"])
        item["schema"] = json.loads(item.pop("schema_json")); return item

    def list_tools(self, limit: int = 500) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute("SELECT registered_name FROM mcp_tools ORDER BY family,semantic,registered_name LIMIT ?", (min(2000, max(1, limit)),)).fetchall()
        return [self.get_tool(row["registered_name"]) for row in rows]

    def acknowledge_tool_drift(self, registered_name: str) -> None:
        with self.connection() as conn:
            cursor = conn.execute("UPDATE mcp_tools SET drifted=0 WHERE registered_name=? AND enabled=1", (registered_name,))
        if cursor.rowcount != 1:
            raise KeyError("enabled MCP tool not found")
        self.event("warning", "mcp.catalog.drift_acknowledged", "operator", None, f"Acknowledged schema drift for {registered_name}", {})

    # Cycles and deterministic decisions -----------------------------------
    def create_cycle(self, *, profile: dict[str, Any], source: str, window: dict[str, Any],
                     data_quality: dict[str, Any], kpis: dict[str, Any], snapshot: dict[str, Any],
                     decisions: list[dict[str, Any]], created_by: str) -> dict[str, Any]:
        profile_row = self.upsert_profile(profile); profile_id = profile_row["profile_id"]
        cycle_id = uuid.uuid4().hex[:20]
        snapshot_hash = __import__("hashlib").sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
        created = now_iso()
        status = "planned" if decisions else "no_action"
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT INTO cycles(id,profile_id,status,source,window_start,window_end,grain,data_quality_json,kpi_json,snapshot_hash,created_by,created_at,completed_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (cycle_id, profile_id, status, source[:80], window.get("start"), window.get("end"), str(window.get("grain") or "daily"),
                     json.dumps(redact(data_quality), ensure_ascii=False), json.dumps(redact(kpis), ensure_ascii=False), snapshot_hash, created_by[:80], created,
                     created if not decisions else None),
                )
                for level in ("campaigns", "targets", "search_terms", "placements", "recommendations", "budget_usage", "hourly"):
                    for row in snapshot.get(level, []) if isinstance(snapshot.get(level), list) else []:
                        if not isinstance(row, dict):
                            continue
                        entity_id = row.get("campaign_id") or row.get("target_id") or row.get("keyword_id") or row.get("recommendation_id") or row.get("id")
                        parent_id = row.get("ad_group_id") or row.get("campaign_id")
                        conn.execute(
                            "INSERT INTO metric_rows(cycle_id,profile_id,ad_product,level,entity_id,parent_id,row_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
                            (cycle_id, profile_id, row.get("ad_product"), level, str(entity_id) if entity_id is not None else None,
                             str(parent_id) if parent_id is not None else None, json.dumps(redact(row), ensure_ascii=False), created),
                        )
                for raw in decisions:
                    decision_id = uuid.uuid4().hex[:20]
                    conn.execute(
                        "INSERT INTO decisions(id,cycle_id,profile_id,entity_type,entity_id,action_type,status,priority,rule_id,reason,evidence_json,payload_json,expected_family,risk,plan_key,created_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (decision_id, cycle_id, profile_id, raw["entity_type"], raw["entity_id"], raw["action_type"], "planned",
                         int(raw["priority"]), raw["rule_id"], redact_text(raw["reason"]), json.dumps(redact(raw.get("evidence", {})), ensure_ascii=False),
                         json.dumps(redact(raw.get("payload", {})), ensure_ascii=False), raw["expected_family"], raw.get("risk", "medium"), raw["plan_key"], created),
                    )
                conn.commit()
            except Exception:
                conn.rollback(); raise
        self.event("info", "cycle.planned", created_by, None, f"Optimization cycle planned for profile {profile_id}",
                   {"cycle_id": cycle_id, "decisions": len(decisions), "quality": data_quality, "kpis": kpis})
        return self.get_cycle(cycle_id) or {}

    def get_cycle(self, cycle_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM cycles WHERE id=?", (cycle_id,)).fetchone()
        if not row: return None
        item = dict(row); item["data_quality"] = json.loads(item.pop("data_quality_json")); item["kpis"] = json.loads(item.pop("kpi_json"))
        item["decisions"] = self.list_decisions(cycle_id=cycle_id, limit=500); return item

    def list_cycles(self, limit: int = 50, profile_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT id FROM cycles"; params: list[Any] = []
        if profile_id: sql += " WHERE profile_id=?"; params.append(profile_id)
        sql += " ORDER BY created_at DESC LIMIT ?"; params.append(min(500, max(1, limit)))
        with self.connection() as conn: rows = conn.execute(sql, params).fetchall()
        return [self.get_cycle(row["id"]) for row in rows]

    def list_decisions(self, *, cycle_id: str | None = None, task_id: str | None = None,
                       status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        clauses = []; params: list[Any] = []
        if cycle_id: clauses.append("cycle_id=?"); params.append(cycle_id)
        if task_id: clauses.append("task_id=?"); params.append(task_id)
        if status: clauses.append("status=?"); params.append(status)
        sql = "SELECT * FROM decisions" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY priority DESC,created_at LIMIT ?"
        params.append(min(1000, max(1, limit)))
        with self.connection() as conn: rows = conn.execute(sql, params).fetchall()
        return [self._decision_dict(row) for row in rows]

    def get_decision(self, decision_id: str) -> dict[str, Any] | None:
        with self.connection() as conn: row = conn.execute("SELECT * FROM decisions WHERE id=?", (decision_id,)).fetchone()
        return self._decision_dict(row) if row else None

    # Tasks, workers, reservations -----------------------------------------
    def create_task(self, title: str, kind: str, created_by: str, parent_session_id: str | None,
                    write_allowed: bool, payload: dict[str, Any], cycle_id: str | None = None,
                    decision_ids: list[str] | None = None) -> dict[str, Any]:
        task_id = uuid.uuid4().hex[:16]; created = now_iso(); decision_ids = decision_ids or []
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT INTO tasks(id,cycle_id,title,kind,status,created_by,parent_session_id,write_allowed,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (task_id, cycle_id, title[:240], kind[:40], "planned", created_by[:80], parent_session_id,
                     int(write_allowed), json.dumps(redact(payload), ensure_ascii=False), created),
                )
                if decision_ids:
                    placeholders = ",".join("?" for _ in decision_ids)
                    found = conn.execute(f"SELECT COUNT(*) FROM decisions WHERE id IN ({placeholders}) AND task_id IS NULL", tuple(decision_ids)).fetchone()[0]
                    if found != len(decision_ids):
                        raise ValueError("one or more decisions are missing or already assigned")
                    conn.execute(f"UPDATE decisions SET task_id=? WHERE id IN ({placeholders})", (task_id, *decision_ids))
                conn.commit()
            except Exception:
                conn.rollback(); raise
        self.event("info", "task.created", created_by, task_id, f"Task created: {title}", {"kind": kind, "write_allowed": write_allowed, "decisions": decision_ids})
        return self.get_task(task_id) or {}

    def create_task_from_cycle(self, cycle_id: str, created_by: str, parent_session_id: str | None = None,
                               limit: int = 25) -> dict[str, Any]:
        decisions = [d for d in self.list_decisions(cycle_id=cycle_id, status="planned", limit=limit) if d["risk"] != "critical"]
        if not decisions: raise ValueError("cycle has no executable planned decisions")
        profile_id = decisions[0]["profile_id"]
        payload = {
            "objective": f"Execute {len(decisions)} deterministic Amazon Ads decisions for profile {profile_id}",
            "decision_ids": [d["id"] for d in decisions],
            "expected_actions": [{
                "decision_id": d["id"], "idempotency_key": d["plan_key"], "action_type": d["action_type"],
                "entity_id": d["entity_id"], "expected_family": d["expected_family"], **d["payload"],
            } for d in decisions],
        }
        return self.create_task(f"Amazon Ads cycle {cycle_id}", "optimization", created_by, parent_session_id, True,
                                payload, cycle_id=cycle_id, decision_ids=[d["id"] for d in decisions])

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.connection() as conn: row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self._task_dict(row) if row else None

    def list_tasks(self, limit: int = 100, status: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM tasks"; params: list[Any] = []
        if status: sql += " WHERE status=?"; params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"; params.append(min(500, max(1, limit)))
        with self.connection() as conn: rows = conn.execute(sql, params).fetchall()
        return [self._task_dict(row) for row in rows]

    def bind_worker(self, task_id: str, parent_session_id: str | None, worker_session_id: str,
                    worker_subagent_id: str | None, goal: str, role: str = "executor", model: str | None = None) -> dict[str, Any]:
        if role not in {"executor", "verifier"}:
            raise ValueError("role must be executor or verifier")
        if not worker_session_id:
            raise ValueError("worker_session_id is required")
        started = now_iso()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
                if not task:
                    raise KeyError("task not found")
                allowed_status = {"planned", "queued", "executing", "verifying"}
                if task["status"] not in allowed_status:
                    raise ValueError(f"task cannot bind {role} from status {task['status']}")
                if role == "executor":
                    if task["verifier_session_id"]:
                        raise ValueError("task already entered verification; executor cannot be rebound")
                    if task["worker_session_id"] and task["worker_session_id"] != worker_session_id:
                        active = conn.execute(
                            "SELECT 1 FROM workers WHERE session_id=? AND status='running'",
                            (task["worker_session_id"],),
                        ).fetchone()
                        if active:
                            raise ValueError("task already has a different running executor")
                    conn.execute(
                        "UPDATE tasks SET status='executing',parent_session_id=COALESCE(parent_session_id,?),"
                        "worker_session_id=?,worker_subagent_id=?,started_at=COALESCE(started_at,?) WHERE id=?",
                        (parent_session_id, worker_session_id, worker_subagent_id, started, task_id),
                    )
                else:
                    if task["worker_session_id"] == worker_session_id:
                        raise ValueError("verifier must use a different Hermes session from executor")
                    if task["verifier_session_id"] and task["verifier_session_id"] != worker_session_id:
                        active = conn.execute(
                            "SELECT 1 FROM workers WHERE session_id=? AND status='running'",
                            (task["verifier_session_id"],),
                        ).fetchone()
                        if active:
                            raise ValueError("task already has a different running verifier")
                    pending = conn.execute(
                        "SELECT COUNT(*) FROM decisions WHERE task_id=? AND status NOT IN "
                        "('executed','pending','uncertain','failed','blocked','verified','mismatch')",
                        (task_id,),
                    ).fetchone()[0]
                    if pending:
                        raise ValueError("executor decisions are not ready for verification")
                    conn.execute(
                        "UPDATE tasks SET status='verifying',verifier_session_id=?,verifier_subagent_id=? WHERE id=?",
                        (worker_session_id, worker_subagent_id, task_id),
                    )
                existing = conn.execute(
                    "SELECT task_id,role,status FROM workers WHERE session_id=?", (worker_session_id,)
                ).fetchone()
                if existing and existing["status"] == "running" and (
                    existing["task_id"] != task_id or existing["role"] != role
                ):
                    raise ValueError("Hermes session is already bound to another running role or task")
                conn.execute(
                    "INSERT INTO workers(session_id,subagent_id,parent_session_id,task_id,role,status,model,goal,last_seen_at,started_at) "
                    "VALUES(?,?,?,?,?,'running',?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET "
                    "subagent_id=excluded.subagent_id,parent_session_id=excluded.parent_session_id,task_id=excluded.task_id,"
                    "role=excluded.role,status='running',model=excluded.model,goal=excluded.goal,last_seen_at=excluded.last_seen_at,"
                    "stopped_at=NULL",
                    (worker_session_id, worker_subagent_id, parent_session_id, task_id, role, model, goal[:4000], started, started),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        self.event(
            "info", "worker.bound", role, task_id, f"Hermes {role} bound to task",
            {"session_id": worker_session_id, "subagent_id": worker_subagent_id},
        )
        return self.get_task(task_id) or {}

    def worker_for_session(self, session_id: str | None) -> dict[str, Any] | None:
        if not session_id: return None
        with self.connection() as conn: row = conn.execute("SELECT * FROM workers WHERE session_id=? AND status='running'", (session_id,)).fetchone()
        return dict(row) if row else None

    def touch_worker(self, session_id: str | None) -> None:
        if session_id:
            with self.connection() as conn: conn.execute("UPDATE workers SET last_seen_at=? WHERE session_id=? AND status='running'", (now_iso(), session_id))

    def finish_worker(self, session_id: str, status: str, summary: str | None = None, duration_ms: int | None = None) -> None:
        stopped = now_iso()
        with self.connection() as conn:
            worker = conn.execute("SELECT task_id,role FROM workers WHERE session_id=?", (session_id,)).fetchone()
            conn.execute("UPDATE workers SET status=?,stopped_at=?,last_seen_at=? WHERE session_id=?", (status, stopped, stopped, session_id))
        self.event("info" if status == "completed" else "error", "worker.stopped", worker["role"] if worker else "worker",
                   worker["task_id"] if worker else None, f"Worker {status}", {"session_id": session_id, "duration_ms": duration_ms, "summary": summary or ""})

    def reconcile_expired_reservations(self) -> list[str]:
        """Quarantine expired write reservations instead of retrying them blindly.

        A process may die after Amazon accepted a write but before the post-tool hook
        records the result. Replaying such a decision could double-apply a budget or
        bid change, so expired reservations require independent read reconciliation.
        """
        now = now_iso()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                rows = conn.execute(
                    "SELECT id,profile_id,task_id FROM decisions "
                    "WHERE status='reserved' AND reservation_expires_at IS NOT NULL AND reservation_expires_at<?",
                    (now,),
                ).fetchall()
                ids = [str(row["id"]) for row in rows]
                if ids:
                    placeholders = ",".join("?" for _ in ids)
                    conn.execute(
                        f"UPDATE decisions SET status='uncertain',execution_outcome='reservation_expired',"
                        f"failure='reservation expired before a confirmed tool result' WHERE id IN ({placeholders})",
                        ids,
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        for row in rows:
            self.alert_once(
                "critical", "WRITE_RESERVATION_EXPIRED", row["profile_id"], row["task_id"], row["id"],
                "A write reservation expired without a confirmed result; independent read reconciliation is required",
                {"decision_id": row["id"]}, window_seconds=86400,
            )
        if rows:
            self.event(
                "critical", "decision.reservation_expired", "controller", None,
                f"Quarantined {len(rows)} expired write reservation(s)", {"decision_ids": ids},
            )
        return ids

    def reserve_decision(
        self, decision_id: str, task_id: str, session_id: str, ttl_seconds: int,
        cooldown_seconds: int = 86400, *, max_actions_per_task: int = 50,
        max_actions_per_day: int = 250, max_campaign_creates_per_day: int = 2,
    ) -> dict[str, Any]:
        self.reconcile_expired_reservations()
        now = now_iso(); token = secrets.token_urlsafe(24); expires = future_iso(ttl_seconds)
        cooldown_cutoff = (datetime.now(UTC) - timedelta(seconds=max(0, cooldown_seconds))).isoformat(timespec="seconds")
        day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute("SELECT * FROM decisions WHERE id=? AND task_id=?", (decision_id, task_id)).fetchone()
                if not row: raise KeyError("decision not found for task")
                if row["status"] != "planned": raise ValueError(f"decision is not reservable from status {row['status']}")
                task_count = conn.execute(
                    "SELECT COUNT(*) FROM decisions WHERE task_id=? AND reserved_at IS NOT NULL", (task_id,)
                ).fetchone()[0]
                if task_count >= max_actions_per_task:
                    raise ValueError("task write limit reached")
                daily_count = conn.execute(
                    "SELECT COUNT(*) FROM decisions WHERE reserved_at>=?", (day_start,)
                ).fetchone()[0]
                if daily_count >= max_actions_per_day:
                    raise ValueError("daily write limit reached")
                if row["action_type"] == "create_campaign":
                    create_count = conn.execute(
                        "SELECT COUNT(*) FROM decisions WHERE action_type='create_campaign' AND reserved_at>=?", (day_start,)
                    ).fetchone()[0]
                    if create_count >= max_campaign_creates_per_day:
                        raise ValueError("daily campaign creation limit reached")
                duplicate = conn.execute(
                    "SELECT id,status FROM decisions WHERE id<>? AND plan_key=? AND created_at>=? "
                    "AND status IN ('reserved','executed','pending','uncertain','verified','failed','mismatch') ORDER BY created_at DESC LIMIT 1",
                    (decision_id, row["plan_key"], cooldown_cutoff),
                ).fetchone()
                if duplicate:
                    raise ValueError(f"equivalent decision is inside cooldown ({duplicate['status']})")
                updated = conn.execute("UPDATE decisions SET status='reserved',reserved_by=?,reservation_token=?,reservation_expires_at=?,reserved_at=? "
                                       "WHERE id=? AND status='planned'", (session_id, token, expires, now, decision_id)).rowcount
                if updated != 1: raise ValueError("decision reservation race lost")
                conn.commit()
            except Exception:
                conn.rollback(); raise
        decision = self.get_decision(decision_id) or {}; decision["reservation_token"] = token; return decision

    def mark_execution(self, *, decision_id: str, reservation_token: str, tool_name: str, outcome: str,
                       result: Any, failure: str | None = None) -> dict[str, Any]:
        status_map = {"success": "executed", "pending": "pending", "partial": "uncertain", "failure": "failed", "unknown": "uncertain"}
        status = status_map.get(outcome, "failed")
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute("SELECT status,reservation_token FROM decisions WHERE id=?", (decision_id,)).fetchone()
                if not row: raise KeyError("decision not found")
                if row["reservation_token"] != reservation_token: raise ValueError("reservation token mismatch")
                if row["status"] not in {"reserved", "uncertain"}:
                    raise ValueError(f"decision cannot record execution from status {row['status']}")
                conn.execute("UPDATE decisions SET status=?,execution_tool=?,execution_outcome=?,result_json=?,failure=?,executed_at=? WHERE id=?",
                             (status, tool_name, outcome, json.dumps(redact(result), ensure_ascii=False, default=str), redact_text(failure or "") or None, now_iso(), decision_id))
                conn.commit()
            except Exception:
                conn.rollback(); raise
        return self.get_decision(decision_id) or {}

    def record_verification(self, *, decision_id: str, task_id: str, verifier_session_id: str,
                            evidence_action_id: int, expected: dict[str, Any], actual: dict[str, Any],
                            differences: dict[str, Any], status: str, message: str = "") -> dict[str, Any]:
        if status not in {"verified", "mismatch", "not_found", "error"}: raise ValueError("invalid verification status")
        final_decision_status = "verified" if status == "verified" else "mismatch"
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                worker = conn.execute("SELECT role,task_id FROM workers WHERE session_id=? AND status='running'", (verifier_session_id,)).fetchone()
                task = conn.execute("SELECT verifier_session_id FROM tasks WHERE id=?", (task_id,)).fetchone()
                if (not worker or worker["role"] != "verifier" or worker["task_id"] != task_id
                        or not task or task["verifier_session_id"] != verifier_session_id):
                    raise ValueError("verification requires the task's current bound verifier")
                decision = conn.execute("SELECT status FROM decisions WHERE id=? AND task_id=?", (decision_id, task_id)).fetchone()
                if not decision or decision["status"] not in {"executed", "pending", "uncertain", "mismatch"}:
                    raise ValueError("decision is not ready for verification")
                conn.execute(
                    "INSERT INTO verifications(decision_id,task_id,verifier_session_id,evidence_action_id,status,expected_json,actual_json,differences_json,message,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (decision_id, task_id, verifier_session_id, evidence_action_id, status,
                     json.dumps(redact(expected), ensure_ascii=False), json.dumps(redact(actual), ensure_ascii=False),
                     json.dumps(redact(differences), ensure_ascii=False), redact_text(message), now_iso()),
                )
                conn.execute("UPDATE decisions SET status=?,verified_at=? WHERE id=?", (final_decision_status, now_iso(), decision_id))
                conn.commit()
            except Exception:
                conn.rollback(); raise
        if status != "verified":
            decision = self.get_decision(decision_id) or {}
            self.alert("critical", "WRITE_VERIFICATION_MISMATCH", decision.get("profile_id"), task_id, decision_id,
                       f"Amazon Ads write verification failed: {message or status}", {"expected": expected, "actual": actual, "differences": differences})
        return self.get_decision(decision_id) or {}

    def finalize_task(self, task_id: str, actor: str, summary: str = "") -> dict[str, Any]:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
                if not task: raise KeyError("task not found")
                rows = conn.execute("SELECT status,COUNT(*) count FROM decisions WHERE task_id=? GROUP BY status", (task_id,)).fetchall()
                counts = {row["status"]: row["count"] for row in rows}
                total = sum(counts.values())
                if not total: raise ValueError("task has no decisions")
                pending = sum(counts.get(status, 0) for status in ("planned", "reserved", "executed", "pending", "uncertain"))
                if pending: raise ValueError("task still has unverified or pending decisions")
                failed = counts.get("failed", 0) + counts.get("mismatch", 0)
                status = "completed" if failed == 0 and counts.get("verified", 0) == total else "completed_with_issues"
                result = {"summary": redact_text(summary), "decision_counts": counts}
                conn.execute("UPDATE tasks SET status=?,result_json=?,error=?,completed_at=? WHERE id=?",
                             (status, json.dumps(result, ensure_ascii=False), None if failed == 0 else f"{failed} decision(s) failed verification", now_iso(), task_id))
                if task["cycle_id"]:
                    conn.execute("UPDATE cycles SET status=?,completed_at=? WHERE id=?", (status, now_iso(), task["cycle_id"]))
                conn.commit()
            except Exception:
                conn.rollback(); raise
        self.event("info" if status == "completed" else "warning", "task.finalized", actor, task_id, f"Task finalized as {status}", result)
        return self.get_task(task_id) or {}

    # Audit, alerts, stream -------------------------------------------------
    def record_action(self, *, decision_id: str | None = None, task_id: str | None, session_id: str | None,
                      tool_call_id: str | None = None, actor_role: str, phase: str, tool_name: str,
                      operation: str, allowed: bool, plan_key: str | None = None, reservation_token: str | None = None,
                      args: dict[str, Any], success: bool | None = None, outcome_status: str | None = None,
                      structured_result: bool | None = None, reason: str | None = None,
                      result_summary: str | None = None, result: Any = None,
                      duration_ms: int | None = None) -> int:
        serialized_result = None
        if result is not None:
            serialized_result = json.dumps(redact(result), ensure_ascii=False, default=str)
        with self.connection() as conn:
            cursor = conn.execute(
                "INSERT INTO actions(decision_id,task_id,session_id,tool_call_id,plan_key,reservation_token,actor_role,phase,"
                "tool_name,operation,allowed,success,outcome_status,structured_result,reason,args_json,result_summary,result_json,"
                "duration_ms,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (decision_id, task_id, session_id, tool_call_id, plan_key, reservation_token, actor_role, phase,
                 tool_name[:240], operation, int(allowed), None if success is None else int(success), outcome_status,
                 None if structured_result is None else int(structured_result), redact_text(reason or "")[:2000] or None,
                 json.dumps(redact(args), ensure_ascii=False), redact_text(result_summary or "")[:4000] or None,
                 serialized_result, duration_ms, now_iso()),
            )
            return int(cursor.lastrowid)

    @staticmethod
    def _action_dict(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["allowed"] = bool(item["allowed"])
        item["success"] = None if item["success"] is None else bool(item["success"])
        item["structured_result"] = None if item["structured_result"] is None else bool(item["structured_result"])
        item["args"] = json.loads(item.pop("args_json"))
        raw_result = item.pop("result_json", None)
        item["result"] = json.loads(raw_result) if raw_result else None
        return item

    def get_action(self, action_id: int) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
        return self._action_dict(row) if row else None

    def list_actions(self, limit: int = 200, task_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM actions"; params: list[Any] = []
        if task_id:
            sql += " WHERE task_id=?"; params.append(task_id)
        sql += " ORDER BY id DESC LIMIT ?"; params.append(min(1000, max(1, limit)))
        with self.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._action_dict(row) for row in rows]

    def list_read_evidence(self, session_id: str, task_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM actions WHERE session_id=? AND task_id=? AND phase='after' AND operation='read' "
                "AND allowed=1 AND structured_result=1 AND result_json IS NOT NULL ORDER BY id DESC LIMIT ?",
                (session_id, task_id, min(100, max(1, limit))),
            ).fetchall()
        return [self._action_dict(row) for row in rows]

    def list_verifications(self, limit: int = 200, task_id: str | None = None,
                           decision_id: str | None = None) -> list[dict[str, Any]]:
        clauses: list[str] = []; params: list[Any] = []
        if task_id: clauses.append("task_id=?"); params.append(task_id)
        if decision_id: clauses.append("decision_id=?"); params.append(decision_id)
        sql = "SELECT * FROM verifications"
        if clauses: sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT ?"; params.append(min(1000, max(1, limit)))
        with self.connection() as conn: rows = conn.execute(sql, params).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            for key in ("expected_json", "actual_json", "differences_json"):
                item[key.removesuffix("_json")] = json.loads(item.pop(key))
            output.append(item)
        return output

    def event(self, level: str, event_type: str, actor: str, task_id: str | None, message: str, data: dict[str, Any] | None = None) -> int:
        with self.connection() as conn:
            cursor = conn.execute("INSERT INTO events(level,type,actor,task_id,message,data_json,created_at) VALUES(?,?,?,?,?,?,?)",
                                  (level[:20], event_type[:120], actor[:120], task_id, redact_text(message)[:1000], json.dumps(redact(data or {}), ensure_ascii=False), now_iso()))
            return int(cursor.lastrowid)

    def list_events(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.connection() as conn: rows = conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (min(1000, max(1, limit)),)).fetchall()
        out = []
        for row in rows:
            item = dict(row); item["data"] = json.loads(item.pop("data_json")); out.append(item)
        return out

    def alert(self, severity: str, code: str, profile_id: str | None, task_id: str | None, decision_id: str | None,
              message: str, data: dict[str, Any] | None = None) -> int:
        with self.connection() as conn:
            cursor = conn.execute("INSERT INTO alerts(severity,code,profile_id,task_id,decision_id,status,message,data_json,created_at) VALUES(?,?,?,?,?,'open',?,?,?)",
                                  (severity[:20], code[:120], profile_id, task_id, decision_id, redact_text(message)[:1000],
                                   json.dumps(redact(data or {}), ensure_ascii=False), now_iso()))
            return int(cursor.lastrowid)

    def alert_once(self, severity: str, code: str, profile_id: str | None, task_id: str | None,
                   decision_id: str | None, message: str, data: dict[str, Any] | None = None,
                   window_seconds: int = 3600) -> int | None:
        cutoff = (datetime.now(UTC) - timedelta(seconds=max(1, window_seconds))).isoformat(timespec="seconds")
        with self.connection() as conn:
            row = conn.execute(
                "SELECT id FROM alerts WHERE code=? AND COALESCE(profile_id,'')=COALESCE(?, '') "
                "AND status='open' AND created_at>=? ORDER BY id DESC LIMIT 1",
                (code, profile_id, cutoff),
            ).fetchone()
        if row:
            return None
        return self.alert(severity, code, profile_id, task_id, decision_id, message, data)

    def list_alerts(self, limit: int = 100, status: str | None = "open") -> list[dict[str, Any]]:
        sql = "SELECT * FROM alerts"; params: list[Any] = []
        if status: sql += " WHERE status=?"; params.append(status)
        sql += " ORDER BY id DESC LIMIT ?"; params.append(min(1000, max(1, limit)))
        with self.connection() as conn: rows = conn.execute(sql, params).fetchall()
        out=[]
        for row in rows:
            item=dict(row); item["data"]=json.loads(item.pop("data_json")); out.append(item)
        return out

    def ingest_stream_events(self, events: list[dict[str, Any]]) -> dict[str, int]:
        inserted = duplicate = 0
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for event in events:
                    payload = redact(event.get("payload") if isinstance(event.get("payload"), dict) else event)
                    dedupe = str(event.get("dedupe_key") or __import__("hashlib").sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest())
                    cursor = conn.execute("INSERT OR IGNORE INTO stream_events(profile_id,dataset_id,event_time,dedupe_key,payload_json,received_at) VALUES(?,?,?,?,?,?)",
                                          (event.get("profile_id"), str(event.get("dataset_id") or "unknown"), event.get("event_time"), dedupe,
                                           json.dumps(payload, ensure_ascii=False), now_iso()))
                    if cursor.rowcount: inserted += 1
                    else: duplicate += 1
                conn.commit()
            except Exception:
                conn.rollback(); raise
        return {"inserted": inserted, "duplicates": duplicate}

    def list_workers(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connection() as conn: rows = conn.execute("SELECT * FROM workers ORDER BY last_seen_at DESC LIMIT ?", (min(500, max(1, limit)),)).fetchall()
        return [dict(row) for row in rows]

    def count_actions(self, *, task_id: str | None = None, since: str | None = None, family: str | None = None,
                      operations: tuple[str, ...] = ("write", "workflow")) -> int:
        placeholders = ",".join("?" for _ in operations)
        clauses = [f"operation IN ({placeholders})", "allowed=1", "phase='before'"]; params: list[Any] = list(operations)
        if task_id: clauses.append("task_id=?"); params.append(task_id)
        if since: clauses.append("created_at>=?"); params.append(since)
        if family: clauses.append("tool_name IN (SELECT registered_name FROM mcp_tools WHERE family=?)"); params.append(family)
        with self.connection() as conn: return int(conn.execute("SELECT COUNT(*) FROM actions WHERE " + " AND ".join(clauses), params).fetchone()[0])

    def dashboard(self) -> dict[str, Any]:
        today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        with self.connection() as conn:
            task_counts = {row["status"]: row["count"] for row in conn.execute("SELECT status,COUNT(*) count FROM tasks GROUP BY status")}
            decision_counts = {row["status"]: row["count"] for row in conn.execute("SELECT status,COUNT(*) count FROM decisions GROUP BY status")}
            active_workers = conn.execute("SELECT COUNT(*) FROM workers WHERE status='running'").fetchone()[0]
            actions_today = conn.execute("SELECT COUNT(*) FROM actions WHERE created_at>=?", (today,)).fetchone()[0]
            writes_today = conn.execute("SELECT COUNT(*) FROM actions WHERE created_at>=? AND operation IN ('write','workflow') AND allowed=1 AND phase='before'", (today,)).fetchone()[0]
            blocked_today = conn.execute("SELECT COUNT(*) FROM actions WHERE created_at>=? AND allowed=0", (today,)).fetchone()[0]
            open_alerts = conn.execute("SELECT COUNT(*) FROM alerts WHERE status='open'").fetchone()[0]
            catalog = conn.execute("SELECT COUNT(*) count,SUM(CASE WHEN drifted=1 THEN 1 ELSE 0 END) drifted FROM mcp_tools WHERE enabled=1").fetchone()
            latest_cycle = conn.execute("SELECT id FROM cycles ORDER BY created_at DESC LIMIT 1").fetchone()
        return {
            "settings": self.get_settings(), "task_counts": task_counts, "decision_counts": decision_counts,
            "active_workers": active_workers, "actions_today": actions_today, "writes_today": writes_today,
            "blocked_today": blocked_today, "open_alerts": open_alerts,
            "catalog": {"tools": int(catalog["count"] or 0), "drifted": int(catalog["drifted"] or 0)},
            "profiles": self.list_profiles(), "latest_cycle": self.get_cycle(latest_cycle["id"]) if latest_cycle else None,
            "recent_cycles": self.list_cycles(10), "recent_tasks": self.list_tasks(20), "recent_actions": self.list_actions(40),
            "recent_verifications": self.list_verifications(40), "recent_events": self.list_events(30), "alerts": self.list_alerts(30), "workers": self.list_workers(20), "generated_at": now_iso(),
        }

    def integrity_check(self, *, quick: bool = True) -> dict[str, Any]:
        pragma = "quick_check" if quick else "integrity_check"
        with self.connection() as conn:
            rows = [str(row[0]) for row in conn.execute(f"PRAGMA {pragma}").fetchall()]
            foreign_key_errors = [tuple(row) for row in conn.execute("PRAGMA foreign_key_check").fetchall()]
        ok = rows == ["ok"] and not foreign_key_errors
        return {"ok": ok, "pragma": pragma, "messages": rows, "foreign_key_errors": foreign_key_errors}

    def backup_to(self, destination: Path | str) -> dict[str, Any]:
        target = Path(destination)
        if target.resolve() == self.path.resolve():
            raise ValueError("backup destination must differ from the live database")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        temporary.unlink(missing_ok=True)
        with self.connection() as source:
            backup = sqlite3.connect(temporary)
            try:
                source.backup(backup)
                backup.commit()
            finally:
                backup.close()
        check = Store(temporary).integrity_check(quick=False)
        if not check["ok"]:
            temporary.unlink(missing_ok=True)
            raise RuntimeError(f"backup integrity check failed: {check}")
        temporary.replace(target)
        target.chmod(0o600)
        return {"path": str(target), "size": target.stat().st_size, "integrity": check}

    def purge_old(self, retention_days: int) -> dict[str, int]:
        cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat(); deleted = {}
        with self.connection() as conn:
            for table in ("actions", "events", "stream_events"):
                column = "received_at" if table == "stream_events" else "created_at"
                cursor = conn.execute(f"DELETE FROM {table} WHERE {column}<?", (cutoff,)); deleted[table] = cursor.rowcount
        return deleted

    @staticmethod
    def _task_dict(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row); item["write_allowed"] = bool(item["write_allowed"]); item["payload"] = json.loads(item.pop("payload_json"))
        item["result"] = json.loads(item.pop("result_json")) if item.get("result_json") else None; return item

    @staticmethod
    def _decision_dict(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row); item["evidence"] = json.loads(item.pop("evidence_json")); item["payload"] = json.loads(item.pop("payload_json"))
        item["result"] = json.loads(item.pop("result_json")) if item.get("result_json") else None; return item
