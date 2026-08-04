from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from amazon_ads_control.catalog import descriptor_from_payload
from amazon_ads_control.catalog_region_hardening import install as install_catalog_region_hardening
from amazon_ads_control.db import Store
from amazon_ads_control.service import ControlService


def account_discovery(source: str) -> dict:
    return {
        "registered_name": "mcp_amazon_ads_account_management_query_advertiser_accounts",
        "native_name": "account_management-query_advertiser_accounts",
        "server_name": "amazon-ads",
        "source": source,
        "schema": {
            "description": "Query advertiser accounts and Profiles",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def report_tool(source: str) -> dict:
    return {
        "registered_name": "mcp_amazon_ads_reporting_create_report",
        "native_name": "reporting-create_report",
        "server_name": "amazon-ads",
        "source": source,
        "schema": {
            "description": "Create a report job for one Profile",
            "parameters": {
                "type": "object",
                "required": ["profileId"],
                "properties": {"profileId": {"type": "string"}},
            },
        },
    }


class RegionalFailClosedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "state.db")
        self.service = ControlService(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_catalog_hardening_install_is_idempotent(self):
        original = ControlService.sync_catalog
        install_catalog_region_hardening()
        self.assertIs(ControlService.sync_catalog, original)

    def test_service_catalog_sync_requires_non_empty_tool_array(self):
        for payload in ({}, {"tools": []}, {"tools": "not-a-list"}):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ValueError, "non-empty array"):
                    self.service.sync_catalog(payload)

    def test_service_catalog_sync_rejects_non_object_tool(self):
        with self.assertRaisesRegex(ValueError, "must be an object"):
            self.service.sync_catalog({"tools": ["not-an-object"]})

    def test_service_catalog_sync_rejects_non_amazon_registered_name(self):
        tool = report_tool("hermes-registry:na")
        tool["registered_name"] = "mcp_other_reporting_create_report"
        with self.assertRaisesRegex(ValueError, "outside mcp-amazon-ads"):
            self.service.sync_catalog({"tools": [tool]})

    def test_service_catalog_sync_preserves_validated_region_source(self):
        tool = report_tool("  HERMES-REGISTRY:FE  ")
        self.service.sync_catalog({"tools": [tool]})
        stored = self.store.get_tool(tool["registered_name"])
        self.assertEqual(stored["source"], "hermes-registry:fe")
        self.assertEqual(stored["semantic"], "job")
        self.assertEqual(stored["family"], "report")

    def test_service_catalog_sync_rejects_untrusted_source_names(self):
        tool = report_tool("user-supplied:fe")
        with self.assertRaisesRegex(ValueError, "source must be one of"):
            self.service.sync_catalog({"tools": [tool]})

    def test_service_catalog_sync_without_source_remains_untagged(self):
        tool = report_tool("")
        self.service.sync_catalog({"tools": [tool]})
        self.assertEqual(
            self.store.get_tool(tool["registered_name"])["source"],
            "hermes-registry",
        )

    def test_untagged_profile_discovery_is_blocked(self):
        tool = account_discovery("hermes-registry")
        self.store.sync_catalog([descriptor_from_payload(tool)])
        result = self.service.authorize_tool({
            "tool_name": tool["registered_name"],
            "args": {},
            "session_id": "main",
            "tool_call_id": "untagged-discovery",
        })
        self.assertFalse(result["allowed"])
        self.assertIn("missing an explicit NA/EU/FE region tag", result["reason"])

    def test_region_source_change_is_catalog_drift(self):
        first = report_tool("hermes-registry:fe")
        second = report_tool("hermes-registry:na")
        self.store.sync_catalog([descriptor_from_payload(first)])
        result = self.store.sync_catalog([descriptor_from_payload(second)])
        self.assertIn(first["registered_name"], result["drifted"])
        self.assertTrue(self.store.get_tool(first["registered_name"])["drifted"])
        self.assertTrue(any(
            alert["code"] == "MCP_REGION_DRIFT"
            for alert in self.store.list_alerts()
        ))


if __name__ == "__main__":
    unittest.main()
