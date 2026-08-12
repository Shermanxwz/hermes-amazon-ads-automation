from __future__ import annotations

import unittest

from amazon_ads_control.probabilistic_acos import DelayModel, PosteriorConfig, estimate_acos_posterior


class ProbabilisticAcosV4Tests(unittest.TestCase):
    def test_zero_order_waste_has_high_over_max_probability_without_fake_certainty(self):
        posterior = estimate_acos_posterior(
            {"clicks": 30, "orders": 0, "sales": 0, "spend": 60},
            target_acos=30, max_acos=45, delay_model=DelayModel(), age_days=6,
            config=PosteriorConfig(prior_clicks=24, prior_cvr=0.08, default_aov=30),
        )
        self.assertGreater(posterior.p_acos_over_max, 0.90)
        self.assertLess(posterior.confidence, 1.0)
        self.assertGreater(posterior.expected_final_sales, 0)

    def test_small_winner_is_shrunk_and_large_winner_is_scale_confident(self):
        small = estimate_acos_posterior({"clicks": 2, "orders": 1, "sales": 40, "spend": 2}, target_acos=30, max_acos=45, age_days=6)
        large = estimate_acos_posterior({"clicks": 120, "orders": 18, "sales": 720, "spend": 120}, target_acos=30, max_acos=45, age_days=6)
        self.assertLess(small.confidence, large.confidence)
        self.assertGreater(large.p_acos_under_target, 0.80)

    def test_delay_model_does_not_treat_recent_sales_as_final(self):
        recent = estimate_acos_posterior({"clicks": 40, "orders": 2, "sales": 80, "spend": 30}, target_acos=30, max_acos=45, delay_model=DelayModel(), age_days=0)
        mature = estimate_acos_posterior({"clicks": 40, "orders": 2, "sales": 80, "spend": 30}, target_acos=30, max_acos=45, delay_model=DelayModel(), age_days=6)
        self.assertLess(recent.maturity, mature.maturity)
        self.assertGreaterEqual(recent.expected_final_sales, recent.observed_sales)


if __name__ == "__main__":
    unittest.main()
