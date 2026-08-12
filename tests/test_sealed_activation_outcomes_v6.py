from __future__ import annotations

import unittest

from amazon_ads_control.catalog import descriptor_from_payload
from helpers import Environment, READ_CAMPAIGN, one_target_snapshot

CREATE_CAMPAIGN = {
    "registered_name": "mcp_amazon_ads_campaign_management_create_campaign",
    "native_name": "campaign_management-create_campaign",
    "source": "hermes-registry:na",
    "schema": {"description": "Create one Sponsored Products campaign", "parameters": {
        "type": "object", "required": ["campaigns"], "properties": {"campaigns": {
            "type": "array", "minItems": 1, "maxItems": 1, "items": {"type": "object",
            "required": ["name", "budget", "state", "adProduct"], "properties": {
                "name": {"type": "string"}, "budget": {"type": "number", "minimum": 1},
                "state": {"type": "string"}, "adProduct": {"type": "string"}}}}}}},
}
UPDATE_CAMPAIGN = {
    "registered_name": "mcp_amazon_ads_campaign_management_update_campaign",
    "native_name": "campaign_management-update_campaign",
    "source": "hermes-registry:na",
    "schema": {"description": "Update one Sponsored Products campaign", "parameters": {
        "type": "object", "required": ["campaigns"], "properties": {"campaigns": {
            "type": "array", "minItems": 1, "maxItems": 1, "items": {"type": "object",
            "required": ["campaignId", "state"], "properties": {
                "campaignId": {"type": "string"}, "state": {"type": "string"}}}}}}},
}


class SealedActivationOutcomeV6Tests(unittest.TestCase):
    def setUp(self):
        self.env = Environment()
        self.env.plan(one_target_snapshot())
        self.env.store.sync_catalog([
            descriptor_from_payload(CREATE_CAMPAIGN),
            descriptor_from_payload(UPDATE_CAMPAIGN),
            descriptor_from_payload(READ_CAMPAIGN),
        ])
        self.env.store.update_settings({"mode": "autopilot", "execution_enabled": True})
        self.env.store.record_action(
            task_id=None, session_id="initial-budget-read", actor_role="main",
            phase="after", tool_name=READ_CAMPAIGN["registered_name"],
            operation="read", allowed=True,
            args={"body": {"accessRequestedAccount": {"profileId": "p1"}}},
            success=True, outcome_status="success", structured_result=True,
            result={"campaigns": []},
        )

    def tearDown(self):
        self.env.close()

    def create_task(self):
        campaign = {
            "name": "HERMES-SP-OUTCOME-001", "budget": 20,
            "state": "PAUSED", "adProduct": "SPONSORED_PRODUCTS",
        }
        result = self.env.service.create_managed_plan({
            "title": "Activation outcome quarantine",
            "profile": {"profile_id": "p1", "marketplace": "US", "country_code": "US", "currency": "USD"},
            "actions": [{
                "plan_key": "campaign-step",
                "tool_name": CREATE_CAMPAIGN["registered_name"],
                "action_type": "create_campaign",
                "entity_type": "campaign",
                "entity_id": "planned:campaign-step",
                "arguments": {"campaigns": [campaign]},
                "expected_state": campaign,
                "maximum_daily_budget": 20,
            }],
        }, "hermes-main")
        rows = {item["plan_key"]: item for item in self.env.store.list_decisions(task_id=result["task"]["id"])}
        return result["task"], rows["campaign-step"], rows["campaign-step:verified-enable"]

    def bind_and_reserve(self, task_id, decision_id, session_id):
        self.env.service.bind_worker({
            "task_id": task_id,
            "worker_session_id": session_id,
            "worker_subagent_id": session_id + "-sub",
            "role": "executor",
            "model": "MiniMax-M3",
            "goal": f"[ads-task:{task_id}] [ads-role:executor] activation outcome test",
        })
        return self.env.store.reserve_decision(decision_id, task_id, session_id, 300)

    def test_definite_create_failure_aborts_all_activation_stages_immediately(self):
        task, create, activation = self.create_task()
        reserved = self.bind_and_reserve(task["id"], create["id"], "failure-executor")
        result = self.env.store.mark_execution(
            decision_id=create["id"],
            reservation_token=reserved["reservation_token"],
            tool_name=CREATE_CAMPAIGN["registered_name"],
            outcome="failure",
            result={"errors": [{"code": "INVALID_ARGUMENT"}]},
            failure="Amazon rejected create",
        )
        self.assertEqual(result["activation_transition"]["state"], "aborted")
        self.assertEqual(self.env.store.get_decision(activation["id"])["status"], "failed")
        self.assertFalse(any(
            item.get("action_type") == "enable" and item.get("status") == "planned"
            for item in self.env.store.list_decisions(task_id=task["id"])
        ))

    def test_unknown_create_outcome_quarantines_and_permanently_blocks_activation(self):
        task, create, activation = self.create_task()
        reserved = self.bind_and_reserve(task["id"], create["id"], "unknown-executor")
        result = self.env.store.mark_execution(
            decision_id=create["id"],
            reservation_token=reserved["reservation_token"],
            tool_name=CREATE_CAMPAIGN["registered_name"],
            outcome="unknown",
            result={"transport": "timeout"},
            failure="response lost after write attempt",
        )
        self.assertEqual(result["activation_transition"]["state"], "write_uncertain")
        self.assertEqual(self.env.store.get_decision(create["id"])["status"], "uncertain")
        self.assertEqual(self.env.store.get_decision(activation["id"])["status"], "failed")
        self.assertEqual(self.env.store.get_task(task["id"])["payload"]["activation_state"], "write_uncertain")


if __name__ == "__main__":
    unittest.main()