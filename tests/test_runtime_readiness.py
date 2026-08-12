from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import unittest

from amazon_ads_control.runtime_readiness import (
    authorize_with_runtime_gate,
    create_task_with_runtime_gate,
    gateway_is_idle,
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

    def _seed_active_worker(self):
        # Create a plan + task and bind a running executor so the activity
        # tables (tasks/decisions/workers) show genuine in-flight work.
        # The control plane should then consider the gateway ACTIVE and
        # the heartbeat_fresh check must apply normally.
        snapshot, _ = self.env.plan(self.env.one_target_snapshot()[0]) if False else (None, None)
        # Use the canonical one_decision_task helper instead, which creates a
        # real task in status='planned' with a bound executor.
        cycle, task, decision = self.env.one_decision_task()
        self.env.service.bind_worker({
            "task_id": task["id"],
            "worker_session_id": "exec-stale-heartbeat",
            "role": "executor",
            "goal": f"[ads-task:{task['id']}] [ads-role:executor]",
        })
        return task

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

    def test_missing_heartbeat_always_blocks_autopilot(self):
        """A completely absent plugin row must block autopilot regardless of
        idle state — the heartbeat check still requires hermes_plugin_present
        even when no LLM traffic is in flight, because the plugin has never
        proven it can talk to the control plane."""
        self.env.store.update_settings({"mode": "autopilot", "execution_enabled": True})
        missing = readiness_snapshot(self.env.store)
        self.assertFalse(missing["writable"])
        self.assertIn("hermes_plugin_present", missing["blocking_checks"])
        # Even with an active task, "missing" still blocks.
        self._seed_active_worker()
        still_missing = readiness_snapshot(self.env.store)
        self.assertFalse(still_missing["writable"])
        self.assertIn("hermes_plugin_present", still_missing["blocking_checks"])

    def test_stale_heartbeat_blocks_autopilot_when_gateway_is_active(self):
        """When the gateway has an active worker / in-flight task, a stale
        heartbeat MUST still block autopilot — the gateway should be talking
        to us, and the absence of fresh heartbeats is a real fault signal."""
        self.env.store.update_settings({"mode": "autopilot", "execution_enabled": True})
        task = self._seed_active_worker()
        self.heartbeat()
        # Force the heartbeat to be 1 hour old.
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
        self.assertFalse(state["observed"]["gateway_idle"])
        self.assertFalse(state["observed"]["heartbeat_idle_exempted"])

    def test_idle_gateway_exempts_heartbeat_fresh(self):
        """When the gateway is demonstrably idle (no in-flight tasks,
        decisions, workers, approvals, or hermes sessions, and the most
        recent activity is older than the idle window), autopilot must
        pass the readiness gate even if the plugin heartbeat is stale.

        This is the production idle-install scenario: hermes-studio is
        running but no chat session is attached, so pre_llm_call never
        fires and the heartbeat naturally goes stale. The fix distinguishes
        "idle but healthy" from "active but stale".
        """
        self.env.store.update_settings({"mode": "autopilot", "execution_enabled": True})
        self.heartbeat()
        stale = (datetime.now(UTC) - timedelta(hours=1)).isoformat(timespec="seconds")
        with self.env.store.connection() as conn:
            conn.execute(
                "UPDATE runtime_status SET updated_at=? WHERE component='hermes-plugin'",
                (stale,),
            )
        state = readiness_snapshot(self.env.store)
        self.assertTrue(state["observed"]["gateway_idle"])
        self.assertTrue(state["writable"])
        self.assertEqual(state["operational_state"], "writable")
        self.assertNotIn("hermes_plugin_heartbeat_fresh", state["blocking_checks"])
        self.assertTrue(state["checks"]["hermes_plugin_heartbeat_fresh"])
        self.assertTrue(state["observed"]["heartbeat_idle_exempted"])

    def test_idle_exemption_does_not_weaken_other_checks(self):
        """Even when the heartbeat is idle-exempted, every other readiness
        check still gates writes. An idle gateway with a broken outbox or
        a corrupt catalog must still be blocked."""
        self.env.store.update_settings({"mode": "autopilot", "execution_enabled": True})
        # Idle state (no in-flight activity) but other checks failing.
        self.heartbeat(
            outbox={"pending": 1000, "bytes": 32 * 1024 * 1024, "over_limit": True},
            catalog_sync={"ok": False, "error": "registry unavailable"},
        )
        stale = (datetime.now(UTC) - timedelta(hours=1)).isoformat(timespec="seconds")
        with self.env.store.connection() as conn:
            conn.execute(
                "UPDATE runtime_status SET updated_at=? WHERE component='hermes-plugin'",
                (stale,),
            )
        state = readiness_snapshot(self.env.store)
        self.assertTrue(state["observed"]["gateway_idle"])
        self.assertFalse(state["writable"])
        self.assertNotIn("hermes_plugin_heartbeat_fresh", state["blocking_checks"])
        self.assertIn("catalog_sync_healthy", state["blocking_checks"])
        self.assertIn("result_outbox_below_limit", state["blocking_checks"])

    def test_idle_window_zero_only_looks_at_inflight(self):
        """Setting ADS_RUNTIME_IDLE_WINDOW_SECONDS=0 means "idle iff no
        in-flight activity" — even a heartbeat that's one second old keeps
        the gateway marked active and blocks autopilot when stale."""
        self.env.store.update_settings({"mode": "autopilot", "execution_enabled": True})
        self.heartbeat()
        # No in-flight activity at all, heartbeat fresh → ready.
        with patch.dict("os.environ", {"ADS_RUNTIME_IDLE_WINDOW_SECONDS": "0"}):
            fresh = readiness_snapshot(self.env.store)
            self.assertTrue(fresh["writable"])
            self.assertTrue(fresh["observed"]["gateway_idle"])
        # Now seed in-flight activity and re-check: even with window=0, the
        # activity tables force active state.
        self._seed_active_worker()
        with patch.dict("os.environ", {"ADS_RUNTIME_IDLE_WINDOW_SECONDS": "0"}):
            active = readiness_snapshot(self.env.store)
            self.assertFalse(active["observed"]["gateway_idle"])
            self.assertTrue(active["observed"]["gateway_idle"] is False)

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

    # --- direct unit tests for the helper -------------------------------

    def test_gateway_is_idle_helper_on_fresh_db(self):
        """A control plane that has never seen any activity is idle."""
        self.assertTrue(gateway_is_idle(self.env.store))

    def test_gateway_is_idle_helper_with_recent_action(self):
        """A control plane with a recent tool action is NOT idle, regardless
        of the window size — even window=0 leaves active markers recent."""
        self.heartbeat()
        self.env.store.record_action(
            decision_id=None, task_id=None, session_id="s",
            actor_role="executor", phase="before", tool_name="x",
            operation="write", allowed=True, args={},
        )
        self.assertFalse(gateway_is_idle(self.env.store, window_seconds=86400))

    def test_gateway_is_idle_helper_with_running_worker(self):
        """A control plane with a running worker is NOT idle."""
        _, task, _ = self.env.one_decision_task()
        self.env.service.bind_worker({
            "task_id": task["id"],
            "worker_session_id": "exec-idle-test",
            "role": "executor",
            "goal": "[ads-role:executor]",
        })
        self.assertFalse(gateway_is_idle(self.env.store, window_seconds=86400))


if __name__ == "__main__":
    unittest.main()
