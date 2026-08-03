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
                (task_id, title[:240], kind[:40], "planned", created_by[:80], parent_session_id,
                 int(write_allowed), json.dumps(payload, ensure_ascii=False), created),
            )
        self.event("info", "task.created", created_by, task_id, f"Task created: {title}", {"kind": kind, "write_allowed": write_allowed})
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self._task_dict(row) if row else None

    def list_tasks(self, limit: int = 100, status: str | None = None) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))
        sql, params = "SELECT * FROM tasks", []
        if status:
            sql += " WHERE status=?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._task_dict(row) for row in rows]

    def bind_worker(self, task_id: str, parent_session_id: str | None, worker_session_id: str,
                    worker_subagent_id: str | None, goal: str, role: str = "worker", model: str | None = None) -> dict[str, Any]:
        started = now_iso()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not task:
                conn.rollback()
                raise KeyError("task not found")
            if task["status"] not in {"planned", "queued", "running"}:
                conn.rollback()
                raise ValueError(f"task cannot bind worker from status {task['status']}")
            conn.execute(
                "UPDATE tasks SET status='running', parent_session_id=COALESCE(parent_session_id,?), "
                "worker_session_id=?, worker_subagent_id=?, started_at=COALESCE(started_at,?) WHERE id=?",
                (parent_session_id, worker_session_id, worker_subagent_id, started, task_id),
            )
            conn.execute(
                "INSERT INTO workers(session_id,subagent_id,parent_session_id,task_id,role,status,model,goal,last_seen_at,started_at) "
                "VALUES(?,?,?,?,?,'running',?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET "
                "subagent_id=excluded.subagent_id,parent_session_id=excluded.parent_session_id,task_id=excluded.task_id,"
                "role=excluded.role,status='running',model=excluded.model,goal=excluded.goal,last_seen_at=excluded.last_seen_at",
                (worker_session_id, worker_subagent_id, parent_session_id, task_id, role, model, goal[:4000], started, started),
            )
            conn.commit()
        self.event("info", "worker.bound", role, task_id, "Hermes worker bound to task", {"session_id": worker_session_id, "subagent_id": worker_subagent_id})
        return self.get_task(task_id)

    def worker_for_session(self, session_id: str | None) -> dict[str, Any] | None:
        if not session_id:
            return None
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM workers WHERE session_id=? AND status='running'", (session_id,)).fetchone()
        return dict(row) if row else None

    def finish_worker(self, session_id: str, status: str, summary: str | None = None,
                      duration_ms: int | None = None, verification: dict[str, Any] | None = None) -> None:
        stopped = now_iso()
        task_status = "completed" if status == "completed" else "failed"
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            worker = conn.execute("SELECT task_id FROM workers WHERE session_id=?", (session_id,)).fetchone()
            conn.execute("UPDATE workers SET status=?,stopped_at=?,last_seen_at=? WHERE session_id=?", (status, stopped, stopped, session_id))
            if worker and worker["task_id"]:
                conn.execute(
                    "UPDATE tasks SET status=?,result_json=?,error=?,completed_at=? WHERE id=?",
                    (task_status, json.dumps({"summary": redact_text(summary or ""), "duration_ms": duration_ms,
                                                  "verification": redact(verification or {})}, ensure_ascii=False),
                     None if task_status == "completed" else redact_text(summary or status), stopped, worker["task_id"]),
                )
            conn.commit()
        self.event("info" if status == "completed" else "error", "worker.stopped", "worker", worker["task_id"] if worker else None,
                   f"Worker {status}", {"session_id": session_id, "duration_ms": duration_ms,
                                                "verification": redact(verification or {})})

    def record_action(self, *, task_id: str | None, session_id: str | None, actor_role: str,
                      phase: str, tool_name: str, operation: str, allowed: bool,
                      plan_key: str | None = None,
                      args: dict[str, Any], success: bool | None = None, reason: str | None = None,
                      result_summary: str | None = None, duration_ms: int | None = None) -> int:
        with self.connection() as conn:
            cursor = conn.execute(
                "INSERT INTO actions(task_id,session_id,plan_key,actor_role,phase,tool_name,operation,allowed,success,reason,args_json,result_summary,duration_ms,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (task_id, session_id, plan_key, actor_role, phase, tool_name[:240], operation, int(allowed),
                 None if success is None else int(success), reason, json.dumps(args, ensure_ascii=False),
                 result_summary[:4000] if result_summary else None, duration_ms, now_iso()),
            )
            return int(cursor.lastrowid)

    def successful_plan_exists(self, task_id: str, plan_key: str) -> bool:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM actions WHERE task_id=? AND plan_key=? AND phase='after' AND success=1 LIMIT 1",
                (task_id, plan_key),
            ).fetchone()
        return bool(row)

    def touch_worker(self, session_id: str | None) -> None:
        if not session_id:
            return
        with self.connection() as conn:
            conn.execute("UPDATE workers SET last_seen_at=? WHERE session_id=? AND status='running'", (now_iso(), session_id))

    def list_actions(self, limit: int = 200, task_id: str | None = None) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 1000))
        sql, params = "SELECT * FROM actions", []
        if task_id:
            sql += " WHERE task_id=?"
            params.append(task_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["allowed"] = bool(item["allowed"])
            item["success"] = None if item["success"] is None else bool(item["success"])
            item["args"] = json.loads(item.pop("args_json"))
            out.append(item)
        return out

    def event(self, level: str, event_type: str, actor: str, task_id: str | None,
              message: str, data: dict[str, Any] | None = None) -> int:
        with self.connection() as conn:
            cursor = conn.execute(
                "INSERT INTO events(level,type,actor,task_id,message,data_json,created_at) VALUES(?,?,?,?,?,?,?)",
                (level[:20], event_type[:120], actor[:120], task_id, redact_text(message)[:1000],
                 json.dumps(redact(data or {}), ensure_ascii=False), now_iso()),
            )
            return int(cursor.lastrowid)

    def list_events(self, limit: int = 200) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 1000))
        with self.connection() as conn:
            rows = conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["data"] = json.loads(item.pop("data_json"))
            out.append(item)
        return out

    def list_workers(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute("SELECT * FROM workers ORDER BY last_seen_at DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall()
        return [dict(row) for row in rows]

    def count_actions(self, *, task_id: str | None = None, since: str | None = None) -> int:
        clauses, params = [], []
        if task_id:
            clauses.append("task_id=?")
            params.append(task_id)
        if since:
            clauses.append("created_at>=?")
            params.append(since)
        sql = "SELECT COUNT(*) FROM actions WHERE operation='write' AND allowed=1"
        if clauses:
            sql += " AND " + " AND ".join(clauses)
        with self.connection() as conn:
            return int(conn.execute(sql, params).fetchone()[0])

    def dashboard(self) -> dict[str, Any]:
        today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        with self.connection() as conn:
            counts = {
                row["status"]: row["count"]
                for row in conn.execute("SELECT status,COUNT(*) AS count FROM tasks GROUP BY status")
            }
            active_workers = conn.execute("SELECT COUNT(*) FROM workers WHERE status='running'").fetchone()[0]
            actions_today = conn.execute("SELECT COUNT(*) FROM actions WHERE created_at>=?", (today,)).fetchone()[0]
            writes_today = conn.execute("SELECT COUNT(*) FROM actions WHERE created_at>=? AND operation='write' AND allowed=1", (today,)).fetchone()[0]
            blocked_today = conn.execute("SELECT COUNT(*) FROM actions WHERE created_at>=? AND allowed=0", (today,)).fetchone()[0]
        return {
            "settings": self.get_settings(),
            "counts": counts,
            "active_workers": active_workers,
            "actions_today": actions_today,
            "writes_today": writes_today,
            "blocked_today": blocked_today,
            "recent_tasks": self.list_tasks(20),
            "recent_actions": self.list_actions(30),
            "recent_events": self.list_events(30),
            "workers": self.list_workers(20),
            "generated_at": now_iso(),
        }

    def purge_old(self, retention_days: int) -> dict[str, int]:
        cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
        deleted = {}
        with self.connection() as conn:
            for table in ("actions", "events"):
                cursor = conn.execute(f"DELETE FROM {table} WHERE created_at<?", (cutoff,))
                deleted[table] = cursor.rowcount
        return deleted

    @staticmethod
    def _task_dict(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["write_allowed"] = bool(item["write_allowed"])
        item["payload"] = json.loads(item.pop("payload_json"))
        item["result"] = json.loads(item.pop("result_json")) if item.get("result_json") else None
        return item
