from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from amazon_ads_control.db import Store


V1_SCHEMA = """
CREATE TABLE settings (
  key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE tasks (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, kind TEXT NOT NULL,
  status TEXT NOT NULL, created_by TEXT NOT NULL, parent_session_id TEXT,
  worker_session_id TEXT, worker_subagent_id TEXT,
  write_allowed INTEGER NOT NULL DEFAULT 0, payload_json TEXT NOT NULL,
  result_json TEXT, error TEXT, created_at TEXT NOT NULL,
  started_at TEXT, completed_at TEXT
);
CREATE TABLE actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, session_id TEXT,
  plan_key TEXT, actor_role TEXT NOT NULL, phase TEXT NOT NULL,
  tool_name TEXT NOT NULL, operation TEXT NOT NULL, allowed INTEGER NOT NULL,
  success INTEGER, reason TEXT, args_json TEXT NOT NULL,
  result_summary TEXT, duration_ms INTEGER, created_at TEXT NOT NULL
);
CREATE TABLE events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, level TEXT NOT NULL,
  type TEXT NOT NULL, actor TEXT NOT NULL, task_id TEXT,
  message TEXT NOT NULL, data_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE workers (
  session_id TEXT PRIMARY KEY, subagent_id TEXT, parent_session_id TEXT,
  task_id TEXT, role TEXT NOT NULL, status TEXT NOT NULL, model TEXT,
  goal TEXT, last_seen_at TEXT NOT NULL, started_at TEXT NOT NULL,
  stopped_at TEXT
);
"""


class V1MigrationTests(unittest.TestCase):
    def test_existing_v1_database_upgrades_without_reset(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.db"
            conn = sqlite3.connect(path)
            conn.executescript(V1_SCHEMA)
            conn.execute(
                "INSERT INTO settings VALUES(?,?,?)",
                ("mode", json.dumps("autopilot"), "2026-08-01T00:00:00+00:00"),
            )
            conn.execute(
                "INSERT INTO tasks(id,title,kind,status,created_by,write_allowed,payload_json,created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                ("legacy-task", "legacy", "optimization", "planned", "main", 1, "{}", "2026-08-01T00:00:00+00:00"),
            )
            conn.execute(
                "INSERT INTO actions(task_id,session_id,plan_key,actor_role,phase,tool_name,operation,allowed,args_json,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("legacy-task", "old-worker", "legacy-plan", "worker", "before", "mcp_amazon_ads_update", "write", 1, "{}", "2026-08-01T00:00:00+00:00"),
            )
            conn.commit(); conn.close()

            store = Store(path)
            self.assertEqual(store.get_settings()["mode"], "autopilot")
            task = store.get_task("legacy-task")
            self.assertIsNotNone(task)
            actions = store.list_actions(task_id="legacy-task")
            self.assertEqual(len(actions), 1)
            self.assertIsNone(actions[0]["decision_id"])

            conn = sqlite3.connect(path)
            task_columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
            action_columns = {row[1] for row in conn.execute("PRAGMA table_info(actions)")}
            indexes = {row[1] for row in conn.execute("PRAGMA index_list(actions)")}
            conn.close()
            self.assertTrue({"cycle_id", "verifier_session_id", "verifier_subagent_id"} <= task_columns)
            self.assertTrue({"decision_id", "tool_call_id", "reservation_token", "outcome_status", "structured_result"} <= action_columns)
            self.assertIn("idx_actions_decision", indexes)


if __name__ == "__main__":
    unittest.main()
