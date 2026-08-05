from __future__ import annotations

from pathlib import Path
import json
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from amazon_ads_control.api import build_server
from amazon_ads_control.catalog import descriptor_from_payload
from amazon_ads_control.config import Settings
from amazon_ads_control.db import Store
from amazon_ads_control.security import hash_password
from helpers import WRITE_TARGET


class ApiRuntimeReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(
            host="127.0.0.1",
            port=0,
            db_path=Path(self.temp.name) / "state.db",
            public_origin="",
            control_password_hash=hash_password("test password"),
            agent_token="a" * 48,
            session_ttl_seconds=3600,
            max_sessions=8,
            retention_days=30,
            allow_remote_bind=False,
        )
        self.server = build_server(self.settings)
        self.store: Store = self.server.RequestHandlerClass.app.store
        self.store.sync_catalog([descriptor_from_payload(WRITE_TARGET)])
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, method: str, path: str, payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        headers = {"Authorization": f"Bearer {self.settings.agent_token}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.base + path, data=body, method=method, headers=headers)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def heartbeat(self):
        self.store.record_runtime_status(
            "hermes-plugin",
            {
                "readiness_protocol": 1,
                "result_outbox": {"pending": 0, "bytes": 0, "over_limit": False},
                "catalog_sync": {"ok": True, "tool_count": 1},
            },
        )

    def test_health_and_write_boundary_fail_closed_then_recover(self):
        status, state = self.request("GET", "/health/ready")
        self.assertEqual(status, 200)
        self.assertEqual(state["operational_state"], "degraded")
        self.assertFalse(state["configured"])

        self.store.update_settings({"mode": "autopilot", "execution_enabled": True})
        status, state = self.request("GET", "/health/ready")
        self.assertEqual(status, 503)
        self.assertTrue(state["blocked"])
        self.assertIn("hermes_plugin_present", state["blocking_checks"])

        status, denied = self.request(
            "POST",
            "/api/agent/tool-check",
            {
                "tool_name": WRITE_TARGET["registered_name"],
                "args": {"targetId": "t1", "bid": 0.75},
                "session_id": "executor-missing-heartbeat",
                "tool_call_id": "runtime-1",
            },
        )
        self.assertEqual(status, 403)
        self.assertIn("runtime readiness gate blocked write", denied["reason"])
        self.assertFalse(denied["allowed"])

        status, task_error = self.request(
            "POST",
            "/api/agent/tasks",
            {"title": "blocked before validation", "decision_ids": ["missing"]},
        )
        self.assertEqual(status, 400)
        self.assertIn("runtime readiness gate blocks", task_error["error"])

        self.heartbeat()
        status, state = self.request("GET", "/health/ready")
        self.assertEqual(status, 200)
        self.assertTrue(state["writable"])
        self.assertEqual(state["operational_state"], "writable")

        status, denied = self.request(
            "POST",
            "/api/agent/tool-check",
            {
                "tool_name": WRITE_TARGET["registered_name"],
                "args": {"targetId": "t1", "bid": 0.75},
                "session_id": "not-a-bound-executor",
                "tool_call_id": "runtime-2",
            },
        )
        self.assertEqual(status, 403)
        self.assertNotIn("runtime readiness gate", denied["reason"])
        self.assertIn("bound executor", denied["reason"])


if __name__ == "__main__":
    unittest.main()
