from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from amazon_ads_control.catalog import descriptor_from_payload
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
