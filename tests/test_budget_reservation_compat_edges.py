from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from amazon_ads_control import budget_reservation_compat as compat

OVERDELIVERY = "amazon_daily_budget_max_spend_multiplier"


def fake_br(settings: dict, *, observation=None, committed=(0.0, 0.0), exploration=False):
    return SimpleNamespace(
        OVERDELIVERY_SETTING=OVERDELIVERY,
        _settings=lambda _conn: settings,
        _fresh_complete_live_exposure=lambda _conn, _profile, _age: observation,
        _committed_inside_transaction=lambda _store, _conn, _profile, _observation, _current: committed,
        _exploration=lambda _source: exploration,
    )


def settings(**updates):
    values = {
        "daily_budget_hard_cap_enabled": True,
        "max_daily_ad_spend": 100.0,
        "exploration_budget_pct": 20.0,
        "budget_guard_exploration_stop_pct": 80.0,
        "budget_guard_conservative_pct": 90.0,
        "budget_guard_live_read_max_age_seconds": 900,
        OVERDELIVERY: 2.0,
    }
    values.update(updates)
    return values


class BudgetReservationCompatEdgeTests(unittest.TestCase):
    def test_settings_fail_closed_when_guard_is_disabled(self):
        br = fake_br(settings(daily_budget_hard_cap_enabled=False))
        with self.assertRaisesRegex(ValueError, "unavailable or disabled"):
            compat._settings_or_raise(br, object())

    def test_settings_fail_closed_when_required_value_is_missing(self):
        values = settings()
        values.pop("exploration_budget_pct")
        br = fake_br(values)
        with self.assertRaisesRegex(ValueError, "settings are incomplete"):
            compat._settings_or_raise(br, object())

    def test_settings_fail_closed_when_cap_or_multiplier_is_invalid(self):
        br = fake_br(settings(max_daily_ad_spend=0))
        with self.assertRaisesRegex(ValueError, "configuration is invalid"):
            compat._settings_or_raise(br, object())

    def test_staged_enable_requires_profile_binding(self):
        br = fake_br(settings(), observation={"campaign_budget_sum": 0})
        with self.assertRaisesRegex(ValueError, "bound Profile"):
            compat._enforce_staged_enable(br, object(), object(), {"id": "d1"}, {})

    def test_staged_enable_requires_fresh_complete_campaign_observation(self):
        br = fake_br(settings(), observation=None)
        decision = {"id": "d1", "profile_id": "p1"}
        with self.assertRaisesRegex(ValueError, "fresh complete unpaginated"):
            compat._enforce_staged_enable(br, object(), object(), decision, {})

    def test_staged_enable_blocks_hard_cap_oversubscription(self):
        br = fake_br(
            settings(), observation={"campaign_budget_sum": 0}, committed=(51.0, 0.0),
        )
        decision = {"id": "d1", "profile_id": "p1"}
        with self.assertRaisesRegex(ValueError, "hard cap"):
            compat._enforce_staged_enable(br, object(), object(), decision, {})

    def test_staged_enable_blocks_exploration_pool_oversubscription(self):
        br = fake_br(
            settings(), observation={"campaign_budget_sum": 0},
            committed=(5.0, 11.0), exploration=True,
        )
        decision = {"id": "d1", "profile_id": "p1"}
        with self.assertRaisesRegex(ValueError, "exploration maximum-spend pool"):
            compat._enforce_staged_enable(br, object(), object(), decision, {})

    def test_staged_enable_stops_exploration_at_utilization_threshold(self):
        br = fake_br(
            settings(exploration_budget_pct=100.0, budget_guard_exploration_stop_pct=20.0),
            observation={"campaign_budget_sum": 0}, committed=(10.0, 10.0),
            exploration=True,
        )
        decision = {"id": "d1", "profile_id": "p1"}
        with self.assertRaisesRegex(ValueError, "new exploration is stopped"):
            compat._enforce_staged_enable(br, object(), object(), decision, {})

    def test_staged_enable_stops_positive_exposure_in_conservative_mode(self):
        br = fake_br(
            settings(budget_guard_conservative_pct=20.0),
            observation={"campaign_budget_sum": 0}, committed=(10.0, 0.0),
            exploration=False,
        )
        decision = {"id": "d1", "profile_id": "p1"}
        with self.assertRaisesRegex(ValueError, "conservative threshold"):
            compat._enforce_staged_enable(br, object(), object(), decision, {})

    def test_reservation_lock_has_portable_process_lock_fallback(self):
        previous = compat.fcntl
        compat.fcntl = None
        try:
            with tempfile.TemporaryDirectory() as td:
                store = SimpleNamespace(path=Path(td) / "state.db")
                with compat._reservation_lock(store):
                    self.assertTrue(True)
        finally:
            compat.fcntl = previous


if __name__ == "__main__":
    unittest.main()
