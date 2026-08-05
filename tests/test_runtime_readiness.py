from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import unittest

from amazon_ads_control.runtime_readiness import (
    authorize_with_runtime_gate,
    create_task_with_runtime_gate,
    readiness_snapshot,
)
from helpers import Environment, WRITE_TARGET

UTC = timezone.utc


class RuntimeReadinessTests(unittest.TestCase):
    def setUp(self):
        self.env = Environment()
        self.env.sync_basic_catalog()

    def tearDown(self):
        self.env.close()

    def heartbeat(self, *, outbox=None, catalog_sync=None):
        return self.env.store.record_runtime_status(
            "hermes-plugin",
            {
                "readiness_protocol": 1,
                "resources": {"tier": "2c2g", "cpu_count": 2, "memory_total_mb": 2048},
                "result_outbox": outbox or {"pending": 0, "bytes": 0, "over_limit": False},
                "catalog_sync": catalog_sync or {"ok": True, "tool_count": 6},
            },
        )

    def test_configuration_ready_and_writable_are_distinct(self):
        self.heartbeat()
        observe = readiness_snapshot(self.env.store)
        self.assertTrue(observe["ready"])
        self.assertFalse(observe["configured"])
        self.assertFalse(observe["writable"])
        self.assertEqual(observe["operational_state"], "ready")

        self.env.store.update_settings({"mode": "autopilot", "execution_enabled": True})
        autopilot = readiness_snapshot(self.env.store)
        self.assertTrue(autopilot["configured"])
        self.assertTrue(autopilot["ready"])
        self.assertTrue(autopilot["writable"])
        self.assertEqual(autopilot["operational_state"], "writable")

    def test_missing_or_stale_heartbeat_blocks_autopilot(self):
        self.env.store.update_settings({"mode": "autopilot", "execution_enabled": True})
        missing = readiness_snapshot(self.env.store)
        self.assertFalse(missing["writable"])
        self.assertIn("hermes_plugin_present", missing["blocking_checks"])

        self.heartbeat()
        stale = (datetime.now(UTC) - timedelta(hours=1)).isoformat(timespec="seconds")
        with self.env.store.connection() as conn:
            conn.execute(
                "UPDATE runtime_status SET updated_at=? WHERE component='hermes-plugin'",
                (stale,),
            )
        state = readiness_snapshot(self.env.store)
        self.assertFalse(state["writable"])
        self.assertIn("hermes_plugin_heartbeat_fresh", state["blocking_checks"])
        self.assertEqual(state["operational_state"], "blocked")

    def test_legacy_plugin_protocol_cannot_enable_writes(self):
        self.env.store.update_settings({"mode": "autopilot", "execution_enabled": True})
        self.env.store.record_runtime_status(
            "hermes-plugin",
            {
                "result_outbox": {"pending": 0, "bytes": 0, "over_limit": False},
                "catalog_sync": {"ok": True, "tool_count": 6},
            },
        )
        state = readiness_snapshot(self.env.store)
        self.assertFalse(state["writable"])
        self.assertIn("hermes_plugin_protocol_supported", state["blocking_checks"])

    def test_outbox_backlog_and_catalog_sync_failure_block_writes(self):
        self.env.store.update_settings({"mode": "autopilot", "execution_enabled": True})
        self.heartbeat(
            outbox={"pending": 1000, "bytes": 32 * 1024 * 1024, "over_limit": False},
            catalog_sync={"ok": False, "error": "registry unavailable"},
        )
        state = readiness_snapshot(self.env.store)
        self.assertFalse(state["writable"])
        self.assertIn("catalog_sync_healthy", state["blocking_checks"])
        self.assertIn(
            "result_outbox_backlog_below_threshold", state["blocking_checks"]
        )

    def test_pending_callback_threshold_is_a_hard_write_gate(self):
        self.env.store.update_settings({"mode": "autopilot", "execution_enabled": True})
        self.heartbeat()
        original_dashboard = self.env.store.dashboard

        def dashboard():
            value = original_dashboard()
            value["pending_callbacks"] = 999
            return value

        with patch.object(self.env.store, "dashboard", side_effect=dashboard):
            state = readiness_snapshot(self.env.store)
        self.assertFalse(state["writable"])
        self.assertIn("pending_callbacks_below_threshold", state["blocking_checks"])

    def test_database_write_probe_failure_is_reported_not_raised(self):
        self.heartbeat()
        with patch(
            "amazon_ads_control.runtime_readiness._database_writable",
            return_value=(False, "readonly"),
        ):
            state = readiness_snapshot(self.env.store)
        self.assertFalse(state["service_ready"])
        self.assertEqual(state["observed"]["database_write_error"], "readonly")

    def test_network_boundary_gate_blocks_before_reservation(self):
        self.env.store.update_settings({"mode": "autopilot", "execution_enabled": True})
        result = authorize_with_runtime_gate(
            self.env.service,
            {
                "tool_name": WRITE_TARGET["registered_name"],
                "args": {"targetId": "t1", "bid": 0.75},
                "session_id": "executor-without-heartbeat",
                "tool_call_id": "call-1",
            },
        )
        self.assertFalse(result["allowed"])
        self.assertEqual(result["operation"], "write")
        self.assertIn("runtime readiness gate blocked write", result["reason"])
        actions = self.env.store.list_actions(limit=10)
        self.assertEqual(actions[0]["phase"], "before")
        self.assertFalse(actions[0]["allowed"])
        self.assertIsNone(actions[0]["reservation_token"])

    def test_new_write_task_is_rejected_when_runtime_is_blocked(self):
        self.env.store.update_settings({"mode": "autopilot", "execution_enabled": True})
        with self.assertRaisesRegex(ValueError, "runtime readiness gate blocks"):
            create_task_with_runtime_gate(
                self.env.service,
                {"title": "must not be created", "decision_ids": ["missing"]},
                "hermes-main",
            )


if __name__ == "__main__":
    unittest.main()
