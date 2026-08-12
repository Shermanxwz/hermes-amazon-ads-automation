from datetime import datetime, timedelta, timezone
import unittest
from amazon_ads_control.strategy import OptimizationEngine, StrategyPolicy
from helpers import dates, one_target_snapshot

UTC=timezone.utc
PRODUCT="SPONSORED_PRODUCTS"


class StrategyTests(unittest.TestCase):
    def setUp(self): self.engine=OptimizationEngine(); self.policy=StrategyPolicy()

    def test_waste_bid_decrease(self):
        plan=self.engine.plan(one_target_snapshot(),self.policy)
        self.assertEqual(plan.decisions[0].rule_id,"ADS-TARGET-WASTE")
        self.assertEqual(plan.decisions[0].payload["after"],0.8)

    def test_scale_bid_increase(self):
        plan=self.engine.plan(one_target_snapshot(waste=False),self.policy)
        self.assertEqual(plan.decisions[0].rule_id,"ADS-TARGET-SCALE")
        self.assertEqual(plan.decisions[0].payload["after"],1.1)

    def test_paused_target_skipped(self):
        snapshot=one_target_snapshot(); snapshot["targets"][0]["state"]="PAUSED"
        self.assertEqual(self.engine.plan(snapshot,self.policy).decisions,[])

    def test_maturity_is_derived_not_trusted(self):
        snapshot=one_target_snapshot(); snapshot["window"]={**dates(lag=0),"attribution_mature":True}
        plan=self.engine.plan(snapshot,self.policy)
        self.assertFalse(plan.data_quality["eligible_for_writes"])
        self.assertIn("attribution_not_mature",plan.data_quality["missing_or_unsafe"])

    def test_stale_data_blocks_writes(self):
        snapshot=one_target_snapshot(); end=datetime.now(UTC).date()-timedelta(days=20); start=end-timedelta(days=13)
        snapshot["window"]={"start":start.isoformat(),"end":end.isoformat(),"days":14}
        plan=self.engine.plan(snapshot,self.policy)
        self.assertIn("data_too_stale",plan.data_quality["missing_or_unsafe"])

    def test_search_negative_and_harvest(self):
        s=one_target_snapshot(); s["targets"]=[]; s["search_terms"]=[
            {"search_term":"bad","campaign_id":"c","ad_group_id":"g","ad_product":PRODUCT,"clicks":15,"spend":25,"sales":0,"orders":0},
            {"search_term":"good","campaign_id":"c","ad_group_id":"g","ad_product":PRODUCT,"clicks":20,"spend":10,"sales":100,"orders":3,"already_exact":False},
        ]
        decisions=self.engine.plan(s,self.policy).decisions
        rules={d.rule_id for d in decisions}
        self.assertEqual(rules,{"ADS-SEARCH-NEGATIVE","ADS-SEARCH-HARVEST"})
        harvest=next(d for d in decisions if d.rule_id=="ADS-SEARCH-HARVEST")
        self.assertFalse(harvest.payload["migration"]["source_negative_automatic"])
        self.assertTrue(harvest.payload["migration"]["source_traffic_preserved"])
        self.assertIn("不会",harvest.reason)

    def test_budget_placement_and_recommendation(self):
        s=one_target_snapshot(); s["targets"]=[]
        s["campaigns"]=[{"campaign_id":"c","ad_product":PRODUCT,"state":"ENABLED","budget":100,"clicks":20,"spend":20,"sales":100,"orders":3}]
        s["budget_usage"]=[{"campaign_id":"c","budget_usage_percent":95}]
        s["placements"]=[{"campaign_id":"c","ad_product":PRODUCT,"placement":"TOP_OF_SEARCH","adjustment_percent":10,"clicks":20,"spend":10,"sales":100,"orders":3}]
        s["recommendations"]=[{"recommendation_id":"r","type":"BID","entity_id":"t","payload":{},"expected_state":{"bid":1}}]
        default_rules={d.rule_id for d in self.engine.plan(s,self.policy).decisions}
        self.assertEqual(default_rules,{"ADS-BUDGET-PACING-WINNER","ADS-PLACEMENT-TOS-SCALE"})
        opted_in=StrategyPolicy(allow_official_recommendation_apply=True)
        rules={d.rule_id for d in self.engine.plan(s,opted_in).decisions}
        self.assertEqual(rules,{"ADS-BUDGET-PACING-WINNER","ADS-PLACEMENT-TOS-SCALE","ADS-OFFICIAL-RECOMMENDATION"})

    def test_kpis(self):
        k=self.engine.plan(one_target_snapshot(waste=False),self.policy).kpis
        self.assertEqual(k["acos"],10.0); self.assertEqual(k["roas"],10.0); self.assertEqual(k["cpc"],0.5)

    def test_plan_key_deterministic(self):
        a=self.engine.plan(one_target_snapshot(),self.policy).decisions[0].plan_key
        b=self.engine.plan(one_target_snapshot(),self.policy).decisions[0].plan_key
        self.assertEqual(a,b)
