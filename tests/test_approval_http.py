from __future__ import annotations

from http.cookiejar import CookieJar
from pathlib import Path
import json
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPCookieProcessor

from amazon_ads_control.api import build_server
from amazon_ads_control.config import Settings
from amazon_ads_control.security import hash_password

CREATE_CAMPAIGN = {
    "registered_name": "mcp_amazon_ads_campaign_management_create_campaign",
    "native_name": "campaign_management-create_campaign",
    "schema": {
        "description": "Create campaign",
        "parameters": {
            "type": "object",
            "required": ["campaigns"],
            "properties": {
                "campaigns": {
                    "type": "array", "minItems": 1, "maxItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["name", "budget"],
                        "properties": {"name": {"type": "string"}, "budget": {"type": "number"}},
                    },
                }
            },
        },
    },
}


class ApprovalHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.password = "correct horse battery staple"
        settings = Settings(
            host="127.0.0.1", port=0, db_path=Path(self.temp.name) / "state.db",
            public_origin="http://127.0.0.1",
            control_password_hash=hash_password(self.password),
            agent_token="a" * 48, operator_token="b" * 48,
            session_ttl_seconds=3600, max_sessions=4, retention_days=30,
            allow_remote_bind=False,
        )
        self.server = build_server(settings)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.jar = CookieJar()
        self.browser = build_opener(HTTPCookieProcessor(self.jar))
        self.csrf = ""

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.temp.cleanup()

    def request(self, path, method="GET", data=None, headers=None, opener=None):
        body = None if data is None else json.dumps(data).encode()
        request = Request(
            self.base + path, data=body, method=method,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            with (opener or build_opener()).open(request) as response:
                return response.status, json.loads(response.read().decode())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def login(self):
        status, data = self.request(
            "/api/login", "POST", {"password": self.password}, opener=self.browser,
        )
        self.assertEqual(status, 200)
        self.csrf = data["csrf"]

    def test_agent_can_request_but_cannot_approve(self):
        agent = {"Authorization": "Bearer " + "a" * 48}
        status, _ = self.request(
            "/api/agent/catalog-sync", "POST", {"tools": [CREATE_CAMPAIGN]}, agent,
        )
        self.assertEqual(status, 200)
        status, result = self.request(
            "/api/agent/managed-plans", "POST", {
                "title": "Create approved campaign",
                "profile": {"profile_id": "p1", "marketplace": "US", "currency": "USD"},
                "actions": [{
                    "tool_name": CREATE_CAMPAIGN["registered_name"],
                    "action_type": "create_campaign",
                    "entity_type": "campaign",
                    "entity_id": "planned:http-test",
                    "arguments": {"campaigns": [{"name": "HTTP Approved", "budget": 20}]},
                    "expected_state": {"name": "HTTP Approved", "budget": 20},
                    "maximum_daily_budget": 20,
                }],
            }, agent,
        )
        self.assertEqual(status, 201, result)
        approval = result["approval"]
        self.assertEqual(approval["status"], "pending")

        phrase = f"APPROVE {approval['id']} {approval['payload_hash'][:12]}"
        status, denied = self.request(
            f"/api/operator/approvals/{approval['id']}/approve", "POST",
            {"payload_hash": approval["payload_hash"], "confirmation": phrase}, agent,
        )
        self.assertEqual(status, 401)
        self.assertEqual(denied["error"], "invalid_operator_token")

        self.login()
        status, no_csrf = self.request(
            f"/api/approvals/{approval['id']}/approve", "POST",
            {"payload_hash": approval["payload_hash"], "confirmation": phrase},
            {"Origin": "http://127.0.0.1"}, self.browser,
        )
        self.assertEqual(status, 403)
        self.assertIn("error", no_csrf)

        status, approved = self.request(
            f"/api/approvals/{approval['id']}/approve", "POST",
            {"payload_hash": approval["payload_hash"], "confirmation": phrase},
            {"Origin": "http://127.0.0.1", "X-CSRF-Token": self.csrf}, self.browser,
        )
        self.assertEqual(status, 200, approved)
        self.assertEqual(approved["status"], "approved")


if __name__ == "__main__":
    unittest.main()
