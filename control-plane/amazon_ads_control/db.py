from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import threading
import uuid
from typing import Any, Iterator

from .policy import redact, redact_text

UTC = timezone.utc


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self.connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    parent_session_id TEXT,
                    worker_session_id TEXT,
                    worker_subagent_id TEXT,
                    write_allowed INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_status_created ON tasks(status, created_at DESC);
                CREATE TABLE IF NOT EXISTS actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT,
                    session_id TEXT,
                    plan_key TEXT,
                    actor_role TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    allowed INTEGER NOT NULL,
                    success INTEGER,
                    reason TEXT,
                    args_json TEXT NOT NULL,
                    result_summary TEXT,
                    duration_ms INTEGER,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id)
                );
                CREATE INDEX IF NOT EXISTS idx_actions_created ON actions(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_actions_task ON actions(task_id, created_at DESC);
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
                """
            )
            action_columns = {row[1] for row in conn.execute("PRAGMA table_info(actions)")}
            if "plan_key" not in action_columns:
                conn.execute("ALTER TABLE actions ADD COLUMN plan_key TEXT")
            defaults = {
                "mode": "autopilot",
                "execution_enabled": True,
                "max_bid_change_pct": 15,
                "max_budget_change_pct": 20,
                "max_actions_per_task": 50,
                "max_actions_per_day": 250,
                "block_deletes": True,
                "require_planned_writes": True,
            }
            for key, value in defaults.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings(key,value,updated_at) VALUES(?,?,?)",
                    (key, json.dumps(value), now_iso()),
                )

    def get_settings(self) -> dict[str, Any]:
        with self.connection() as conn:
            return {row["key"]: json.loads(row["value"]) for row in conn.execute("SELECT key,value FROM settings")}

    def update_settings(self, updates: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "mode", "execution_enabled", "max_bid_change_pct", "max_budget_change_pct",
            "max_actions_per_task", "max_actions_per_day", "block_deletes", "require_planned_writes",
        }
        unknown = set(updates) - allowed
        if unknown:
            raise ValueError(f"unknown settings: {', '.join(sorted(unknown))}")
        if "mode" in updates and updates["mode"] not in {"autopilot", "observe", "paused"}:
            raise ValueError("mode must be autopilot, observe, or paused")
        for key in ("max_bid_change_pct", "max_budget_change_pct"):
            if key in updates and not 1 <= int(updates[key]) <= 100:
                raise ValueError(f"{key} must be between 1 and 100")
        for key in ("max_actions_per_task", "max_actions_per_day"):
            if key in updates and not 1 <= int(updates[key]) <= 10000:
                raise ValueError(f"{key} must be between 1 and 10000")
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for key, value in updates.items():
                    conn.execute(
                        "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                        (key, json.dumps(value), now_iso()),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        self.event("info", "settings.updated", "operator", None, "Control settings updated", updates)
        return self.get_settings()

    def create_task(self, title: str, kind: str, created_by: str, parent_session_id: str | None,
                    write_allowed: bool, payload: dict[str, Any]) -> dict[str, Any]:
        task_id = uuid.uuid4().hex[:16]
        created = now_iso()
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO tasks(id,title,kind,status,created_by,parent_session_id,write_allowed,payload_json,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (task_id, title[:240], kind[:40], "planned", created_b