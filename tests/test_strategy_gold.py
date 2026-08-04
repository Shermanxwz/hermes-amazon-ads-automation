from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import unittest

from amazon_ads_control.strategy import OptimizationEngine, StrategyPolicy
from helpers import one_target_snapshot

UTC = timezone.utc
PRODUCT = "SPONSORED_PRODUCTS"


class GoldStrategyTests(unittest.TestCase):
    def setUp(self):
        self.engine = OptimizationEngine()
        self.policy = StrategyPolicy()

    def test_invalid_row_is_rejected_without_poisoning_valid_rows(self):
        snapshot = one_target_snapshot()
        broken = deepcopy(snapshot["targets"][0])
        broken["target_id"] = "broken"
        broken["spend"] = "not-a-number"
        snapshot["targets"].append(broken)
        plan = self.engine.plan(snapshot, self.policy)
        self.assertTrue(plan.data_quality["eligible_for_writes"])
        self.assertIn("targets_rows_rejected", plan.data_quality["warnings"])
        self.assertEqual([item.entity_id for item in plan.decisions], ["t1"])

    def test_recent_change_enters_cooldown(self):
        snapshot = one_target_snapshot()
        snapshot["targets"][0]["last_bid_change_at"] = datetime.now(UTC).isoformat()
        plan = self.engine.plan(snapshot, self.policy)
        self.assertEqual(plan.decisions, [])

    def test_duplicate_exact_target_suppresses_scale_not_reduction(self):
        snapshot = one_target_snapshot(waste=False)
        snapshot["targets"][0].update({"keyword_text": "shoe", "match_type": "EXACT"})
        duplicate = deepcopy(snapshot["targets"][0])
        duplicate["target_id"] = "t2"
        snapshot["targets"].append(duplicate)
        plan = self.engine.plan(snapshot, self.policy)
        self.assertTrue(plan.data_quality["eligible_for_writes"])
        self.assertIn("overlapping_exact_targets", plan.data_quality["warnings"])
        self.assertEqual(plan.decisions, [])

    def test_budget_and_placement_can_reduce_local_loss(self):
        snapshot = one_target_snapshot()
        snapshot["targets"] = []
        snapshot["campaigns"] = [{
            "campaign_id": "c1", "ad_product": PRODUCT, "state": "ENABLED", "budget": 100,
            "clicks": 50, "spend": 90, "sales": 100, "orders": 4,
        }]
        snapshot["budget_usage"] = [{"campaign_id": "c1", "budget_usage_percent": 90}]
        snapshot["placements"] = [{
            "campaign_id": "c1", "ad_product": PRODUCT, "placement": "PRODUCT_PAGES",
            "adjustment_percent": 30, "clicks": 50, "spend": 90,
            "sales": 100, "orders": 4,
        }]
        rules = {item.rule_id for item in self.engine.plan(snapshot, self.policy).decisions}
        self.assertEqual(rules, {"ADS-BUDGET-CONTAIN-LOSS", "ADS-PLACEMENT-REDUCE"})

    def test_string_false_does_not_enable_recommendations(self):
        policy = StrategyPolicy.from_mapping({
            "allow_official_recommendation_apply": "false",
            "decision_cooldown_hours": 48,
        })
        self.assertFalse(policy.allow_official_recommendation_apply)


if __name__ == "__main__":
    unittest.main()
