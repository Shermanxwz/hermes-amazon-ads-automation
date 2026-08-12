from __future__ import annotations

import json
import unittest

from scripts.check_unified_api_contract import summarize


class UnifiedContractV4Tests(unittest.TestCase):
    def test_ga_is_required_and_beta_is_observe_only(self):
        prod = [{"name": resource, "item": [{"name": "Query", "request": {"method": "POST", "url": {"raw": f"{{{{api_url}}}}/adsApi/v1/query/{resource}"}}}]} for resource in ("Campaigns", "AdGroups", "Ads", "Targets", "AdAssociations", "CampaignForecasts", "Recommendations", "RecommendationTypes")]
        beta = [{"name": resource, "item": [{"name": "Query", "request": {"method": "POST", "url": {"raw": f"{{{{api_url}}}}/adsApi/v1/query/{resource}"}}}]} for resource in ("Reports", "Events", "Rules", "RuleLinks", "Labels")]
        document = {"item": [{"name": "Unified API — Prod (3P)", "item": prod}, {"name": "Unified API — Beta", "item": beta}]}
        manifest = summarize(json.dumps(document).encode(), "fixture")
        self.assertTrue(manifest["ok"])
        self.assertIn("Reports", manifest["beta_observe_only"])
        self.assertEqual(manifest["policy"]["beta"], "observe-only; never a sole sealed execution or reporting dependency")


if __name__ == "__main__":
    unittest.main()
