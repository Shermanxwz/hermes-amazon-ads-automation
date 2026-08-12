from __future__ import annotations

from copy import deepcopy
import unittest

from amazon_ads_control.strategy import OptimizationEngine, StrategyPolicy
from helpers import one_target_snapshot

PRODUCT = "SPONSORED_PRODUCTS"


class StrategyEdgeTests(unittest.TestCase):
    def setUp(self):
        self.engine = OptimizationEngine()
        self.policy = StrategyPolicy()

    def unsafe(self, snapshot):
        plan = self.engine.plan(snapshot, self.policy)
        self.assertFalse(plan.data_quality["eligible_for_writes"])
        return set(plan.data_quality["missing_or_unsafe"])

    def test_untrusted_source_reversed_and_mismatched_windows(self):
        s = one_target_snapshot(); s["source"] = "spreadsheet"
        self.assertIn("untrusted_source", self.unsafe(s))
        s = one_target_snapshot(); s["window"]["start"], s["window"]["end"] = s["window"]["end"], s["window"]["start"]
        self.assertIn("window_start_after_end", self.unsafe(s))
        s = one_target_snapshot(); s["window"]["days"] = 999
        self.assertIn("window_days_mismatch", self.unsafe(s))

    def test_duplicate_target_negative_and_nonfinite_metrics(self):
        s = one_target_snapshot(); s["targets"].append(deepcopy(s["targets"][0]))
        self.assertIn("duplicate_target_ids", self.unsafe(s))
        s = one_target_snapshot(); s["account"]["spend"] = -1
        self.assertIn("invalid_account_spend", self.unsafe(s))
        s = one_target_snapshot(); s["account"]["sales"] = float("nan")
        self.assertIn("invalid_account_sales", self.unsafe(s))

    def test_same_search_term_in_two_ad_groups_is_not_collapsed(self):
        s = one_target_snapshot(); s["targets"] = []
        s["search_terms"] = [
            {"search_term": "shoe", "campaign_id": "c", "ad_group_id": "g1", "ad_product": PRODUCT, "clicks": 15, "spend": 25, "sales": 0, "orders": 0},
            {"search_term": "shoe", "campaign_id": "c", "ad_group_id": "g2", "ad_product": PRODUCT, "clicks": 15, "spend": 25, "sales": 0, "orders": 0},
        ]
        decisions = self.engine.plan(s, self.policy).decisions
        self.assertEqual(len(decisions), 2)
        self.assertEqual(len({d.plan_key for d in decisions}), 2)

    def test_recommendation_without_expected_state_is_not_executable(self):
        s = one_target_snapshot(); s["targets"] = []
        s["recommendations"] = [{"recommendation_id": "r", "type": "BID", "entity_id": "t", "payload": {"bid": 2}}]
        plan = self.engine.plan(s, StrategyPolicy(allow_official_recommendation_apply=True))
        self.assertNotIn("ADS-OFFICIAL-RECOMMENDATION", {d.rule_id for d in plan.decisions})

    def test_zero_placement_adjustment_can_scale(self):
        s = one_target_snapshot(); s["targets"] = []
        s["placements"] = [{"campaign_id": "c", "ad_product": PRODUCT, "placement": "TOP_OF_SEARCH", "adjustment_percent": 0, "clicks": 20, "spend": 10, "sales": 100, "orders": 3}]
        decisions = self.engine.plan(s, self.policy).decisions
        self.assertEqual(decisions[0].rule_id, "ADS-PLACEMENT-TOS-SCALE")
        self.assertEqual(decisions[0].payload["before"], 0)
        self.assertGreater(decisions[0].payload["after"], 0)

    def test_unknown_placement_is_rejected_before_reduction(self):
        s = one_target_snapshot(); s["targets"] = []
        s["placements"] = [{
            "campaign_id": "c", "ad_product": PRODUCT, "placement": "UNKNOWN_PLACEMENT",
            "adjustment_percent": 30, "clicks": 50, "spend": 90, "sales": 100, "orders": 4,
        }]
        plan = self.engine.plan(s, self.policy)
        self.assertEqual(plan.decisions, [])
        self.assertIn("unsupported_placement", plan.data_quality["rejected_rows"]["placements"]["0"])

    def test_non_sp_product_is_observe_only_in_routine_optimizer(self):
        s = one_target_snapshot()
        s["targets"][0]["ad_product"] = "SPONSORED_BRANDS"
        policy = StrategyPolicy.from_mapping({
            "auto_write_ad_products": ["SPONSORED_PRODUCTS", "SPONSORED_BRANDS"],
        })
        plan = self.engine.plan(s, policy)
        self.assertEqual(plan.decisions, [])
        self.assertEqual(plan.data_quality["auto_write_ad_products"], ["SPONSORED_PRODUCTS"])
        self.assertIn("ad_product_observe_only", plan.data_quality["rejected_rows"]["targets"]["0"])


if __name__ == "__main__":
    unittest.main()
