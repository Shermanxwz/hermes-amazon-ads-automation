from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from amazon_ads_control.strategy import OptimizationEngine, StrategyPolicy

UTC = timezone.utc


def window():
    end = datetime.now(UTC).date() - timedelta(days=3)
    return {"start": (end - timedelta(days=13)).isoformat(), "end": end.isoformat(), "days": 14}


class StrategyV4Tests(unittest.TestCase):
    def snapshot(self):
        return {
            "source": "amazon-ads-report", "profile": {"profile_id": "p1", "country_code": "US"}, "window": window(),
            "account": {"impressions": 10000, "clicks": 200, "spend": 260, "sales": 900, "orders": 24},
            "targets": [
                {"target_id": "winner-target", "campaign_id": "winner", "ad_group_id": "g1", "state": "ENABLED", "ad_product": "SPONSORED_PRODUCTS", "bid": 1.0, "impressions": 5000, "clicks": 120, "spend": 120, "sales": 720, "orders": 18},
                {"target_id": "loser-target", "campaign_id": "loser", "ad_group_id": "g2", "state": "ENABLED", "ad_product": "SPONSORED_PRODUCTS", "bid": 1.0, "impressions": 5000, "clicks": 80, "spend": 140, "sales": 180, "orders": 6},
            ],
            "campaigns": [
                {"campaign_id": "winner", "state": "ENABLED", "ad_product": "SPONSORED_PRODUCTS", "budget": 40, "impressions": 5000, "clicks": 120, "spend": 120, "sales": 720, "orders": 18, "budget_usage_percent": 95},
                {"campaign_id": "loser", "state": "ENABLED", "ad_product": "SPONSORED_PRODUCTS", "budget": 40, "impressions": 5000, "clicks": 80, "spend": 140, "sales": 180, "orders": 6, "budget_usage_percent": 90},
            ],
            "budget_usage": [{"campaign_id": "winner", "budget_usage_percent": 95}, {"campaign_id": "loser", "budget_usage_percent": 90}],
            "search_terms": [], "placements": [], "recommendations": [], "hourly": [],
        }

    def test_v4_emits_probability_evidence_and_sp_only_scope(self):
        plan = OptimizationEngine().plan(self.snapshot(), StrategyPolicy.from_mapping({}))
        self.assertEqual(plan.data_quality["decision_os_version"], "4.0")
        self.assertEqual(plan.data_quality["strategy_summary"]["objective"], "ads_attributed_target_acos")
        self.assertEqual(StrategyPolicy.from_mapping({}).auto_write_ad_products, ("SPONSORED_PRODUCTS",))
        self.assertTrue(all("posterior_acos" in d.evidence or d.rule_id.startswith("ADS-GLOBAL") for d in plan.decisions))

    def test_global_budget_allocator_is_exposure_neutral(self):
        plan = OptimizationEngine().plan(self.snapshot(), StrategyPolicy.from_mapping({"posterior_reduce_probability": 0.70, "posterior_scale_probability": 0.70}))
        budget = [d for d in plan.decisions if d.rule_id.startswith("ADS-GLOBAL-BUDGET")]
        if budget:
            increases = sum(max(0, d.payload["after"] - d.payload["before"]) for d in budget)
            decreases = sum(max(0, d.payload["before"] - d.payload["after"]) for d in budget)
            self.assertAlmostEqual(increases, decreases, places=2)


if __name__ == "__main__":
    unittest.main()
