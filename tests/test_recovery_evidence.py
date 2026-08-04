from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from amazon_ads_control.catalog import descriptor_from_payload
from amazon_ads_control.db import Store
from amazon_ads_control.service import ControlService
from helpers import Environment, READ_TARGET, WRITE_TARGET, one_target_snapshot

UTC = timezone.utc


def marker(task_id: str, role: str) -> str:
    return f"[ads-task:{task_id}] [ads-role:{role}]"


class RecoveryEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.env = Environment()
        self.store = self.env.store
        self.service = self.env.service

    def tearDown(self):
        self.env.close()

    def _executed(self):
        _, task, decision = self.env.one_decision_task()
        self.service.bind_worker({"task_id": task["id"], "worker_session_id": "exec", "role": "executor", "goal": marker(task["id"], "executor")})
        auth = self.service.authorize_tool({"tool_name": WRITE_TARGET["registered_name"], "args": {"targetId": "t1", "bid": 0.8}, "session_id": "exec"})
        self.assertTrue(auth["allowed"])
        self.service.finish_tool({"tool_name": WRITE_TARGET["registered_name"], "args": {"targetId": "t1", "bid": 0.8}, "result": {"success": [{"targetId": "t1"}], "error": []}, "session_id": "exec", "decision_id": decision["id"], "reservation_token": auth["reservation_token"]})
        self.store.finish_worker("exec", "completed", "done")
        self.service.bind_worker({"task_id": task["id"], "worker_session_id": "verify", "role": "verifier", "goal": marker(task["id"], "verifier")})
        return task, decision

    def _read_action(self, task, *, session="verify", result=None, tool=READ_TARGET):
        result = result if result is not None else {"targetId": "t1", "bid": 0.8}
        auth = self.service.authorize_tool({"tool_name": tool["registered_name"], "args": {"targetId": "t1"}, "session_id": session})
        self.assertTrue(auth["allowed"])
        return self.service.finish_tool({"tool_name": tool["registered_name"], "args": {"targetId": "t1"}, "result": result, "session_id": session, "task_id": task["id"]})["action_id"]

    def test_verifier_must_be_different_current_session(self):
        _, task, _ = self.env.one_decision_task()
        self.service.bind_worker({"task_id": task["id"], "worker_session_id": "same", "role": "executor", "goal": marker(task["id"], "executor")})
        with self.assertRaisesRegex(ValueError, "different Hermes session"):
            self.service.bind_worker({"task_id": task["id"], "worker_session_id": "same", "role": "verifier", "goal": marker(task["id"], "verifier")})
        with self.assertRaisesRegex(ValueError, "different running executor"):
            self.service.bind_worker({"task_id": task["id"], "worker_session_id": "other", "role": "executor", "goal": marker(task["id"], "executor")})

    def test_expired_reservation_is_quarantined_and_late_result_reconciles(self):
        _, task, decision = self.env.one_decision_task()
        reserved = self.store.reserve_decision(decision["id"], task["id"], "exec", 300)
        with self.store.connection() as conn:
            conn.execute("UPDATE decisions SET reservation_expires_at=? WHERE id=?", ((datetime.now(UTC)-timedelta(seconds=1)).isoformat(), decision["id"]))
        ids = self.store.reconcile_expired_reservations()
        self.assertEqual(ids, [decision["id"]])
        current = self.store.get_decision(decision["id"])
        self.assertEqual(current["status"], "uncertain")
        self.assertEqual(current["execution_outcome"], "reservation_expired")
        with self.assertRaisesRegex(ValueError, "not reservable"):
            self.store.reserve_decision(decision["id"], task["id"], "exec2", 300)
        reconciled = self.store.mark_execution(decision_id=decision["id"], reservation_token=reserved["reservation_token"], tool_name=WRITE_TARGET["registered_name"], outcome="success", result={"success": True})
        self.assertEqual(reconciled["status"], "executed")
        self.assertIn("WRITE_RESERVATION_EXPIRED", [a["code"] for a in self.store.list_alerts()])

    def test_verification_requires_recorded_fresh_matching_read(self):
        task, decision = self._executed()
        with self.assertRaisesRegex(ValueError, "evidence_action_id"):
            self.service.verify_decision({"decision_id": decision["id"], "session_id": "verify", "actual": {"targetId": "t1", "bid": 0.8}})
        wrong_entity = self._read_action(task, result={"targetId": "other", "bid": 0.8})
        with self.assertRaisesRegex(ValueError, "planned entity"):
            self.service.verify_decision({"decision_id": decision["id"], "session_id": "verify", "evidence_action_id": wrong_entity})
        valid = self._read_action(task)
        verified = self.service.verify_decision({"decision_id": decision["id"], "session_id": "verify", "evidence_action_id": valid})
        self.assertEqual(verified["status"], "verified")
        record = self.store.list_verifications(decision_id=decision["id"])[0]
        self.assertEqual(record["evidence_action_id"], valid)

    def test_pre_write_and_stale_read_evidence_are_rejected(self):
        task, decision = self._executed()
        evidence = self._read_action(task)
        executed_at = self.store.get_decision(decision["id"])["executed_at"]
        with self.store.connection() as conn:
            conn.execute("UPDATE actions SET created_at=? WHERE id=?", ((datetime.fromisoformat(executed_at)-timedelta(seconds=1)).isoformat(), evidence))
        with self.assertRaisesRegex(ValueError, "predates the write"):
            self.service.verify_decision({"decision_id": decision["id"], "session_id": "verify", "evidence_action_id": evidence})

        evidence = self._read_action(task)
        with self.store.connection() as conn:
            conn.execute("UPDATE decisions SET executed_at=? WHERE id=?", ((datetime.now(UTC)-timedelta(hours=2)).isoformat(), decision["id"]))
            conn.execute("UPDATE actions SET created_at=? WHERE id=?", ((datetime.now(UTC)-timedelta(hours=1)).isoformat(), evidence))
        with self.assertRaisesRegex(ValueError, "too old"):
            self.service.verify_decision({"decision_id": decision["id"], "session_id": "verify", "evidence_action_id": evidence})

    def test_disabled_profile_and_stale_decision_block_write(self):
        _, task, decision = self.env.one_decision_task()
        self.service.bind_worker({"task_id": task["id"], "worker_session_id": "exec", "role": "executor", "goal": marker(task["id"], "executor")})
        self.store.upsert_profile({"profile_id": "p1", "enabled": False})
        blocked = self.service.authorize_tool({"tool_name": WRITE_TARGET["registered_name"], "args": {"targetId": "t1", "bid": 0.8}, "session_id": "exec"})
        self.assertFalse(blocked["allowed"]); self.assertIn("disabled", blocked["reason"])
        self.store.upsert_profile({"profile_id": "p1", "enabled": True})
        with self.store.connection() as conn:
            conn.execute("UPDATE decisions SET created_at=? WHERE id=?", ((datetime.now(UTC)-timedelta(hours=10)).isoformat(), decision["id"]))
        blocked = self.service.authorize_tool({"tool_name": WRITE_TARGET["registered_name"], "args": {"targetId": "t1", "bid": 0.8}, "session_id": "exec"})
        self.assertFalse(blocked["allowed"]); self.assertIn("too old", blocked["reason"])

    def test_catalog_semantic_family_and_risk_drift(self):
        base = descriptor_from_payload(WRITE_TARGET)
        self.store.sync_catalog([base])
        changed = dict(WRITE_TARGET)
        changed["schema"] = {**WRITE_TARGET["schema"], "description": "Bulk workflow update target and campaign"}
        result = self.store.sync_catalog([descriptor_from_payload(changed)])
        self.assertIn(WRITE_TARGET["registered_name"], result["drifted"])
        self.assertTrue(self.store.get_tool(WRITE_TARGET["registered_name"])["drifted"])

    def test_high_risk_composite_write_is_blocked(self):
        composite = {
            "registered_name": "mcp_amazon_ads_campaign_management_bulk_update_targets_workflow",
            "native_name": "campaign_management-bulk_update_targets_workflow",
            "source": "hermes-registry:na",
            "schema": {"name": "bulk update targets workflow", "description": "Update target bid", "parameters": {"type": "object"}},
        }
        tool = descriptor_from_payload(composite)
        self.assertEqual(tool.risk, "critical")
        self.store.sync_catalog([tool])
        result = self.service.authorize_tool({"tool_name": composite["registered_name"], "args": {}, "session_id": "main"})
        self.assertFalse(result["allowed"])

    def test_wrong_family_foreign_and_unstructured_evidence_are_rejected(self):
        task, decision = self._executed()
        campaign_read = {
            "registered_name":"mcp_amazon_ads_campaign_management_query_campaign",
            "native_name":"campaign_management-query_campaign",
            "source":"hermes-registry:na",
            "schema":{"description":"Query campaign","parameters":{"type":"object"}},
        }
        self.store.sync_catalog([descriptor_from_payload(READ_TARGET), descriptor_from_payload(WRITE_TARGET), descriptor_from_payload(campaign_read)])
        self.service.authorize_tool({"tool_name":campaign_read["registered_name"],"args":{"campaignId":"c1"},"session_id":"verify"})
        wrong=self.service.finish_tool({"tool_name":campaign_read["registered_name"],"args":{"campaignId":"c1"},"result":{"campaignId":"c1"},"session_id":"verify","task_id":task["id"]})["action_id"]
        with self.assertRaisesRegex(ValueError,"family"):
            self.service.verify_decision({"decision_id":decision["id"],"session_id":"verify","evidence_action_id":wrong})
        unstructured=self.store.record_action(decision_id=None,task_id=task["id"],session_id="verify",actor_role="verifier",phase="after",tool_name=READ_TARGET["registered_name"],operation="read",allowed=True,args={},structured_result=False,result={"targetId":"t1","bid":0.8})
        with self.assertRaisesRegex(ValueError,"structured"):
            self.service.verify_decision({"decision_id":decision["id"],"session_id":"verify","evidence_action_id":unstructured})

    def test_restart_quarantines_expired_reservation(self):
        _, task, decision = self.env.one_decision_task()
        self.store.reserve_decision(decision["id"],task["id"],"exec",60)
        with self.store.connection() as conn:
            conn.execute("UPDATE decisions SET reservation_expires_at=? WHERE id=?",((datetime.now(UTC)-timedelta(minutes=1)).isoformat(),decision["id"]))
        restarted=Store(self.store.path)
        service=ControlService(restarted)
        service.context(None)
        self.assertEqual(restarted.get_decision(decision["id"])["status"],"uncertain")

    def test_failed_write_is_not_verifiable(self):
        _,task,decision=self.env.one_decision_task()
        self.service.bind_worker({"task_id":task["id"],"worker_session_id":"exec","role":"executor","goal":marker(task["id"],"executor")})
        auth=self.service.authorize_tool({"tool_name":WRITE_TARGET["registered_name"],"args":{"targetId":"t1","bid":0.8},"session_id":"exec"})
        self.service.finish_tool({"tool_name":WRITE_TARGET["registered_name"],"result":{"error":[{"message":"rejected"}]},"session_id":"exec","decision_id":decision["id"],"reservation_token":auth["reservation_token"]})
        self.store.finish_worker("exec","failed")
        self.service.bind_worker({"task_id":task["id"],"worker_session_id":"verify","role":"verifier","goal":marker(task["id"],"verifier")})
        action=self._read_action(task)
        with self.assertRaisesRegex(ValueError,"not ready"):
            self.service.verify_decision({"decision_id":decision["id"],"session_id":"verify","evidence_action_id":action})

    def test_zero_placement_guardrail_authorizes_single_entity(self):
        placement_tool={
            "registered_name":"mcp_amazon_ads_campaign_management_update_campaign_placement",
            "native_name":"campaign_management-update_campaign_placement",
            "source":"hermes-registry:na",
            "schema":{"description":"Update campaign placement percentage","parameters":{"type":"object","properties":{"campaignId":{"type":"string"},"placement":{"type":"string"},"percentage":{"type":"number"}}}},
        }
        self.store.sync_catalog([descriptor_from_payload(placement_tool)])
        self.store.update_settings({"mode":"autopilot","execution_enabled":True})
        snapshot=one_target_snapshot(); snapshot["targets"]=[]; snapshot["placements"]=[{"campaign_id":"c","ad_product":"SPONSORED_PRODUCTS","placement":"TOP_OF_SEARCH","adjustment_percent":0,"clicks":20,"spend":10,"sales":100,"orders":3}]
        cycle=self.service.plan_cycle({"snapshot":snapshot},"main"); task=self.service.create_task({"cycle_id":cycle["id"]},"main")
        self.service.bind_worker({"task_id":task["id"],"worker_session_id":"exec-placement","role":"executor","goal":marker(task["id"],"executor")})
        auth=self.service.authorize_tool({"tool_name":placement_tool["registered_name"],"args":{"campaignId":"c","placement":"PLACEMENT_TOP","percentage":10},"session_id":"exec-placement"})
        self.assertTrue(auth["allowed"],auth["reason"])

    def test_entity_matching_requires_exact_scalar_not_substring(self):
        _,task,_=self.env.one_decision_task()
        self.service.bind_worker({"task_id":task["id"],"worker_session_id":"exec-exact","role":"executor","goal":marker(task["id"],"executor")})
        result=self.service.authorize_tool({"tool_name":WRITE_TARGET["registered_name"],"args":{"targetId":"prefix-t1-suffix","bid":0.8},"session_id":"exec-exact"})
        self.assertFalse(result["allowed"]); self.assertIn("does not match",result["reason"])

    def test_setting_profile_and_backup_validation(self):
        with self.assertRaisesRegex(ValueError, "boolean"):
            self.store.update_settings({"allow_bid_changes": 1})
        with self.assertRaisesRegex(ValueError, "target_acos"):
            self.store.update_settings({"target_acos": 50, "max_acos": 40})
        with self.assertRaisesRegex(ValueError, "unknown strategy"):
            self.store.upsert_profile({"profile_id": "p", "strategy": {"evil": True}})
        with self.assertRaisesRegex(ValueError, "differ"):
            self.store.backup_to(self.store.path)
        backup = Path(self.env.temp.name) / "nested" / "backup.db"
        result = self.store.backup_to(backup)
        self.assertTrue(result["integrity"]["ok"])
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
