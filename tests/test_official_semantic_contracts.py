from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sync_official_contracts.py"


def load():
    spec = importlib.util.spec_from_file_location("semantic_contract_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SemanticContractTests(unittest.TestCase):
    def test_normalizes_schema_without_secret_values(self):
        module = load()
        raw = json.dumps({
            "info": {"name": "Amazon Ads"},
            "item": [{
                "name": "Sponsored Products",
                "item": [{
                    "name": "Create campaign",
                    "request": {
                        "method": "POST",
                        "url": {"raw": "https://advertising-api.amazon.com/sp/v3/campaigns?x=1"},
                        "header": [
                            {"key": "Authorization", "value": "Bearer literal-secret"},
                            {"key": "Amazon-Advertising-API-ClientId", "value": "{{clientId}}"},
                        ],
                        "body": {
                            "mode": "raw",
                            "raw": '{"campaigns":[{"name":"x","budget":10}]}',
                            "options": {"raw": {"language": "json"}},
                        },
                    },
                }],
            }],
        }).encode()
        manifest = module.summarize(raw, "fixture")
        endpoint = manifest["endpoints"][0]
        self.assertEqual(endpoint["method"], "POST")
        self.assertEqual(endpoint["endpoint_path"], "/sp/v3/campaigns")
        self.assertIn("$.campaigns[]", endpoint["body"]["json_paths"])
        auth = next(item for item in endpoint["headers"] if item["name"] == "authorization")
        self.assertTrue(auth["redacted"])
        self.assertNotIn("literal-secret", json.dumps(manifest))

    def test_removed_or_changed_endpoint_is_breaking(self):
        module = load()
        base = {"endpoints": [{
            "method": "GET", "path": "/v1/profiles", "contract_id": "a",
            "display_path": "Profiles", "body": {}, "headers": [],
        }]}
        current = {"endpoints": [{
            "method": "GET", "path": "/v1/profiles", "contract_id": "b",
            "display_path": "Profiles", "body": {}, "headers": [],
        }]}
        diff = module.semantic_diff(base, current)
        self.assertTrue(diff["breaking"])
        self.assertEqual(diff["changed"], ["GET /v1/profiles"])


if __name__ == "__main__":
    unittest.main()
