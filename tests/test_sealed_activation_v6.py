from __future__ import annotations

import unittest

from amazon_ads_control.catalog import descriptor_from_payload
from amazon_ads_control.sealed_activation import _advance_activation
from helpers import Environment, one_target_snapshot

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
READ_CAMPAIGN = {
    "registered_name": "mcp_amazon_ads_campaign_management_query_campaign",
    "native_name": "campaign_management-query_campaign",
    "source": "hermes-registry:na",
    "schema": {"description": "Query one campaign", "parameters": {
        "type": "object", "properties": {"campaignId": {"type": "string"}}}},
}


class SealedActivationV6Tests(unittest.TestCase):
    def setUp(self):
        self.env = Environment()
        self.env.plan(one_target_snapshot())
        self.env.store.sync_catalog([
            descriptor_from_payload(CREATE_CAMPAIGN),
            descriptor_from_payload(UPDATE_CAMPAIGN),
            descriptor_from_payload(READ_CAMPAIGN),
        ])
        self.env.store.update_settings({"mode": "autopilot", "execution_enabled": True})

    def tearDown(self):
        self.env.close()

    def payload(self):
        campaign = {
            "name": "HERMES-SP-CLOSED-LOOP-001",
            "budget": 20,
            "state": "PAUSED",
            "adProduct": "SPONSORED_PRODUCTS",
        }
        return {
            "title": "Closed-loop SP campaign",
            "profile": {
                "profile_id": "p1", "marketplace": "US",
                "country_code": "US", "currency": "USD",
            },
            "actions": [{
                "plan_key": "campaign-step",
                "tool_name": CREATE_CAMPAIGN["registered_name"],
                "action_type": "create_campaign",
                "entity_type": "campaign",
                "entity_id": "planned:campaign-step",
                "arguments": {"campaigns": [campaign]},
                "expected_state": campaign,
                "maximum_daily_budget": 20,
                "observed_in_ads": True,
            }],
        }

    def bind(self, task_id, session_id, role, model):
        return self.env.service.bind_worker({
            "task_id": task_id,
            "worker_session_id": session_id,
            "worker_subagent_id": session_id + "-sub",
            "role": role,
            "model": model,
            "goal": f"[ads-task:{task_id}] [ads-role:{role}] sealed activation phase",
        })

    def evidence(self, task_id, session_id, result):
        return self.env.store.record_action(
            task_id=task_id,
            session_id=session_id,
            actor_role="verifier",
            phase="after",
            tool_name=READ_CAMPAIGN["registered_name"],
            operation="read",
            allowed=True,
            args={"campaignId": "AMZ-CAMPAIGN-1"},
            success=True,
            outcome_status="success",
            structured_result=True,
            result=result,
        )

    def test_verified_paused_create_releases_and_verifies_automatic_enable(self):
        result = self.env.service.create_managed_plan(self.payload(), "hermes-main")
        task_id = result["task"]["id"]
        decisions = {item["plan_key"]: item for item in self.env.store.list_decisions(task_id=task_id)}
        create = decisions["campaign-step"]
        activation = decisions["campaign-step:verified-enable"]
        self.assertEqual(activation["status"], "blocked")
        self.assertTrue(activation["payload"]["standing_authorization"]["verified_create"])

        self.bind(task_id, "create-executor", "executor", "MiniMax-M3")
        reserved = self.env.store.reserve_decision(create["id"], task_id, "create-executor", 300)
        self.env.store.mark_execution(
            decision_id=create["id"],
            reservation_token=reserved["reservation_token"],
            tool_name=CREATE_CAMPAIGN["registered_name"],
            outcome="success",
            result={"campaigns": [{"campaignId": "AMZ-CAMPAIGN-1"}]},
        )
        self.env.store.finish_worker("create-executor", "completed")

        self.bind(task_id, "create-verifier", "verifier", "gpt-5.6-sol")
        evidence = self.evidence(task_id, "create-verifier", {
            "campaigns": [{
                "campaignId": "AMZ-CAMPAIGN-1",
                "name": "HERMES-SP-CLOSED-LOOP-001",
                "budget": 20,
                "state": "PAUSED",
                "adProduct": "SPONSORED_PRODUCTS",
            }]
        })
        verified = self.env.service.verify_decision({
            "decision_id": create["id"],
            "session_id": "create-verifier",
            "evidence_action_id": evidence,
        })
        self.assertEqual(verified["activation_transition"]["state"], "activation_planned")
        activation = self.env.store.get_decision(activation["id"])
        self.assertEqual((activation["status"], activation["entity_id"]), ("planned", "AMZ-CAMPAIGN-1"))
        self.assertEqual(self.env.store.get_task(task_id)["status"], "planned")

        self.bind(task_id, "activation-executor", "executor", "MiniMax-M3")
        args = {"campaigns": [{"campaignId": "AMZ-CAMPAIGN-1", "state": "ENABLED"}]}
        authorized = self.env.service.authorize_tool({
            "tool_name": UPDATE_CAMPAIGN["registered_name"],
            "args": args,
            "session_id": "activation-executor",
            "tool_call_id": "activation-call",
        })
        self.assertTrue(authorized["allowed"], authorized)
        self.env.store.mark_execution(
            decision_id=activation["id"],
            reservation_token=authorized["reservation_token"],
            tool_name=UPDATE_CAMPAIGN["registered_name"],
            outcome="success",
            result={"campaigns": [{"campaignId": "AMZ-CAMPAIGN-1", "state": "ENABLED"}]},
        )
        self.env.store.finish_worker("activation-executor", "completed")

        self.bind(task_id, "activation-verifier", "verifier", "gpt-5.6-sol")
        evidence = self.evidence(task_id, "activation-verifier", {
            "campaigns": [{"campaignId": "AMZ-CAMPAIGN-1", "state": "ENABLED"}]
        })
        completed = self.env.service.verify_decision({
            "decision_id": activation["id"],
            "session_id": "activation-verifier",
            "evidence_action_id": evidence,
        })
        self.assertEqual(completed["activation_transition"]["state"], "completed")
        self.assertEqual(self.env.store.get_task(task_id)["status"], "completed")

    def test_leaf_then_ad_group_then_campaign_release_order_is_strict(self):
        profile = {"profile_id": "p1", "marketplace": "US", "country_code": "US", "currency": "USD"}

        def decision(entity_type, entity_id, action_type, plan_key, payload, priority):
            return {
                "entity_type": entity_type,
                "entity_id": entity_id,
                "action_type": action_type,
                "priority": priority,
                "rule_id": "sealed_activation_order_test",
                "reason": "Verify strict leaf-to-Campaign activation release order",
                "evidence": {},
                "payload": payload,
                "expected_family": entity_type,
                "risk": "high",
                "plan_key": plan_key,
            }

        decisions = [
            decision("campaign", "AMZ-C", "create_campaign", "create-c", {}, 100),
            decision("ad_group", "AMZ-G", "create_ad_group", "create-g", {}, 90),
            decision("ad", "AMZ-A", "create_ad", "create-a", {}, 80),
            decision("ad", "AMZ-A", "enable", "enable-a", {"activation_phase": True, "activation_rank": 10}, 30),
            decision("ad_group", "AMZ-G", "enable", "enable-g", {"activation_phase": True, "activation_rank": 20}, 20),
            decision("campaign", "AMZ-C", "enable", "enable-c", {"activation_phase": True, "activation_rank": 30}, 10),
        ]
        cycle = self.env.store.create_cycle(
            profile=profile, source="activation-order-test",
            window={"start": None, "end": None, "grain": "structural-plan"},
            data_quality={"eligible_for_writes": True}, kpis={}, snapshot={"profile": profile},
            decisions=decisions, created_by="test",
        )
        rows = self.env.store.list_decisions(cycle_id=cycle["id"], limit=20)
        by_key = {item["plan_key"]: item for item in rows}
        task = self.env.store.create_task(
            title="strict staged activation", kind="full-managed-sp-plan", created_by="test",
            parent_session_id=None, write_allowed=True, payload={}, cycle_id=cycle["id"],
            decision_ids=[item["id"] for item in rows],
        )
        create_ids = [by_key[key]["id"] for key in ("create-c", "create-g", "create-a")]
        activation_ids = [by_key[key]["id"] for key in ("enable-a", "enable-g", "enable-c")]
        with self.env.store.connection() as conn:
            conn.execute(
                f"UPDATE decisions SET status='verified' WHERE id IN ({','.join('?' for _ in create_ids)})",
                create_ids,
            )
            conn.execute(
                f"UPDATE decisions SET status='blocked' WHERE id IN ({','.join('?' for _ in activation_ids)})",
                activation_ids,
            )

        first = _advance_activation(self.env.store, task["id"])
        self.assertEqual((first["state"], first["rank"]), ("activation_planned", 10))
        self.assertEqual(self.env.store.get_decision(by_key["enable-a"]["id"])["status"], "planned")
        self.assertEqual(self.env.store.get_decision(by_key["enable-g"]["id"])["status"], "blocked")
        self.assertEqual(self.env.store.get_decision(by_key["enable-c"]["id"])["status"], "blocked")

        with self.env.store.connection() as conn:
            conn.execute("UPDATE decisions SET status='verified' WHERE id=?", (by_key["enable-a"]["id"],))
        second = _advance_activation(self.env.store, task["id"])
        self.assertEqual((second["state"], second["rank"]), ("activation_planned", 20))
        self.assertEqual(self.env.store.get_decision(by_key["enable-g"]["id"])["status"], "planned")
        self.assertEqual(self.env.store.get_decision(by_key["enable-c"]["id"])["status"], "blocked")

        with self.env.store.connection() as conn:
            conn.execute("UPDATE decisions SET status='verified' WHERE id=?", (by_key["enable-g"]["id"],))
        third = _advance_activation(self.env.store, task["id"])
        self.assertEqual((third["state"], third["rank"]), ("activation_planned", 30))
        self.assertEqual(self.env.store.get_decision(by_key["enable-c"]["id"])["status"], "planned")

        with self.env.store.connection() as conn:
            conn.execute("UPDATE decisions SET status='verified' WHERE id=?", (by_key["enable-c"]["id"],))
        final = _advance_activation(self.env.store, task["id"])
        self.assertEqual(final["state"], "completed")
        self.assertEqual(self.env.store.get_task(task["id"])["status"], "completed")

    def test_missing_atomic_activation_tool_rejects_create_before_task_exists(self):
        self.env.store.sync_catalog([descriptor_from_payload(CREATE_CAMPAIGN)])
        before = len(self.env.store.list_tasks())
        with self.assertRaisesRegex(ValueError, "cannot guarantee PAUSED-to-ENABLED closure"):
            self.env.service.create_managed_plan(self.payload(), "hermes-main")
        self.assertEqual(len(self.env.store.list_tasks()), before)

    def test_create_verification_mismatch_aborts_activation_and_keeps_campaign_paused(self):
        result = self.env.service.create_managed_plan(self.payload(), "hermes-main")
        task_id = result["task"]["id"]
        decisions = {item["plan_key"]: item for item in self.env.store.list_decisions(task_id=task_id)}
        create = decisions["campaign-step"]
        activation = decisions["campaign-step:verified-enable"]
        self.bind(task_id, "bad-create-executor", "executor", "MiniMax-M3")
        reserved = self.env.store.reserve_decision(create["id"], task_id, "bad-create-executor", 300)
        self.env.store.mark_execution(
            decision_id=create["id"], reservation_token=reserved["reservation_token"],
            tool_name=CREATE_CAMPAIGN["registered_name"], outcome="success",
            result={"campaigns": [{"campaignId": "AMZ-CAMPAIGN-1"}]},
        )
        self.env.store.finish_worker("bad-create-executor", "completed")
        self.bind(task_id, "bad-create-verifier", "verifier", "gpt-5.6-sol")
        evidence = self.evidence(task_id, "bad-create-verifier", {
            "campaigns": [{
                "campaignId": "AMZ-CAMPAIGN-1", "name": "WRONG",
                "budget": 20, "state": "PAUSED", "adProduct": "SPONSORED_PRODUCTS",
            }]
        })
        mismatch = self.env.service.verify_decision({
            "decision_id": create["id"], "session_id": "bad-create-verifier",
            "evidence_action_id": evidence,
        })
        self.assertEqual(mismatch["activation_transition"]["state"], "aborted")
        self.assertEqual(self.env.store.get_decision(activation["id"])["status"], "failed")
        self.assertFalse(any(
            item.get("status") == "planned" and item.get("action_type") == "enable"
            for item in self.env.store.list_decisions(task_id=task_id)
        ))


if __name__ == "__main__":
    unittest.main()
