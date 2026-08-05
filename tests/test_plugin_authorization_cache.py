from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
CLIENT_PATH = ROOT / "hermes-plugin" / "amazon_ads_control" / "client.py"


def load_client():
    name = "amazon_ads_plugin_client_test"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, CLIENT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load Hermes plugin client")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class AuthorizationCacheTests(unittest.TestCase):
    def setUp(self):
        self.client = load_client()
        self.calls: list[tuple[str, str, object]] = []

        def fake_request(method, path, payload, timeout, headers):
            del timeout, headers
            self.calls.append((method, path, payload))
            if path == "/api/agent/tool-check":
                return {
                    "allowed": True,
                    "operation": "write",
                    "task_id": "task-1",
                    "decision_id": "decision-1",
                    "plan_key": "plan-1",
                    "reservation_token": "reservation-1",
                }
            if path == "/api/agent/catalog-sync":
                return {"tool_count": 10, "created": 10}
            return {"ok": True}

        self.client._request = fake_request

    def test_call_id_is_bound_to_session_tool_and_arguments(self):
        check = {
            "tool_name": "mcp_amazon_ads_update_bid",
            "args": {"targetId": "T1", "bid": 1.25},
            "session_id": "session-1",
            "tool_call_id": "call-1",
        }
        self.client.request("POST", "/api/agent/tool-check", check)
        self.client.request(
            "POST",
            "/api/agent/tool-result",
            {
                **check,
                "result": {"ok": True},
                "task_id": "attacker-task",
                "decision_id": "attacker-decision",
                "reservation_token": "attacker-token",
            },
        )
        sent = self.calls[-1][2]
        self.assertEqual(sent["task_id"], "task-1")
        self.assertEqual(sent["decision_id"], "decision-1")
        self.assertEqual(sent["plan_key"], "plan-1")
        self.assertEqual(sent["reservation_token"], "reservation-1")
        self.assertNotIn("authorization_cache_miss", sent)

    def test_call_id_mismatch_fails_closed_and_cannot_be_reused(self):
        check = {
            "tool_name": "mcp_amazon_ads_update_bid",
            "args": {"targetId": "T1", "bid": 1.25},
            "session_id": "session-1",
            "tool_call_id": "call-1",
        }
        self.client.request("POST", "/api/agent/tool-check", check)
        mismatch = {
            **check,
            "args": {"targetId": "T2", "bid": 1.25},
            "result": {"ok": True},
            "decision_id": "stale",
            "reservation_token": "stale",
        }
        self.client.request("POST", "/api/agent/tool-result", mismatch)
        sent = self.calls[-1][2]
        self.assertTrue(sent["authorization_cache_miss"])
        self.assertNotIn("decision_id", sent)
        self.assertNotIn("reservation_token", sent)

        self.client.request(
            "POST",
            "/api/agent/tool-result",
            {**check, "result": {"ok": True}},
        )
        self.assertTrue(self.calls[-1][2]["authorization_cache_miss"])

    def test_fallback_is_argument_hash_scoped_and_one_shot(self):
        first = {
            "tool_name": "mcp_amazon_ads_update_bid",
            "args": {"targetId": "T1", "bid": 1.25},
            "session_id": "session-1",
        }
        second = {
            "tool_name": "mcp_amazon_ads_update_bid",
            "args": {"targetId": "T2", "bid": 1.5},
            "session_id": "session-1",
        }
        self.client.request("POST", "/api/agent/tool-check", first)
        self.client.request("POST", "/api/agent/tool-check", second)

        self.client.request(
            "POST", "/api/agent/tool-result", {**second, "result": {"ok": True}}
        )
        self.assertEqual(self.calls[-1][2]["decision_id"], "decision-1")

        self.client.request(
            "POST", "/api/agent/tool-result", {**first, "result": {"ok": True}}
        )
        self.assertEqual(self.calls[-1][2]["decision_id"], "decision-1")

        self.client.request(
            "POST", "/api/agent/tool-result", {**first, "result": {"ok": True}}
        )
        self.assertTrue(self.calls[-1][2]["authorization_cache_miss"])

    def test_transient_delivery_failure_releases_lease_for_outbox_retry(self):
        check = {
            "tool_name": "mcp_amazon_ads_update_bid",
            "args": {"targetId": "T1", "bid": 1.25},
            "session_id": "session-1",
            "tool_call_id": "call-1",
        }
        self.client.request("POST", "/api/agent/tool-check", check)
        successful_request = self.client._request

        def unavailable(method, path, payload, timeout, headers):
            del method, path, payload, timeout, headers
            return {"error": "control_plane_unavailable"}

        self.client._request = unavailable
        response = self.client.request(
            "POST", "/api/agent/tool-result", {**check, "result": {"ok": True}}
        )
        self.assertEqual(response["error"], "control_plane_unavailable")
        self.assertEqual(self.client._cache_stats()["pending"], 1)

        self.client._request = successful_request
        self.client.request(
            "POST", "/api/agent/tool-result", {**check, "result": {"ok": True}}
        )
        self.assertEqual(self.calls[-1][2]["decision_id"], "decision-1")
        self.assertEqual(self.client._cache_stats()["pending"], 0)

    def test_concurrent_lease_cannot_attach_same_authorization_twice(self):
        check = {
            "tool_name": "mcp_amazon_ads_update_bid",
            "args": {"targetId": "T1", "bid": 1.25},
            "session_id": "session-1",
            "tool_call_id": "call-1",
        }
        self.client.request("POST", "/api/agent/tool-check", check)
        first, lease = self.client._prepare_payload(
            "POST", "/api/agent/tool-result", {**check, "result": {"ok": True}}
        )
        second, second_lease = self.client._prepare_payload(
            "POST", "/api/agent/tool-result", {**check, "result": {"ok": True}}
        )
        self.assertEqual(first["decision_id"], "decision-1")
        self.assertIsNotNone(lease)
        self.assertTrue(second["authorization_cache_miss"])
        self.assertIsNone(second_lease)
        self.client._release_authorization(lease)

    def test_identical_fallback_calls_can_hold_distinct_concurrent_leases(self):
        check = {
            "tool_name": "mcp_amazon_ads_update_bid",
            "args": {"targetId": "T1", "bid": 1.25},
            "session_id": "session-1",
        }
        self.client.request("POST", "/api/agent/tool-check", check)
        self.client.request("POST", "/api/agent/tool-check", check)
        first, first_lease = self.client._prepare_payload(
            "POST", "/api/agent/tool-result", {**check, "result": {"ok": True}}
        )
        second, second_lease = self.client._prepare_payload(
            "POST", "/api/agent/tool-result", {**check, "result": {"ok": True}}
        )
        self.assertEqual(first["decision_id"], "decision-1")
        self.assertEqual(second["decision_id"], "decision-1")
        self.assertIsNotNone(first_lease)
        self.assertIsNotNone(second_lease)
        self.assertNotEqual(first_lease[2], second_lease[2])
        self.client._commit_authorization(first_lease)
        self.client._commit_authorization(second_lease)
        self.assertEqual(self.client._cache_stats()["pending"], 0)

    def test_expired_authorization_is_not_attached(self):
        check = {
            "tool_name": "mcp_amazon_ads_update_bid",
            "args": {"targetId": "T1", "bid": 1.25},
            "session_id": "session-1",
            "tool_call_id": "call-1",
        }
        self.client.request("POST", "/api/agent/tool-check", check)
        with self.client._AUTH_LOCK:
            self.client._AUTH_BY_CALL["call-1"]["created_at"] -= 1000
        self.client.request(
            "POST", "/api/agent/tool-result", {**check, "result": {"ok": True}}
        )
        self.assertTrue(self.calls[-1][2]["authorization_cache_miss"])

    def test_session_end_and_worker_stop_clear_pending_authorizations(self):
        check = {
            "tool_name": "mcp_amazon_ads_update_bid",
            "args": {"targetId": "T1", "bid": 1.25},
            "session_id": "session-1",
            "tool_call_id": "call-1",
        }
        self.client.request("POST", "/api/agent/tool-check", check)
        self.client.request(
            "POST",
            "/api/agent/session-event",
            {"session_id": "session-1", "state": "ended"},
        )
        self.assertEqual(self.client._cache_stats()["pending"], 0)

        self.client.request("POST", "/api/agent/tool-check", check)
        self.client.request(
            "POST",
            "/api/agent/worker-stop",
            {"worker_session_id": "session-1", "status": "failed"},
        )
        self.assertEqual(self.client._cache_stats()["pending"], 0)

    def test_runtime_status_reports_catalog_and_cache_protocol(self):
        self.client.request("POST", "/api/agent/catalog-sync", {"tools": [{}]})
        self.client.request(
            "POST",
            "/api/agent/runtime-status",
            {"component": "hermes-plugin", "state": {"result_outbox": {"pending": 0}}},
        )
        sent = self.calls[-1][2]
        self.assertEqual(sent["state"]["readiness_protocol"], 1)
        self.assertTrue(sent["state"]["catalog_sync"]["ok"])
        self.assertEqual(sent["state"]["catalog_sync"]["tool_count"], 10)
        self.assertEqual(sent["state"]["authorization_cache"]["protocol"], 1)


if __name__ == "__main__":
    unittest.main()
