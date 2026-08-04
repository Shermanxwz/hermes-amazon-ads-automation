from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import unittest

from amazon_ads_control.reporting import snapshot_hash
from amazon_ads_control.strategy import OptimizationEngine, StrategyPolicy
from helpers import Environment, READ_TARGET, WRITE_TARGET, one_target_snapshot


class ClosedLoopV3Tests(unittest.TestCase):
    def setUp(self):
        self.env = Environment(strict_writes=True)

    def tearDown(self):
        self.env.close()

    def _executor_read(self, task, decision, bid=1.0, session="exec", call_id="pre-read"):
        self.env.service.bind_worker({
            "task_id": task["id"], "worker_session_id": session, "role": "executor",
            "goal": f"[ads-task:{task['id']}] [ads-role:executor]",
        })
        allowed = self.env.service.authorize_tool({
            "tool_name": READ_TARGET["registered_name"], "args": {"targetId": "t1"},
            "session_id": session, "tool_call_id": call_id,
        })
        self.assertTrue(allowed["allowed"])
        read = self.env.service.finish_tool({
            "tool_name": READ_TARGET["registered_name"], "args": {"targetId": "t1"},
            "result": {"targetId": "t1", "bid": bid}, "session_id": session,
            "task_id": task["id"], "tool_call_id": call_id,
        })
        return self.env.service.prepare_write({
            "decision_id": decision["id"], "evidence_action_id": read["action_id"], "session_id": session,
        })

    def _execute(self, task, decision, *, event_id="event-1", result=None):
        self._executor_read(task, decision)
        auth = self.env.service.authorize_tool({
            "tool_name": WRITE_TARGET["registered_name"], "args": {"targetId": "t1", "bid": 0.8},
            "session_id": "exec", "tool_call_id": "write-1",
        })
        self.assertTrue(auth["allowed"])
        payload = {
            "event_id": event_id, "tool_name": WRITE_TARGET["registered_name"],
            "args": {"targetId": "t1", "bid": 0.8},
            "result": result or {"success": [{"targetId": "t1"}], "error": []},
            "session_id": "exec", "decision_id": decision["id"],
            "reservation_token": auth["reservation_token"], "tool_call_id": "write-1",
        }
        return self.env.service.finish_tool(payload), payload

    def test_missing_required_metric_is_rejected_not_zero_filled(self):
        snapshot = one_target_snapshot()
        del snapshot["targets"][0]["orders"]
        plan = OptimizationEngine().plan(snapshot, StrategyPolicy())
        self.assertEqual(plan.decisions, [])
        self.assertIn("missing_orders", plan.data_quality["rejected_rows"]["targets"]["0"])

    def test_report_lifecycle_and_lineage_survive_restart(self):
        snapshot = one_target_snapshot()
        lineage = self.env.lineage_for(snapshot)
        cycle = self.env.service.plan_cycle({"snapshot": snapshot, "lineage": lineage}, "main")
        self.assertEqual(cycle["lineage"]["normalized_hash"], snapshot_hash(snapshot))
        reopened = type(self.env.store)(self.env.store.path)
        job = reopened.get_report_job(lineage["report_job_ids"][0])
        self.assertEqual(job["status"], "INGESTED")
        with self.assertRaisesRegex(ValueError, "transition"):
            reopened.transition_report(job["id"], "SUBMITTED", {"report_id": job["report_id"]}, "test")

    def test_lineage_hash_and_profile_are_enforced(self):
        snapshot = one_target_snapshot()
        lineage = self.env.lineage_for(snapshot)
        wrong = dict(lineage); wrong["normalized_hash"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "normalized_hash"):
            self.env.service.plan_cycle({"snapshot": snapshot, "lineage": wrong}, "main")
        other = one_target_snapshot(profile_id="other")
        other_lineage = dict(lineage); other_lineage["normalized_hash"] = snapshot_hash(other)
        with self.assertRaisesRegex(ValueError, "profile"):
            self.env.service.plan_cycle({"snapshot": other, "lineage": other_lineage}, "main")

    def test_compare_and_set_blocks_stale_before_value(self):
        _cycle, task, decision = self.env.one_decision_task()
        with self.assertRaisesRegex(ValueError, "before value"):
            self._executor_read(task, decision, bid=0.9)
        self._executor_read(task, decision, bid=1.0, session="exec", call_id="pre-read-fresh")

    def test_write_requires_precondition_and_exact_callback_identity(self):
        _cycle, task, decision = self.env.one_decision_task()
        self.env.service.bind_worker({"task_id": task["id"], "worker_session_id": "exec", "role": "executor", "goal": "executor"})
        blocked = self.env.service.authorize_tool({
            "tool_name": WRITE_TARGET["registered_name"], "args": {"targetId": "t1", "bid": 0.8}, "session_id": "exec",
        })
        self.assertFalse(blocked["allowed"]); self.assertIn("compare-and-set", blocked["reason"])
        first, payload = self._execute(task, decision)
        self.assertFalse(first["duplicate"])
        duplicate = self.env.service.finish_tool(payload)
        self.assertTrue(duplicate["duplicate"])
        conflicting = deepcopy(payload)
        conflicting["result"] = {"success": [{"targetId": "t1", "changed": False}], "error": []}
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.env.service.finish_tool(conflicting)

    def test_verification_never_combines_sibling_objects(self):
        _cycle, task, decision = self.env.one_decision_task()
        self._execute(task, decision)
        self.env.service.bind_worker({
            "task_id": task["id"], "worker_session_id": "verify", "role": "verifier",
            "goal": f"[ads-task:{task['id']}] [ads-role:verifier]",
        })
        self.env.service.authorize_tool({
            "tool_name": READ_TARGET["registered_name"], "args": {"targetId": "t1"},
            "session_id": "verify", "tool_call_id": "verify-read",
        })
        read = self.env.service.finish_tool({
            "tool_name": READ_TARGET["registered_name"], "args": {"targetId": "t1"},
            "result": [{"targetId": "t1", "bid": 0.7}, {"targetId": "t2", "bid": 0.8}],
            "session_id": "verify", "task_id": task["id"], "tool_call_id": "verify-read",
        })
        verified = self.env.service.verify_decision({
            "decision_id": decision["id"], "session_id": "verify", "evidence_action_id": read["action_id"],
        })
        self.assertEqual(verified["status"], "mismatch")
        record = self.env.store.list_verifications(1, decision_id=decision["id"])[0]
        self.assertEqual(record["actual"]["targetId"], "t1")
        self.assertEqual(record["actual"]["bid"], 0.7)

    def test_server_recomputes_catalog_risk(self):
        raw = dict(WRITE_TARGET)
        raw.update({"semantic": "read", "family": "profile", "risk": "low"})
        self.env.service.sync_catalog({"tools": [raw]})
        stored = self.env.store.get_tool(WRITE_TARGET["registered_name"])
        self.assertEqual(stored["semantic"], "write")
        self.assertEqual(stored["family"], "target")
        self.assertNotEqual(stored["risk"], "low")

    def test_entity_family_cooldown_catches_different_plan_key(self):
        _cycle, task, decision = self.env.one_decision_task()
        self._execute(task, decision)
        snapshot = one_target_snapshot(waste=False)
        snapshot["targets"][0]["bid"] = 0.8
        start = date.fromisoformat(snapshot["window"]["start"]) - timedelta(days=1)
        snapshot["window"]["start"] = start.isoformat(); snapshot["window"]["days"] += 1
        cycle2 = self.env.plan(snapshot)
        task2 = self.env.service.create_task({"cycle_id": cycle2["id"]}, "main")
        decision2 = self.env.store.list_decisions(task_id=task2["id"])[0]
        self.assertNotEqual(decision["plan_key"], decision2["plan_key"])
        self.env.service.bind_worker({"task_id": task2["id"], "worker_session_id": "exec2", "role": "executor", "goal": "executor"})
        self.env.service.authorize_tool({"tool_name": READ_TARGET["registered_name"], "args": {"targetId": "t1"}, "session_id": "exec2", "tool_call_id": "r2"})
        read = self.env.service.finish_tool({"tool_name": READ_TARGET["registered_name"], "args": {"targetId": "t1"}, "result": {"targetId": "t1", "bid": 0.8}, "session_id": "exec2", "task_id": task2["id"], "tool_call_id": "r2"})
        self.env.service.prepare_write({"decision_id": decision2["id"], "evidence_action_id": read["action_id"], "session_id": "exec2"})
        blocked = self.env.service.authorize_tool({"tool_name": WRITE_TARGET["registered_name"], "args": {"targetId": "t1", "bid": 0.88}, "session_id": "exec2"})
        self.assertFalse(blocked["allowed"]); self.assertIn("cooldown", blocked["reason"])


if __name__ == "__main__":
    unittest.main()
