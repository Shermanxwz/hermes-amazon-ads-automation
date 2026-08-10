from __future__ import annotations

from datetime import datetime, timezone
import unittest

from amazon_ads_control import budget_guard
from amazon_ads_control.catalog import descriptor_from_payload
from helpers import Environment, READ_CAMPAIGN

UTC = timezone.utc


class BudgetGuardV421Tests(unittest.TestCase):
    def setUp(self):
        self.env = Environment()
        self.env.store.upsert_profile({
            "profile_id": "p1", "name": "US", "marketplace": "US",
            "country_code": "US", "currency": "USD", "enabled": True,
        })
        self.env.store.sync_catalog([descriptor_from_payload(READ_CAMPAIGN)])
        self.env.store.update_settings({
            "max_daily_ad_spend": 100.0,
            "exploration_budget_pct": 20.0,
            "budget_guard_exploration_stop_pct": 80.0,
            "budget_guard_conservative_pct": 90.0,
        })

    def tearDown(self):
        self.env.close()

    def live_read(self, *budgets: float) -> int:
        campaigns = []
        for index, amount in enumerate(budgets, 1):
            campaigns.append({
                "campaignId": f"c{index}",
                "state": "ENABLED",
                "budgets": [{"budgetValue": {"monetaryBudgetValue": {"monetaryBudget": {"value": amount}}}}],
            })
        return self.env.store.record_action(
            task_id=None, session_id="main-read", actor_role="main", phase="after",
            tool_name=READ_CAMPAIGN["registered_name"], operation="read", allowed=True,
            args={"body": {"accessRequestedAccount": {"profileId": "p1"}}},
            success=True, outcome_status="COMPLETED", structured_result=True,
            reason="fresh account budget observation", result_summary="campaign read",
            result={"campaigns": campaigns}, duration_ms=1,
        )

    def add_campaign_decision(self, budget: float, *, exploration: bool = False):
        raw = {
            "entity_type": "campaign", "entity_id": "planned:test", "action_type": "create_campaign",
            "priority": 50, "rule_id": "budget-test", "reason": "bounded experiment",
            "evidence": {}, "expected_family": "campaign", "risk": "medium", "plan_key": f"create-{budget}-{exploration}",
            "payload": {
                "approved_args": {"campaigns": [{
                    "name": "HERMES-SP-EXP-test" if exploration else "HERMES-SP-test",
                    "budget": budget, "state": "PAUSED", "adProduct": "SPONSORED_PRODUCTS",
                }]},
                "exploration": exploration,
            },
        }
        cycle = self.env.store.create_cycle(
            profile={"profile_id": "p1", "marketplace": "US", "country_code": "US", "currency": "USD"},
            source="budget-test", window={"start": "2026-08-01", "end": "2026-08-01", "grain": "daily"},
            data_quality={}, kpis={}, snapshot={}, decisions=[raw], created_by="test",
        )
        return self.env.store.list_decisions(cycle_id=cycle["id"])[0]

    def test_fresh_campaign_read_calculates_account_exposure(self):
        action_id = self.live_read(30, 25.5)
        state = budget_guard.budget_status(self.env.store, "p1", require_fresh=True)
        self.assertTrue(state["fresh"])
        self.assertEqual(state["observation"]["action_id"], action_id)
        self.assertAlmostEqual(state["observed_exposure"], 55.5)
        self.assertAlmostEqual(state["remaining"], 44.5)
        self.assertTrue(state["increase_allowed"])
        self.assertTrue(state["exploration_allowed"])

    def test_eighty_percent_stops_new_exploration(self):
        self.live_read(81)
        state = budget_guard.budget_status(self.env.store, "p1")
        self.assertFalse(state["exploration_allowed"])
        self.assertTrue(state["increase_allowed"])
        self.assertIn("stopped new exploration", state["reason"])

    def test_ninety_percent_stops_positive_exposure_increases(self):
        self.live_read(91)
        state = budget_guard.budget_status(self.env.store, "p1")
        self.assertFalse(state["exploration_allowed"])
        self.assertFalse(state["increase_allowed"])
        self.assertIn("conservative", state["reason"])

    def test_hard_cap_blocks_all_positive_exposure(self):
        self.live_read(60, 40)
        state = budget_guard.budget_status(self.env.store, "p1")
        self.assertEqual(state["remaining"], 0)
        self.assertFalse(state["increase_allowed"])
        self.assertIn("hard cap", state["reason"])

    def test_missing_fresh_read_is_fail_closed_for_execution(self):
        state = budget_guard.budget_status(self.env.store, "p1", require_fresh=True)
        self.assertFalse(state["fresh"])
        self.assertFalse(state["increase_allowed"])
        self.assertIn("fresh structured Amazon Campaign read", state["reason"])

    def test_pending_campaign_reserves_budget_and_exploration_pool(self):
        self.live_read(50)
        self.add_campaign_decision(10, exploration=True)
        state = budget_guard.budget_status(self.env.store, "p1")
        self.assertEqual(state["committed_positive_delta_today"], 10)
        self.assertEqual(state["projected_exposure"], 60)
        self.assertEqual(state["exploration_committed_today"], 10)
        self.assertEqual(state["exploration_remaining"], 10)

    def test_newer_live_read_absorbs_executed_budget_delta_without_double_count(self):
        self.live_read(40)
        decision = self.add_campaign_decision(10)
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self.env.store.connection() as conn:
            conn.execute(
                "UPDATE decisions SET status='verified',executed_at=?,verified_at=? WHERE id=?",
                (now, now, decision["id"]),
            )
        before = budget_guard.budget_status(self.env.store, "p1")
        self.assertEqual(before["committed_positive_delta_today"], 10)
        self.live_read(40, 10)
        after = budget_guard.budget_status(self.env.store, "p1")
        self.assertEqual(after["observed_exposure"], 50)
        self.assertEqual(after["committed_positive_delta_today"], 0)
        self.assertEqual(after["projected_exposure"], 50)

    def test_daily_budget_hard_cap_setting_cannot_be_disabled(self):
        with self.assertRaisesRegex(ValueError, "locked safety invariant"):
            self.env.store.update_settings({"daily_budget_hard_cap_enabled": False})

    def test_dashboard_exposes_budget_state_without_profile_identifier(self):
        self.live_read(25)
        dashboard = self.env.store.dashboard()
        state = dashboard["budget_guard"]
        self.assertEqual(state["hard_cap"], 100)
        self.assertNotIn("profile_id", state)

    def test_helper_classifies_only_positive_budget_delta(self):
        create = {
            "action_type": "create_campaign",
            "payload": {"approved_args": {"campaigns": [{"budget": 7}]}, "exploration": True},
        }
        increase = {"action_type": "update_budget", "payload": {"field": "budget", "before": 10, "after": 13}}
        decrease = {"action_type": "update_budget", "payload": {"field": "budget", "before": 13, "after": 8}}
        self.assertEqual(budget_guard._positive_budget_delta(create), 7)
        self.assertTrue(budget_guard._exploration(create))
        self.assertEqual(budget_guard._positive_budget_delta(increase), 3)
        self.assertEqual(budget_guard._positive_budget_delta(decrease), 0)


if __name__ == "__main__":
    unittest.main()
