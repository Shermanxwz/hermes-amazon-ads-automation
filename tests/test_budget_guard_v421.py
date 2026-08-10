from __future__ import annotations

from datetime import datetime, timedelta, timezone
import threading
import unittest

from amazon_ads_control import budget_guard
from amazon_ads_control.budget_reservation import OVERDELIVERY_SETTING
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

    def live_read(self, *budgets: float, created_at: str | None = None, next_token: str | None = None) -> int:
        campaigns = []
        for index, amount in enumerate(budgets, 1):
            campaigns.append({
                "campaignId": f"c{index}",
                "state": "ENABLED",
                "budgets": [{"budgetValue": {"monetaryBudgetValue": {"monetaryBudget": {"value": amount}}}}],
            })
        result = {"campaigns": campaigns}
        if next_token:
            result["nextToken"] = next_token
        action_id = self.env.store.record_action(
            task_id=None, session_id="main-read", actor_role="main", phase="after",
            tool_name=READ_CAMPAIGN["registered_name"], operation="read", allowed=True,
            args={"body": {"accessRequestedAccount": {"profileId": "p1"}}},
            success=True, outcome_status="COMPLETED", structured_result=True,
            reason="fresh account budget observation", result_summary="campaign read",
            result=result, duration_ms=1,
        )
        if created_at:
            with self.env.store.connection() as conn:
                conn.execute("UPDATE actions SET created_at=? WHERE id=?", (created_at, action_id))
        return action_id

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

    def attach_task(self, decision: dict) -> tuple[dict, dict]:
        task = self.env.service.create_task({"cycle_id": decision["cycle_id"]}, "test-main")
        current = self.env.store.get_decision(decision["id"])
        assert current is not None
        return task, current

    def reserve_directly(self, decision_id: str) -> None:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self.env.store.connection() as conn:
            conn.execute(
                "UPDATE decisions SET status='reserved',reserved_at=?,reservation_token='test-token' WHERE id=?",
                (now, decision_id),
            )

    def test_fresh_campaign_read_calculates_worst_case_daily_spend_exposure(self):
        action_id = self.live_read(15, 10)
        state = budget_guard.budget_status(self.env.store, "p1", require_fresh=True)
        self.assertTrue(state["fresh"])
        self.assertEqual(state["observation"]["action_id"], action_id)
        self.assertEqual(state["amazon_overdelivery_multiplier"], 2.0)
        self.assertAlmostEqual(state["observed_campaign_budget_sum"], 25.0)
        self.assertAlmostEqual(state["observed_exposure"], 50.0)
        self.assertAlmostEqual(state["remaining"], 50.0)
        self.assertTrue(state["increase_allowed"])
        self.assertTrue(state["exploration_allowed"])

    def test_paginated_campaign_read_is_not_accepted_as_full_budget_observation(self):
        self.live_read(15, next_token="more")
        state = budget_guard.budget_status(self.env.store, "p1", require_fresh=True)
        self.assertFalse(state["fresh"])
        self.assertFalse(state["increase_allowed"])
        self.assertIn("complete unpaginated", state["reason"])

    def test_eighty_percent_stops_new_exploration(self):
        self.live_read(41)
        state = budget_guard.budget_status(self.env.store, "p1")
        self.assertEqual(state["projected_exposure"], 82)
        self.assertFalse(state["exploration_allowed"])
        self.assertTrue(state["increase_allowed"])
        self.assertIn("stopped new exploration", state["reason"])

    def test_ninety_percent_stops_positive_exposure_increases(self):
        self.live_read(46)
        state = budget_guard.budget_status(self.env.store, "p1")
        self.assertEqual(state["projected_exposure"], 92)
        self.assertFalse(state["exploration_allowed"])
        self.assertFalse(state["increase_allowed"])
        self.assertIn("conservative", state["reason"])

    def test_hard_cap_blocks_all_positive_exposure(self):
        self.live_read(30, 20)
        state = budget_guard.budget_status(self.env.store, "p1")
        self.assertEqual(state["projected_exposure"], 100)
        self.assertEqual(state["remaining"], 0)
        self.assertFalse(state["increase_allowed"])
        self.assertIn("hard cap", state["reason"])

    def test_missing_fresh_read_is_fail_closed_for_execution(self):
        state = budget_guard.budget_status(self.env.store, "p1", require_fresh=True)
        self.assertFalse(state["fresh"])
        self.assertFalse(state["increase_allowed"])
        self.assertIn("complete unpaginated", state["reason"])

    def test_reserved_campaign_reserves_budget_and_exploration_pool(self):
        self.live_read(20)
        decision = self.add_campaign_decision(5, exploration=True)
        self.reserve_directly(decision["id"])
        state = budget_guard.budget_status(self.env.store, "p1")
        self.assertEqual(state["committed_campaign_budget_delta_today"], 5)
        self.assertEqual(state["committed_positive_delta_today"], 10)
        self.assertEqual(state["projected_exposure"], 50)
        self.assertEqual(state["exploration_committed_today"], 10)
        self.assertEqual(state["exploration_remaining"], 10)

    def test_newer_live_read_absorbs_executed_budget_delta_without_double_count(self):
        base_time = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=3)
        self.live_read(20, created_at=base_time.isoformat())
        decision = self.add_campaign_decision(5)
        executed = base_time + timedelta(seconds=1)
        with self.env.store.connection() as conn:
            conn.execute(
                "UPDATE decisions SET status='verified',executed_at=?,verified_at=? WHERE id=?",
                (executed.isoformat(), executed.isoformat(), decision["id"]),
            )
        before = budget_guard.budget_status(self.env.store, "p1")
        self.assertEqual(before["committed_campaign_budget_delta_today"], 5)
        self.assertEqual(before["committed_positive_delta_today"], 10)
        self.assertEqual(before["projected_exposure"], 50)
        observed = base_time + timedelta(seconds=2)
        self.live_read(20, 5, created_at=observed.isoformat())
        after = budget_guard.budget_status(self.env.store, "p1")
        self.assertEqual(after["observed_campaign_budget_sum"], 25)
        self.assertEqual(after["committed_campaign_budget_delta_today"], 0)
        self.assertEqual(after["projected_exposure"], 50)

    def test_concurrent_reservations_cannot_oversubscribe_hard_cap(self):
        self.env.store.update_settings({
            "budget_guard_conservative_pct": 100.0,
            "max_daily_ad_spend": 200.0,
        })
        self.live_read(30)  # worst-case spend exposure = 60
        task1, first = self.attach_task(self.add_campaign_decision(35))
        task2, second = self.attach_task(self.add_campaign_decision(36))
        barrier = threading.Barrier(3)
        outcomes: list[tuple[str, str]] = []
        lock = threading.Lock()

        def reserve(task: dict, decision: dict, session: str) -> None:
            barrier.wait()
            try:
                self.env.store.reserve_decision(
                    decision["id"], task["id"], session, 900,
                    cooldown_seconds=0, max_actions_per_task=50,
                    max_actions_per_day=250, max_campaign_creates_per_day=2,
                )
                outcome = ("ok", decision["id"])
            except ValueError as exc:
                outcome = ("blocked", str(exc))
            with lock:
                outcomes.append(outcome)

        threads = [
            threading.Thread(target=reserve, args=(task1, first, "exec-1")),
            threading.Thread(target=reserve, args=(task2, second, "exec-2")),
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()

        # 60 observed + 70/72 for the first reservation is safe; adding the
        # second would produce 202+ worst-case daily spend exposure.
        self.assertEqual(sum(kind == "ok" for kind, _ in outcomes), 1, outcomes)
        blocked = [detail for kind, detail in outcomes if kind == "blocked"]
        self.assertEqual(len(blocked), 1, outcomes)
        self.assertIn("hard cap", blocked[0])
        statuses = [
            self.env.store.get_decision(first["id"])["status"],
            self.env.store.get_decision(second["id"])["status"],
        ]
        self.assertEqual(statuses.count("reserved"), 1, statuses)
        self.assertEqual(statuses.count("planned"), 1, statuses)

    def test_atomic_reservation_rejects_paginated_observation(self):
        self.env.store.update_settings({"budget_guard_conservative_pct": 100.0})
        self.live_read(10, next_token="page-2")
        task, decision = self.attach_task(self.add_campaign_decision(5))
        with self.assertRaisesRegex(ValueError, "complete unpaginated"):
            self.env.store.reserve_decision(
                decision["id"], task["id"], "exec", 900,
                cooldown_seconds=0, max_actions_per_task=50,
                max_actions_per_day=250, max_campaign_creates_per_day=2,
            )

    def test_daily_budget_hard_cap_and_overdelivery_multiplier_are_locked(self):
        with self.assertRaisesRegex(ValueError, "locked safety invariant"):
            self.env.store.update_settings({"daily_budget_hard_cap_enabled": False})
        with self.assertRaisesRegex(ValueError, "locked safety invariant"):
            self.env.store.update_settings({OVERDELIVERY_SETTING: 1.0})

    def test_dashboard_exposes_budget_state_without_profile_identifier(self):
        self.live_read(12.5)
        dashboard = self.env.store.dashboard()
        state = dashboard["budget_guard"]
        self.assertEqual(state["hard_cap"], 100)
        self.assertEqual(state["projected_exposure"], 25)
        self.assertEqual(state["amazon_overdelivery_multiplier"], 2.0)
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
