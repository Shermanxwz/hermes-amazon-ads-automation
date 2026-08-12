from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from amazon_ads_control.catalog import descriptor_from_payload
from amazon_ads_control.db import Store
from amazon_ads_control.service import ControlService

CREATE_CAMPAIGN = {
    "registered_name": "mcp_amazon_ads_campaign_management_create_campaign",
    "native_name": "campaign_management-create_campaign",
    "source": "hermes-registry:na",
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
UPDATE_CAMPAIGN = {
    "registered_name": "mcp_amazon_ads_campaign_management_update_campaign",
    "native_name": "campaign_management-update_campaign",
    "source": "hermes-registry:na",
    "schema": {
        "description": "Update one campaign",
        "parameters": {
            "type": "object",
            "required": ["campaigns"],
            "properties": {
                "campaigns": {
                    "type": "array", "minItems": 1, "maxItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["campaignId", "budget"],
                        "properties": {
                            "campaignId": {"type": "string"},
                            "budget": {"type": "number", "minimum": 1},
                        },
                    },
                }
            },
        },
    },
}
EXPAND_CAMPAIGN = {
    "registered_name": "mcp_amazon_ads_campaign_management_expand_campaign",
    "native_name": "campaign_management-expand_campaign",
    "source": "hermes-registry:na",
    "schema": {
        "description": "Expand campaign to another marketplace",
        "parameters": {
            "type": "object",
            "properties": {"campaignId": {"type": "string"}},
        },
    },
}


class StructuralHardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "state.db")
        self.service = ControlService(self.store)
        self.store.sync_catalog([
            descriptor_from_payload(CREATE_CAMPAIGN),
            descriptor_from_payload(UPDATE_CAMPAIGN),
            descriptor_from_payload(EXPAND_CAMPAIGN),
        ])

    def tearDown(self):
        self.temp.cleanup()

    def base(self, action):
        return {
            "title": "Structural validation",
            "profile": {
                "profile_id": "p1", "marketplace": "US",
                "country_code": "US", "currency": "USD",
            },
            "actions": [action],
        }

    def test_budget_exposure_is_derived_from_exact_arguments(self):
        result = self.service.create_managed_plan(self.base({
            "plan_key": "campaign",
            "tool_name": CREATE_CAMPAIGN["registered_name"],
            "action_type": "create_campaign",
            "entity_type": "campaign",
            "entity_id": "planned:campaign",
            "arguments": {"campaigns": [{"name": "Budget Derived", "budget": 42}]},
            "expected_state": {"name": "Budget Derived", "budget": 42},
        }), "main")
        action = result["approval"]["plan"]["actions"][0]
        self.assertEqual(action["maximum_daily_budget"], 42)
        self.assertEqual(result["approval"]["maximum_daily_budget"], 42)

    def test_understated_budget_exposure_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "understates"):
            self.service.create_managed_plan(self.base({
                "plan_key": "campaign",
                "tool_name": CREATE_CAMPAIGN["registered_name"],
                "action_type": "create_campaign",
                "entity_id": "planned:campaign",
                "arguments": {"campaigns": [{"name": "Too Large", "budget": 50}]},
                "expected_state": {"name": "Too Large", "budget": 50},
                "maximum_daily_budget": 10,
            }), "main")

    def test_existing_entity_id_must_appear_in_write_arguments(self):
        with self.assertRaisesRegex(ValueError, "not present"):
            self.service.create_managed_plan(self.base({
                "plan_key": "update",
                "tool_name": UPDATE_CAMPAIGN["registered_name"],
                "action_type": "update_campaign",
                "entity_type": "campaign",
                "entity_id": "C-EXPECTED",
                "arguments": {"campaigns": [{"campaignId": "C-OTHER", "budget": 20}]},
                "expected_state": {"campaignId": "C-EXPECTED", "budget": 20},
            }), "main")

    def test_credentials_are_rejected_before_schema_or_approval(self):
        with self.assertRaisesRegex(ValueError, "forbidden credential"):
            self.service.create_managed_plan(self.base({
                "plan_key": "campaign",
                "tool_name": CREATE_CAMPAIGN["registered_name"],
                "action_type": "create_campaign",
                "entity_id": "planned:campaign",
                "arguments": {
                    "authorization": "Bearer secret",
                    "campaigns": [{"name": "No Secret", "budget": 10}],
                },
                "expected_state": {"name": "No Secret", "budget": 10},
            }), "main")

    def test_black_box_expansion_remains_permanently_blocked(self):
        with self.assertRaisesRegex(ValueError, "black-box"):
            self.service.create_managed_plan(self.base({
                "plan_key": "expand",
                "tool_name": EXPAND_CAMPAIGN["registered_name"],
                "action_type": "expand_campaign",
                "entity_type": "campaign",
                "entity_id": "C-1",
                "arguments": {"campaignId": "C-1"},
                "expected_state": {"campaignId": "C-1"},
            }), "main")


if __name__ == "__main__":
    unittest.main()
