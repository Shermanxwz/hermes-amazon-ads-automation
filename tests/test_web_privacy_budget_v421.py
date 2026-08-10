from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "control-plane" / "amazon_ads_control" / "static" / "index.html"
APP = ROOT / "control-plane" / "amazon_ads_control" / "static" / "app_v3.js"


class WebPrivacyBudgetV421Tests(unittest.TestCase):
    def test_owner_budget_controls_are_present(self):
        html = HTML.read_text(encoding="utf-8")
        for identifier in (
            "max-daily-ad-spend", "exploration-budget-pct", "budget-exposure",
            "budget-remaining", "exploration-remaining", "budget-guard-state",
        ):
            self.assertIn(f'id="{identifier}"', html)
        script = APP.read_text(encoding="utf-8")
        self.assertIn("max_daily_ad_spend", script)
        self.assertIn("exploration_budget_pct", script)
        self.assertIn("dashboard.budget_guard", script)

    def test_dashboard_source_contains_no_raw_account_identifiers(self):
        html = HTML.read_text(encoding="utf-8")
        script = APP.read_text(encoding="utf-8")
        combined = html + "\n" + script
        self.assertNotRegex(combined, r"\b\d{14,20}\b")
        self.assertNotIn("amzn1.ads-account", combined)
        self.assertIn("已绑定的 US Sponsored Products Profile", html)


if __name__ == "__main__":
    unittest.main()
