from __future__ import annotations

from datetime import datetime, timedelta, timezone
import tempfile
from pathlib import Path
import unittest

from amazon_ads_control.catalog import descriptor_from_payload
from amazon_ads_control.db import Store
from amazon_ads_control.service import ControlService

UTC = timezone.utc

CREATE_CAMPAIGN = {
    "registered_name": "mcp_amazon_ads_campaign_management_create_campaign",
    "native_name": "campaign_management-create_campaign",
    "source": "hermes-registry:na",
    "schema": {
        "name": "create campaign",
        "description": "Create one Sponsored Products campaign",
        "parameters": {
            "type": "object",
            "required": ["campaigns"],
            "additionalProperties": False,
            "properties": {
                "campaigns": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["name", "budget", "state"],
                        "additionalProperties": False,
                        "properties": {
                            "name": {"type": "string", "minLength": 1},
                            "budget": {"type": "number", "minimum": 1},
                            "state": {"type": "string", "enum": ["ENABLED", "PAUSED"]},
                        },
                    },
                }
            },
        },
    },
}
ACCOUNT_WRITE = {
    "registered_name": "mcp_amazon_ads_account_management_update_advertiser_account",
    "native_name": "account_management-update_advertiser_account",
    "source": "hermes-registry:na",
    "schema": {
        "description": "Update advertiser account",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}}},
    },
}


class ApprovalGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "state.db")
        self.service = ControlService(self.store)
        self.store.sync_catalog([
            descriptor_from_payload(CREATE_CAMPAIGN),
            descriptor_from_payload(ACCOUNT_WRITE),
        ])
        self.store.update_settings({"mode": "autopilot", "execution_enabled": True})
        self.args = {"campaigns": [{"name": "Approved-SP-Exact", "budget": 30, "state": "ENABLED"}]}

    def tearDown(self):
        self.temp.cleanup()

    def managed_plan(self):
        return self.service.create_managed_plan({
            "title": "Launch approved SP campaign",
            "profile": {
                "profile_id": "p1", "name": "US", "marketplace": "US",
                "country_code": "US", "currency": "USD",
            },
            "actions": [{
                "plan_key": "approved-sp-campaign",
                "tool_name": CREATE_CAMPAIGN["registered_name"],
                "action_type": "create_campaign",
                "entity_type": "campaign",
                "entity_id": "planned:approved-sp-exact",
                "arguments": self.args,
                "expected_state": {"name": "Approved-SP-Exact", "budget": 30, "state": "ENABLED"},
                "maximum_daily_budget": 30,
            }],
        }, "main")

    def test_high_risk_plan_waits_for_exact_operator_approval(self):
        result = self.managed_plan()
        task = result["task"]
        approval = result["approval"]
        decision = self.store.list_decisions(task_id=task["id"])[0]
        self.assertEqual(task["status"], "awaiting_approval")
        self.assertFalse(task["write_allowed"])
        self.assertEqual(approval["status"], "pending")

        with self.assertRaisesRegex(ValueError, "awaiting_approval"):
            self.service.bind_worker({
                "task_id": task["id"], "worker_session_id": "executor-before-approval",
                "worker_subagent_id": "sub-1", "role": "executor",
                "goal": f"[ads-task:{task['id']}] [ads-role:executor] execute exact plan",
                "model": "MiniMax-M3",
            })

        with self.assertRaisesRegex(ValueError, "payload hash mismatch"):
            self.store.approve_approval(
                approval["id"], "operator", "0" * 64,
                f"APPROVE {approval['id']} {'0' * 12}",
            )

        phrase = f"APPROVE {approval['id']} {approval['payload_hash'][:12]}"
        approved = self.store.approve_approval(
            approval["id"], "operator", approval["payload_hash"], phrase,
        )
        self.assertEqual(approved["status"], "approved")
        self.assertTrue(self.store.get_task(task["id"])["write_allowed"])

        self.service.bind_worker({
            "task_id": task["id"], "worker_session_id": "executor-after-approval",
            "worker_subagent_id": "sub-2", "role": "executor",
            "goal": f"[ads-task:{task['id']}] [ads-role:executor] execute exact plan",
            "model": "MiniMax-M3",
        })
        authorization = self.service.authorize_tool({
            "tool_name": CREATE_CAMPAIGN["registered_name"],
            "args": self.args,
            "session_id": "executor-after-approval",
            "tool_call_id": "call-1",
        })
        self.assertTrue(authorization["allowed"], authorization)
        self.assertEqual(authorization["decision_id"], decision["id"])
        self.assertTrue(authorization["reservation_token"])

        changed = self.service.authorize_tool({
            "tool_name": CREATE_CAMPAIGN["registered_name"],
            "args": {"campaigns": [{"name": "Approved-SP-Exact", "budget": 300, "state": "ENABLED"}]},
            "session_id": "executor-after-approval",
            "tool_call_id": "call-2",
        })
        self.assertFalse(changed["allowed"])

    def test_account_administration_cannot_be_approved(self):
        with self.assertRaisesRegex(ValueError, "account"):
            self.service.create_managed_plan({
                "title": "Forbidden account mutation",
                "profile": {"profile_id": "p1"},
                "actions": [{
                    "plan_key": "forbidden-account",
                    "tool_name": ACCOUNT_WRITE["registered_name"],
                    "action_type": "update_account",
                    "entity_id": "account:p1",
                    "arguments": {"name": "x"},
                    "expected_state": {"name": "x"},
                }],
            }, "main")

    def test_expired_request_cannot_be_approved(self):
        approval = self.managed_plan()["approval"]
        expired = (datetime.now(UTC) - timedelta(minutes=1)).isoformat(timespec="seconds")
        with self.store.connection() as conn:
            conn.execute("UPDATE approval_requests SET expires_at=? WHERE id=?", (expired, approval["id"]))
        self.store.reconcile_expired_approvals()
        current = self.store.get_approval(approval["id"])
        self.assertEqual(current["status"], "expired")
        with self.assertRaisesRegex(ValueError, "not pending"):
            self.store.approve_approval(
                approval["id"], "operator", approval["payload_hash"],
                f"APPROVE {approval['id']} {approval['payload_hash'][:12]}",
            )


if __name__ == "__main__":
    unittest.main()
