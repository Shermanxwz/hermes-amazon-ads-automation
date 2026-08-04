from __future__ import annotations
import unittest
from amazon_ads_control.policy import Guardrails, classify_tool, is_amazon_ads_tool, match_planned_action, redact, redact_text, validate_write

class PolicyEdgeTests(unittest.TestCase):
    def test_classification_and_non_ads(self):
        self.assertFalse(is_amazon_ads_tool("weather_search")); self.assertEqual(classify_tool("weather_search"),"other")
        self.assertEqual(classify_tool("mcp_amazon_ads_campaign_query_campaign"),"read")
        self.assertEqual(classify_tool("mcp_amazon_ads_campaign_update_campaign"),"write")
        self.assertEqual(classify_tool("mcp_amazon_ads_mystery"),"unknown")
    def test_redaction_limits_depth_and_size(self):
        nested={"password":"secret","safe":"x","deep":{"a":{"b":{"c":{"d":{"e":{"token":"secret"}}}}}}}
        result=redact(nested); self.assertEqual(result["password"],"[redacted]"); self.assertIn("truncated",str(result))
        self.assertLessEqual(len(redact_text("x"*9000)),8001)
        self.assertNotIn("abc",redact_text("refresh_token=abc"))
    def test_planned_matching(self):
        actions=[{"tool_contains":"update_target","entity_id":"t1","field":"bid","after":0.8,"idempotency_key":"k"}]
        item,reason=match_planned_action("mcp_amazon_ads_update_target",{"targetId":"t1","bid":0.8},actions)
        self.assertEqual(item["plan_key"],"k"); self.assertIn("matched",reason)
        self.assertIsNone(match_planned_action("x",{},actions)[0])
    def test_guardrails(self):
        guard=Guardrails.from_mapping({"max_bid_change_pct":10,"max_budget_change_pct":20})
        self.assertFalse(validate_write("delete_campaign",{},guard)[0])
        self.assertFalse(validate_write("update_bid",{"change_percent":"bad"},guard)[0])
        self.assertFalse(validate_write("update_bid",{"change_percent":11},guard)[0])
        self.assertTrue(validate_write("update_budget",{"changePercent":20},guard)[0])

if __name__ == "__main__": unittest.main()
