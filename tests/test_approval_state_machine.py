from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from amazon_ads_control.catalog import descriptor_from_payload
from amazon_ads_control.db import Store
from amazon_ads_control.service import ControlService

UTC = timezone.utc
CREATE_CAMPAIGN = {
    "registered_name": "mcp_amazon_ads_campaign_management_create_campaign",
    "native_name": "campaign_management-create_campaign",
    "schema": {
        "description": "Create one campaign",
        "parameters": {
            "type": "object",
            "required": ["campaigns"],
            "properties": {
                "campaigns": {
                    "type": "array", "minItems": 1, "maxItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["name", "budget"],
                        "properties": {
                            "name": {"type": "string"},
                            "budget": {"type": "number", "minimum": 1},
                        },
                    },
                }
            },
        },
    },
}


class ApprovalStateMachineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "state.db")
        self.service = ControlService(self.store)
        self.store.sync_catalog([descriptor_from_payload(CREATE_CAMPAIGN)])
        self.store.update_settings({"mode": "autopilot", "execution_enabled": True})

    def tearDown(self):
        self.temp.cleanup()

    def plan(self):
        return self.service.create_managed_plan({
            "title": "Approval state plan",
            "profile": {
                "profile_id": "p1", "marketplace": "US",
                "country_code": "US", "currency": "USD",
            },
            "actions": [{
                "plan_key": "campaign",
                "tool_name": CREATE_CAMPAIGN["registered_name"],
                "action_type": "create_campaign",
                "entity_type": "campaign",
                "entity_id": "planned:campaign",
                "arguments": {"campaigns": [{"name": "State Campaign", "budget": 12}]},
                "expected_state": {"name": "State Campaign", "budget": 12},
                "maximum_daily_budget": 12,
            }],
        }, "main")

    def approve(self, result):
        approval = result["approval"]
        self.store.approve_approval(
            approval["id"], "operator", approval["payload_hash"],
            f"APPROVE {approval['id']} {approval['payload_hash'][:12]}",
        )
        return self.store.get_approval(approval["id"])

    def bind_and_reserve(self, result):
        task_id = result["task"]["id"]
        self.service.bind_worker({
            "task_id": task_id,
            "worker_session_id": "executor",
            "worker_subagent_id": "executor-sub",
            "role": "executor",
            "goal": f"[ads-task:{task_id}] [ads-role:executor] execute",
            "model": "MiniMax-M3",
        })
        authorization = self.service.authorize_tool({
            "tool_name": CREATE_CAMPAIGN["registered_name"],
            "args": {"campaigns": [{"name": "State Campaign", "budget": 12}]},
            "session_id": "executor",
            "tool_call_id": "call-1",
        })
        self.assertTrue(authorization["allowed"], authorization)
        return authorization

    def test_approved_plan_requires_explicit_rejection_before_replacement(self):
        result = self.plan()
        approval = self.approve(result)
        with self.assertRaisesRegex(ValueError, "explicitly reject"):
            self.store.create_approval_request(result["task"]["id"], "main", "replacement")
        rejected = self.store.reject_approval(approval["id"], "operator", "replace plan")
        self.assertEqual(rejected["status"], "rejected")
        replacement = self.store.create_approval_request(
            result["task"]["id"], "main", "replacement", 30,
        )
        self.assertEqual(replacement["status"], "pending")

    def test_started_plan_cannot_be_rejected_or_superseded(self):
        result = self.plan()
        approval = self.approve(result)
        self.bind_and_reserve(result)
        with self.assertRaisesRegex(ValueError, "cannot be rejected"):
            self.store.reject_approval(approval["id"], "operator", "changed mind")
        with self.assertRaisesRegex(ValueError, "cannot be superseded"):
            self.store.create_approval_request(result["task"]["id"], "main", "replacement")

    def test_expiry_blocks_new_actions_and_preserves_after_expiry_completion(self):
        result = self.plan()
        approval = self.approve(result)
        authorization = self.bind_and_reserve(result)
        expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat(timespec="seconds")
        with self.store.connection() as conn:
            conn.execute(
                "UPDATE approval_requests SET expires_at=? WHERE id=?",
                (expired, approval["id"]),
            )
        self.store.reconcile_expired_approvals()
        current = self.store.get_approval(approval["id"])
        self.assertEqual(current["status"], "expired_in_flight")
        self.assertFalse(self.store.get_task(result["task"]["id"])["write_allowed"])

        decision = self.store.list_decisions(task_id=result["task"]["id"])[0]
        self.store.mark_execution(
            decision_id=decision["id"],
            reservation_token=authorization["reservation_token"],
            tool_name=CREATE_CAMPAIGN["registered_name"],
            outcome="success",
            result={"campaigns": [{"campaignId": "C-AFTER-EXPIRY"}]},
        )
        self.store.complete_approval_decision(decision["id"])
        current = self.store.get_approval(approval["id"])
        self.assertEqual(current["status"], "completed_after_expiry")

    def test_failed_action_finalizes_approval_with_issues(self):
        result = self.plan()
        approval = self.approve(result)
        authorization = self.bind_and_reserve(result)
        decision = self.store.list_decisions(task_id=result["task"]["id"])[0]
        self.store.mark_execution(
            decision_id=decision["id"],
            reservation_token=authorization["reservation_token"],
            tool_name=CREATE_CAMPAIGN["registered_name"],
            outcome="failed",
            result={"errors": [{"code": "INVALID_ARGUMENT"}]},
            failure="Amazon rejected the create request",
        )
        task = self.store.finalize_task(result["task"]["id"], "main", "failed as expected")
        self.assertEqual(task["status"], "completed_with_issues")
        current = self.store.get_approval(approval["id"])
        self.assertEqual(current["status"], "completed_with_issues")
        self.assertEqual(current["decisions"][0]["status"], "issue")


if __name__ == "__main__":
    unittest.main()
