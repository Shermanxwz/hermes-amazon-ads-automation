import unittest
from amazon_ads_control.policy import Guardrails, classify_tool, redact, redact_text, validate_write

class PolicyTests(unittest.TestCase):
    def test_classifies_ads_tools(self):
        self.assertEqual(classify_tool("campaign_management-query_campaign"), "read")
        self.assertEqual(classify_tool("campaign_management-query_target"), "read")
        self.assertEqual(classify_tool("campaign_management-update_campaign"), "write")
        self.assertEqual(classify_tool("terminal"), "other")

    def test_unknown_ads_fails_classification(self):
        self.assertEqual(classify_tool("amazon_ads_magic_campaign"), "unknown")

    def test_guardrails(self):
        g = Guardrails(max_bid_change_pct=15)
        self.assertEqual(validate_write("amazon_ads_update_bid", {"change_percent": 10}, g)[0], True)
        self.assertEqual(validate_write("amazon_ads_update_bid", {"change_percent": 30}, g)[0], False)
        self.assertEqual(validate_write("amazon_ads_delete_campaign", {}, g)[0], False)

    def test_redacts_secrets(self):
        out = redact({"access_token": "abc", "nested": {"password": "x"}, "safe": "ok"})
        self.assertEqual(out["access_token"], "[redacted]")
        self.assertEqual(out["nested"]["password"], "[redacted]")
        self.assertEqual(out["safe"], "ok")
        text = redact_text("Authorization: Bearer abc.def access_token=xyz client_secret='topsecret'")
        self.assertNotIn("abc.def", text)
        self.assertNotIn("xyz", text)
        self.assertNotIn("topsecret", text)
        self.assertEqual(text.count("[redacted]"), 3)

if __name__ == '__main__': unittest.main()
