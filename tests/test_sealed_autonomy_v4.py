from __future__ import annotations

import unittest

from amazon_ads_control.catalog import descriptor_from_payload
from helpers import Environment, one_target_snapshot

CREATE_CAMPAIGN = {
    "registered_name": "mcp_amazon_ads_campaign_management_create_campaign",
    "native_name": "campaign_management-create_campaign", "source": "hermes-registry:na",
    "schema": {"description": "Create one Sponsored Products campaign", "parameters": {
        "type": "object", "required": ["campaigns"], "properties": {"campaigns": {
            "type": "array", "minItems": 1, "maxItems": 1, "items": {"type": "object",
            "required": ["name", "budget", "state", "adProduct"], "properties": {
                "name": {"type": "string"}, "budget": {"type": "number", "minimum": 1},
                "state": {"type": "string"}, "adProduct": {"type": "string"}}}}}}},
}
UPDATE_CAMPAIGN = {
    "registered_name": "mcp_amazon_ads_campaign_management_update_campaign",
    "native_name": "campaign_management-update_campaign", "source": "hermes-registry:na",
    "schema": {"description": "Update one Sponsored Products campaign", "parameters": {
        "type": "object", "required": ["campaigns"], "properties": {"campaigns": {
            "type": "array", "minItems": 1, "maxItems": 1, "items": {"type": "object",
            "required": ["campaignId", "state"], "properties": {
                "campaignId": {"type": "string"}, "state": {"type": "string"}}}}}}},
}


class SealedAutonomyV4Tests(unittest.TestCase):
    def setUp(self):
        self.env = Environment(); self.env.plan(one_target_snapshot())
        self.env.store.sync_catalog([
            descriptor_from_payload(CREATE_CAMPAIGN),
            descriptor_from_payload(UPDATE_CAMPAIGN),
        ])
        self.env.store.update_settings({"mode": "autopilot", "execution_enabled": True})

    def tearDown(self):
        self.env.close()

    def payload(self, **overrides):
        campaign = {"name": "HERMES-SP-P1-EXACT-001", "budget": 20, "state": "PAUSED", "adProduct": "SPONSORED_PRODUCTS"}
        campaign.update(overrides)
        return {"standing_authorization": True, "title": "Sealed SP campaign maintenance",
            "profile": {"profile_id": "p1", "marketplace": "US", "country_code": "US", "currency": "USD"},
            "actions": [{"plan_key": "campaign-step", "tool_name": CREATE_CAMPAIGN["registered_name"],
                "action_type": "create_campaign", "entity_type": "campaign", "entity_id": "planned:campaign-step",
                "arguments": {"campaigns": [campaign]}, "expected_state": campaign,
                "maximum_daily_budget": campaign["budget"], "observed_in_ads": True}]}

    def test_exact_sp_plan_is_released_without_per_plan_human_approval(self):
        result = self.env.service.create_managed_plan(self.payload(), "main")
        self.assertTrue(result["standing_authorization"]["applied"])
        self.assertTrue(result["task"]["write_allowed"]); self.assertEqual(result["task"]["status"], "planned")
        self.assertEqual(result["approval"]["status"], "cancelled")
        decisions = self.env.store.list_decisions(task_id=result["task"]["id"])
        decision = next(item for item in decisions if item["action_type"] == "create_campaign")
        activation = next(item for item in decisions if item["payload"].get("activation_phase"))
        self.assertEqual(decision["payload"]["standing_authorization"]["ad_product"], "SPONSORED_PRODUCTS")
        self.assertEqual(activation["status"], "blocked")

    def test_campaign_must_be_paused_namespaced_and_bounded(self):
        with self.assertRaisesRegex(ValueError, "PAUSED"):
            self.env.service.create_managed_plan(self.payload(state="ENABLED"), "main")
        with self.assertRaisesRegex(ValueError, "namespace"):
            self.env.service.create_managed_plan(self.payload(name="Manual Campaign"), "main")
        with self.assertRaisesRegex(ValueError, "budget"):
            self.env.service.create_managed_plan(self.payload(budget=500), "main")

    def test_non_sp_scope_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Sponsored Products only"):
            self.env.service.create_managed_plan(self.payload(adProduct="SPONSORED_BRANDS"), "main")


if __name__ == "__main__":
    unittest.main()
