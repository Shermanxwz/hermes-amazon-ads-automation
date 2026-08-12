from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from amazon_ads_control import budget_reservation as br

UTC = timezone.utc


class BudgetReservationEdgeV423Tests(unittest.TestCase):
    def test_reservation_lock_has_portable_process_fallback(self):
        previous = br.fcntl; br.fcntl = None
        try:
            with tempfile.TemporaryDirectory() as td:
                store = SimpleNamespace(path=Path(td) / "state.db")
                with br._reservation_lock(store): self.assertTrue(True)
        finally:
            br.fcntl = previous

    def test_invalid_spend_timezone_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "invalid daily spend timezone"):
            br._day_window("Not/AZone", datetime.now(UTC))

    def test_platform_buffer_is_bounded_not_blanket_two_x(self):
        decision = {"action_type": "create_campaign", "payload": {"approved_args": {"campaigns": [{"budget": 40}]}}}
        self.assertEqual(br._decision_reserve_amount(object(), decision, None, 100, 2.0, 1.0, 5.0), 45.0)

    def test_non_budget_increase_gets_small_atomic_reserve(self):
        decision = {"action_type": "update_bid", "payload": {"field": "bid", "before": 1, "after": 1.2}}
        self.assertEqual(br._decision_reserve_amount(object(), decision, None, 100, 2.0, 1.0, 5.0), 1.0)

    def test_risk_reduction_needs_no_financial_reserve(self):
        decision = {"action_type": "update_bid", "payload": {"field": "bid", "before": 1.2, "after": 1}}
        self.assertEqual(br._decision_reserve_amount(object(), decision, None, 100, 2.0, 1.0, 5.0), 0.0)

    def test_sp_traffic_filter_rejects_other_products_and_nontraffic(self):
        self.assertTrue(br._is_sp_traffic("sp-traffic", {"adProduct": "SPONSORED_PRODUCTS"}))
        self.assertFalse(br._is_sp_traffic("budget-usage", {"adProduct": "SPONSORED_PRODUCTS"}))
        self.assertFalse(br._is_sp_traffic("sd-traffic", {"adProduct": "SPONSORED_DISPLAY"}))

    def test_payload_date_prefers_explicit_metric_date(self):
        result = br._payload_date({"date": "2026-08-11"}, "2026-08-10T23:00:00+00:00", "2026-08-10T23:00:00+00:00", "America/Los_Angeles")
        self.assertEqual(result, "2026-08-11")


if __name__ == "__main__":
    unittest.main()
