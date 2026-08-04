from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "hermes-plugin" / "amazon_ads_control"


def load_plugin():
    name = "amazon_ads_regional_plugin_test"
    for key in [item for item in sys.modules if item == name or item.startswith(name + ".")]:
        sys.modules.pop(key, None)
    spec = importlib.util.spec_from_file_location(
        name,
        PLUGIN / "__init__.py",
        submodule_search_locations=[str(PLUGIN)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class RegionalRegistry:
    def __init__(self):
        self.toolsets = {
            "mcp-amazon-ads-na": [
                "mcp_amazon_ads_na_campaign_management_query_campaigns",
            ],
            "mcp-amazon-ads-eu": [
                "mcp_amazon_ads_eu_campaign_management_query_campaigns",
            ],
            "mcp-amazon-ads-fe": [
                "mcp_amazon_ads_fe_campaign_management_create_campaign",
            ],
            "mcp-unrelated": ["mcp_unrelated_ping"],
        }

    def get_registered_toolset_names(self):
        return sorted(self.toolsets)

    def get_tool_names_for_toolset(self, name):
        return list(self.toolsets.get(name, []))

    def get_schema(self, name):
        if "create_campaign" in name:
            return {
                "description": "Create one campaign",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "campaigns": {"type": "array", "maxItems": 1},
                    },
                },
            }
        return {
            "description": "Query campaigns",
            "parameters": {"type": "object", "properties": {}},
        }


class RegionalPluginTests(unittest.TestCase):
    def setUp(self):
        tools_package = types.ModuleType("tools")
        registry_module = types.ModuleType("tools.registry")
        registry_module.registry = RegionalRegistry()
        tools_package.registry = registry_module
        self.modules = patch.dict(
            sys.modules,
            {"tools": tools_package, "tools.registry": registry_module},
        )
        self.modules.start()
        self.environment = patch.dict(
            os.environ,
            {"ADS_MCP_DEFAULT_REGION": "na"},
            clear=False,
        )
        self.environment.start()
        self.plugin = load_plugin()

    def tearDown(self):
        self.environment.stop()
        self.modules.stop()

    def test_discovers_all_regional_toolsets_and_tags_source(self):
        rows = self.plugin._registry_catalog()
        self.assertEqual(len(rows), 3)
        by_name = {row["registered_name"]: row for row in rows}
        self.assertEqual(
            by_name["mcp_amazon_ads_na_campaign_management_query_campaigns"]["source"],
            "hermes-registry:na",
        )
        self.assertEqual(
            by_name["mcp_amazon_ads_eu_campaign_management_query_campaigns"]["source"],
            "hermes-registry:eu",
        )
        fe = by_name["mcp_amazon_ads_fe_campaign_management_create_campaign"]
        self.assertEqual(fe["source"], "hermes-registry:fe")
        self.assertEqual(fe["native_name"], "campaign_management_create_campaign")
        self.assertEqual(fe["server_name"], "amazon-ads")

    def test_environment_can_narrow_the_enabled_regional_toolsets(self):
        with patch.dict(
            os.environ,
            {"ADS_MCP_TOOLSETS": "mcp-amazon-ads-fe"},
            clear=False,
        ):
            rows = self.plugin._registry_catalog()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "hermes-registry:fe")

    def test_unknown_default_region_fails_closed(self):
        registry = sys.modules["tools.registry"].registry
        registry.toolsets["mcp-amazon-ads"] = [
            "mcp_amazon_ads_campaign_management_query_campaigns",
        ]
        with patch.dict(os.environ, {"ADS_MCP_DEFAULT_REGION": "unknown"}, clear=False):
            with self.assertRaisesRegex(ValueError, "na, eu or fe"):
                self.plugin._registry_catalog()


if __name__ == "__main__":
    unittest.main()
