from __future__ import annotations

import unittest

from amazon_ads_control.catalog import descriptor_from_payload
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


class FullManagedV5Tests(unittest.TestCase):
    def setUp(self):
        self.env = Environment()
        self.env.plan(one_target_snapshot())
        self.env.store.sync_catalog([descriptor_from_payload(CREATE_CAMPAIGN)])

    def tearDown(self):
        self.env.close()

    def payload(self, **overrides):
        campaign = {"name": "HERMES-SP-FULL-MANAGED-001", "budget": 20, "state": "PAUSED", "adProduct": "SPONSORED_PRODUCTS"}
        campaign.update(overrides)
        return {
            "title": "Full-managed SP campaign maintenance",
            "profile": {"profile_id": "p1", "marketplace": "US", "country_code": "US", "currency": "USD"},
            "actions": [{
                "plan_key": "campaign-step", "tool_name": CREATE_CAMPAIGN["registered_name"],
                "action_type": "create_campaign", "entity_type": "campaign", "entity_id": "planned:campaign-step",
                "arguments": {"campaigns": [campaign]}, "expected_state": campaign,
                "maximum_daily_budget": campaign["budget"], "observed_in_ads": True,
            }],
        }

    def test_valid_sp_plan_is_automatic_without_any_approval_flag(self):
        result = self.env.service.create_managed_plan(self.payload(), "hermes-main")
        self.assertTrue(result["standing_authorization"]["applied"])
        self.assertTrue(result["standing_authorization"]["automatic"])
        self.assertEqual(result["task"]["kind"], "full-managed-sp-plan")
        self.assertEqual(result["task"]["status"], "planned")
        self.assertTrue(result["task"]["write_allowed"])
        self.assertEqual(result["approval"]["status"], "cancelled")
        decision = self.env.store.list_decisions(task_id=result["task"]["id"])[0]
        marker = decision["payload"]["standing_authorization"]
        self.assertEqual(marker["scope"], "sealed-sp")
        self.assertFalse(decision["payload"]["approval_required"])

    def test_explicit_sp_plan_outside_envelope_is_rejected_not_downgraded(self):
        with self.assertRaisesRegex(ValueError, "namespace"):
            self.env.service.create_managed_plan(self.payload(name="Manual Campaign"), "hermes-main")
        with self.assertRaisesRegex(ValueError, "PAUSED"):
            self.env.service.create_managed_plan(self.payload(state="ENABLED"), "hermes-main")
        with self.assertRaisesRegex(ValueError, "budget"):
            self.env.service.create_managed_plan(self.payload(budget=500), "hermes-main")

    def test_product_defaults_describe_visualization_only_full_management(self):
        settings = self.env.store.get_settings()
        self.assertTrue(settings["full_managed_sp_enabled"])
        self.assertTrue(settings["notify_only_on_exception"])
        self.assertTrue(settings["web_visualization_only"])
        self.assertEqual(settings["auto_write_ad_products"], ["SPONSORED_PRODUCTS"])


if __name__ == "__main__":
    unittest.main()
