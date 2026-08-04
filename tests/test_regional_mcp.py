from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from amazon_ads_control.catalog import descriptor_from_payload
from amazon_ads_control.db import Store
from amazon_ads_control.regional_mcp import profile_region, tool_region
from amazon_ads_control.service import ControlService


def campaign_tool(region: str) -> dict:
    return {
        "registered_name": f"mcp_amazon_ads_{region}_campaign_management_create_campaign",
        "native_name": "campaign_management-create_campaign",
        "server_name": "amazon-ads",
        "source": f"hermes-registry:{region}",
        "schema": {
            "description": "Create one campaign",
            "parameters": {
                "type": "object",
                "required": ["campaigns"],
                "properties": {
                    "campaigns": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 1,
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


def report_tool(region: str) -> dict:
    return {
        "registered_name": f"mcp_amazon_ads_{region}_reporting_create_report",
        "native_name": "reporting-create_report",
        "server_name": "amazon-ads",
        "source": f"hermes-registry:{region}",
        "schema": {
            "description": "Create a report job for one Profile",
            "parameters": {
                "type": "object",
                "required": ["profileId"],
                "properties": {"profileId": {"type": "string"}},
            },
        },
    }


def profile_tool(region: str) -> dict:
    return {
        "registered_name": f"mcp_amazon_ads_{region}_account_management_query_advertiser_accounts",
        "native_name": "account_management-query_advertiser_accounts",
        "server_name": "amazon-ads",
        "source": f"hermes-registry:{region}",
        "schema": {
            "description": "Query advertiser accounts and Profiles",
            "parameters": {"type": "object", "properties": {}},
        },
    }


class RegionalMCPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "state.db")
        self.service = ControlService(self.store)
        descriptors = []
        for region in ("na", "eu", "fe"):
            descriptors.extend([
                descriptor_from_payload(campaign_tool(region)),
                descriptor_from_payload(report_tool(region)),
                descriptor_from_payload(profile_tool(region)),
            ])
        self.store.sync_catalog(descriptors)
        self.store.upsert_profile({
            "profile_id": "profile-sg",
            "marketplace": "SG",
            "country_code": "SG",
            "currency": "SGD",
        })

    def tearDown(self):
        self.temp.cleanup()

    def payload(self, region: str, country: str) -> dict:
        return {
            "title": f"Create {region.upper()} campaign",
            "profile": {
                "profile_id": f"profile-{country.lower()}",
                "marketplace": country,
                "country_code": country,
                "currency": "SGD" if country == "SG" else "USD",
            },
            "actions": [{
                "plan_key": f"campaign-{region}",
                "tool_name": f"mcp_amazon_ads_{region}_campaign_management_create_campaign",
                "action_type": "create_campaign",
                "entity_type": "campaign",
                "entity_id": f"planned:campaign-{region}",
                "arguments": {
                    "campaigns": [{"name": f"{region}-campaign", "budget": 10}],
                },
                "expected_state": {"name": f"{region}-campaign", "budget": 10},
                "maximum_daily_budget": 10,
            }],
        }

    def test_country_mapping_covers_na_eu_and_fe(self):
        self.assertEqual(profile_region({"country_code": "US"}), "na")
        self.assertEqual(profile_region({"country_code": "DE"}), "eu")
        self.assertEqual(profile_region({"country_code": "SG"}), "fe")
        self.assertIsNone(profile_region({"country_code": "XX"}))
        self.assertEqual(tool_region({"source": "hermes-registry:fe"}), "fe")
        self.assertIsNone(tool_region({"source": "hermes-registry"}))

    def test_matching_singapore_profile_and_fe_tool_can_request_approval(self):
        result = self.service.create_managed_plan(self.payload("fe", "SG"), "main")
        self.assertEqual(result["approval"]["status"], "pending")
        self.assertEqual(result["approval"]["profile_id"], "profile-sg")

    def test_cross_region_structural_plan_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "region na, but profile requires fe"):
            self.service.create_managed_plan(self.payload("na", "SG"), "main")

    def test_unknown_marketplace_is_rejected_for_tagged_tool(self):
        with self.assertRaisesRegex(ValueError, "recognized country_code/marketplace"):
            self.service.create_managed_plan(self.payload("fe", "XX"), "main")

    def test_matching_regional_report_job_is_allowed(self):
        result = self.service.authorize_tool({
            "tool_name": "mcp_amazon_ads_fe_reporting_create_report",
            "args": {"profileId": "profile-sg"},
            "session_id": "main-session",
            "tool_call_id": "report-fe",
        })
        self.assertTrue(result["allowed"], result)
        self.assertEqual(result["operation"], "job")

    def test_cross_region_report_job_is_blocked_and_audited(self):
        result = self.service.authorize_tool({
            "tool_name": "mcp_amazon_ads_na_reporting_create_report",
            "args": {"profileId": "profile-sg"},
            "session_id": "main-session",
            "tool_call_id": "report-na",
        })
        self.assertFalse(result["allowed"])
        self.assertIn("requires Amazon Ads MCP region fe", result["reason"])
        action = self.store.get_action(result["action_id"])
        self.assertFalse(action["allowed"])
        self.assertEqual(action["operation"], "job")

    def test_regional_job_without_profile_is_blocked(self):
        result = self.service.authorize_tool({
            "tool_name": "mcp_amazon_ads_fe_reporting_create_report",
            "args": {},
            "session_id": "main-session",
            "tool_call_id": "report-no-profile",
        })
        self.assertFalse(result["allowed"])
        self.assertIn("known Profile ID", result["reason"])

    def test_profile_discovery_is_allowed_before_profile_binding(self):
        result = self.service.authorize_tool({
            "tool_name": "mcp_amazon_ads_fe_account_management_query_advertiser_accounts",
            "args": {},
            "session_id": "main-session",
            "tool_call_id": "profiles-fe",
        })
        self.assertTrue(result["allowed"], result)
        self.assertEqual(result["operation"], "read")


if __name__ == "__main__":
    unittest.main()
