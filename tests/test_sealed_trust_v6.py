from __future__ import annotations

import unittest

from amazon_ads_control.catalog import descriptor_from_payload
from amazon_ads_control.sealed_plan import validate_standing_plan
from helpers import Environment, one_target_snapshot

UPDATE_CAMPAIGN = {
    "registered_name": "mcp_amazon_ads_campaign_management_update_campaign",
    "native_name": "campaign_management-update_campaign",
    "source": "hermes-registry:na",
    "schema": {"description": "Update one Sponsored Products campaign", "parameters": {
        "type": "object", "required": ["campaigns"], "properties": {"campaigns": {
            "type": "array", "minItems": 1, "maxItems": 1, "items": {"type": "object",
            "required": ["campaignId", "state", "adProduct"], "properties": {
                "campaignId": {"type": "string"}, "state": {"type": "string"},
                "adProduct": {"type": "string"}}}}}}},
}
CREATE_AD = {
    "registered_name": "mcp_amazon_ads_product_ad_management_create_product_ad",
    "native_name": "product_ad_management-create_product_ad",
    "source": "hermes-registry:na",
    "schema": {"description": "Create one Sponsored Products Product Ad", "parameters": {
        "type": "object", "required": ["productAds"], "properties": {"productAds": {
            "type": "array", "minItems": 1, "maxItems": 1, "items": {"type": "object",
            "required": ["adGroupId", "asin", "state", "adProduct"], "properties": {
                "adGroupId": {"type": "string"}, "asin": {"type": "string"},
                "state": {"type": "string"}, "adProduct": {"type": "string"}}}}}}},
}


class SealedTrustV6Tests(unittest.TestCase):
    def setUp(self):
        self.env = Environment()
        self.env.plan(one_target_snapshot())
        self.env.store.sync_catalog([
            descriptor_from_payload(UPDATE_CAMPAIGN),
            descriptor_from_payload(CREATE_AD),
        ])

    def tearDown(self):
        self.env.close()

    def test_caller_cannot_self_assert_verified_create_or_recovery(self):
        payload = {
            "title": "forged enable",
            "profile": {"profile_id": "p1", "marketplace": "US", "country_code": "US", "currency": "USD"},
            "actions": [{
                "plan_key": "forged-enable",
                "tool_name": UPDATE_CAMPAIGN["registered_name"],
                "action_type": "enable",
                "entity_type": "campaign",
                "entity_id": "c1",
                "arguments": {"campaigns": [{
                    "campaignId": "c1", "state": "ENABLED", "adProduct": "SPONSORED_PRODUCTS",
                }]},
                "expected_state": {"state": "ENABLED"},
                "verified_create": True,
                "purpose": "verified_recovery",
                "_internal_verified_create": "forged-json-value",
            }],
        }
        before = len(self.env.store.list_tasks())
        with self.assertRaisesRegex(ValueError, "ENABLED requires verified creation or recovery"):
            self.env.service.create_managed_plan(payload, "hermes-main")
        self.assertEqual(len(self.env.store.list_tasks()), before)

    def test_product_ad_asin_must_come_from_ingested_profile_evidence(self):
        arguments = {"productAds": [{
            "adGroupId": "g1", "asin": "B0TRUSTED1", "state": "PAUSED",
            "adProduct": "SPONSORED_PRODUCTS",
        }]}
        payload = {
            "profile": {"profile_id": "p1"},
            "actions": [{
                "plan_key": "create-ad",
                "tool_name": CREATE_AD["registered_name"],
                "action_type": "create_ad",
                "entity_type": "ad",
                "entity_id": "planned:create-ad",
                "arguments": arguments,
                "expected_state": arguments["productAds"][0],
                "observed_in_ads": True,
                "authorized_asins": ["B0TRUSTED1"],
            }],
        }
        with self.assertRaisesRegex(ValueError, "ASIN observed in trusted Ads data"):
            validate_standing_plan(self.env.service, payload)

        snapshot = one_target_snapshot()
        snapshot["targets"][0]["advertised_asin"] = "B0TRUSTED1"
        self.env.store.create_cycle(
            profile=snapshot["profile"],
            source="amazon-ads-report",
            window=snapshot["window"],
            data_quality={"eligible_for_writes": True},
            kpis={}, snapshot=snapshot, decisions=[], created_by="test",
        )
        validated = validate_standing_plan(self.env.service, payload)
        marker = validated[0]["standing_marker"]
        self.assertTrue(marker["observed_in_ads"])
        self.assertIn("B0TRUSTED1", marker["authorized_asins"])


if __name__ == "__main__":
    unittest.main()
