from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import threading
import unittest
from zoneinfo import ZoneInfo

from amazon_ads_control import budget_guard, budget_reservation
from amazon_ads_control.catalog import descriptor_from_payload
from helpers import Environment, READ_CAMPAIGN

UTC = timezone.utc
LA = ZoneInfo("America/Los_Angeles")


class DailySpendCeilingV423Tests(unittest.TestCase):
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
        self._spend_counter = 0

    def tearDown(self):
        self.env.close()

    def record_spend(self, amount: float, *, campaign_id: str = "c-spend", received_at: datetime | None = None, dataset_id: str = "sp-traffic") -> None:
        self._spend_counter += 1
        now = (received_at or datetime.now(UTC)).replace(microsecond=0)
        date = now.astimezone(LA).date().isoformat()
        self.env.store.ingest_stream_events([{
            "profile_id": "p1",
            "dataset_id": dataset_id,
            "event_time": now.isoformat(),
            "dedupe_key": f"spend:{self._spend_counter}",
            "payload": {"date": date, "adProduct": "SPONSORED_PRODUCTS", "campaignId": campaign_id, "cost": amount},
        }], "budget-test")
        if received_at is not None:
            with self.env.store.connection() as conn:
                conn.execute("UPDATE stream_events SET received_at=? WHERE dedupe_key=?", (received_at.isoformat(), f"spend:{self._spend_counter}"))

    def clear_spend(self) -> None:
        with self.env.store.connection() as conn:
            conn.execute("DELETE FROM stream_events")

    def record_campaigns(self, campaigns: list[dict], *, created_at: str | None = None, next_token: str | None = None) -> int:
        result = {"campaigns": campaigns}
        if next_token:
            result["nextToken"] = next_token
        action_id = self.env.store.record_action(
            task_id=None, session_id="main-read", actor_role="main", phase="after",
            tool_name=READ_CAMPAIGN["registered_name"], operation="read", allowed=True,
            args={"body": {"accessRequestedAccount": {"profileId": "p1"}}},
            success=True, outcome_status="COMPLETED", structured_result=True,
            reason="fresh account Campaign observation", result_summary="campaign read",
            result=result, duration_ms=1,
        )
        if created_at:
            with self.env.store.connection() as conn:
                conn.execute("UPDATE actions SET created_at=? WHERE id=?", (created_at, action_id))
        return action_id

    def live_read(self, *budgets: float, states: tuple[str, ...] | None = None, names: tuple[str, ...] | None = None, next_token: str | None = None) -> int:
        campaigns = []
        for index, amount in enumerate(budgets, 1):
            campaigns.append({
                "campaignId": f"c{index}",
                "name": names[index - 1] if names and index <= len(names) else f"HERMES-SP-c{index}",
                "state": states[index - 1] if states and index <= len(states) else "ENABLED",
                "budgets": [{"budgetValue": {"monetaryBudgetValue": {"monetaryBudget": {"value": amount}}}}],
            })
        return self.record_campaigns(campaigns, next_token=next_token)

    def add_campaign_decision(self, budget: float, *, exploration: bool = False, plan_key: str | None = None):
        key = plan_key or f"create-{budget}-{exploration}"
        raw = {
            "entity_type": "campaign", "entity_id": f"planned:{key}", "action_type": "create_campaign",
            "priority": 50, "rule_id": "budget-test", "reason": "bounded experiment",
            "evidence": {}, "expected_family": "campaign", "risk": "medium", "plan_key": key,
            "payload": {
                "approved_args": {"campaigns": [{"name": "HERMES-SP-EXP-test" if exploration else "HERMES-SP-test", "budget": budget, "state": "PAUSED", "adProduct": "SPONSORED_PRODUCTS"}]},
                "exploration": exploration,
            },
        }
        cycle = self.env.store.create_cycle(
            profile={"profile_id": "p1", "marketplace": "US", "country_code": "US", "currency": "USD"},
            source="budget-test", window={"start": "2026-08-01", "end": "2026-08-01", "grain": "daily"},
            data_quality={}, kpis={}, snapshot={}, decisions=[raw], created_by="test",
        )
        return self.env.store.list_decisions(cycle_id=cycle["id"])[0]

    def add_state_decision(self, campaign_id: str, state: str, *, action_type: str):
        raw = {
            "entity_type": "campaign", "entity_id": campaign_id, "action_type": action_type,
            "priority": 40, "rule_id": "state-test", "reason": "state transition",
            "evidence": {}, "expected_family": "campaign", "risk": "medium",
            "plan_key": f"state-{campaign_id}-{state.lower()}",
            "payload": {"approved_args": {"campaignId": campaign_id, "state": state}, "field": "state", "before": "PAUSED" if state == "ENABLED" else "ENABLED", "after": state},
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

    def approve_task(self, task: dict) -> None:
        approval = self.env.store.create_approval_request(task["id"], "budget-test", "Approve exact budget-test reservation fixture")
        self.env.store.approve_approval(approval["id"], "operator", approval["payload_hash"], f"APPROVE {approval['id']} {approval['payload_hash'][:12]}")

    def reserve(self, task: dict, decision: dict, session: str = "exec") -> dict:
        return self.env.store.reserve_decision(decision["id"], task["id"], session, 900, cooldown_seconds=0, max_actions_per_task=50, max_actions_per_day=250, max_campaign_creates_per_day=10)

    def test_owner_cap_is_based_on_same_day_spend_not_all_campaign_budgets(self):
        self.clear_spend(); self.record_spend(12.5); action_id = self.live_read(250, 500)
        state = budget_guard.budget_status(self.env.store, "p1", require_fresh=True)
        self.assertTrue(state["fresh"]); self.assertEqual(state["spent_today"], 12.5); self.assertEqual(state["remaining"], 87.5); self.assertEqual(state["protected_spend"], 12.5)
        self.assertEqual(state["spend_source"], "marketing_stream_hourly_sp_traffic"); self.assertTrue(state["increase_allowed"]); self.assertNotIn("amazon_overdelivery_multiplier", state)
        with self.env.store.connection() as conn:
            observation = budget_reservation._fresh_complete_live_exposure(conn, "p1", 900)
        self.assertEqual(observation["action_id"], action_id); self.assertEqual(observation["campaign_budget_sum"], 750)

    def test_eighty_ninety_and_hundred_percent_use_actual_daily_spend(self):
        for amount, exploration, increase, reason in ((80, False, True, "stopped new exploration"), (90, False, False, "conservative"), (100, False, False, "ceiling reached")):
            self.clear_spend(); self.record_spend(amount); state = budget_guard.budget_status(self.env.store, "p1")
            self.assertEqual(state["spent_today"], amount); self.assertEqual(state["exploration_allowed"], exploration); self.assertEqual(state["increase_allowed"], increase); self.assertIn(reason, state["reason"])

    def test_stale_or_missing_same_day_spend_is_fail_closed(self):
        self.clear_spend(); missing = budget_guard.budget_status(self.env.store, "p1", require_fresh=True)
        self.assertFalse(missing["fresh"]); self.assertFalse(missing["increase_allowed"])
        self.record_spend(1); old = datetime.now(UTC) - timedelta(hours=3)
        with self.env.store.connection() as conn:
            conn.execute("UPDATE stream_events SET received_at=?", (old.isoformat(),))
        stale = budget_guard.budget_status(self.env.store, "p1", require_fresh=True)
        self.assertFalse(stale["fresh"]); self.assertIn("stale", stale["reason"])

    def test_non_sp_or_non_traffic_stream_does_not_count_as_spend_evidence(self):
        self.clear_spend(); self.record_spend(99, dataset_id="budget-usage")
        state = budget_guard.budget_status(self.env.store, "p1", require_fresh=True)
        self.assertFalse(state["fresh"]); self.assertIsNone(state["spent_today"])

    def test_same_day_lineaged_report_is_safe_fallback(self):
        self.clear_spend(); today = datetime.now(UTC).astimezone(LA).date().isoformat()
        cycle = self.env.store.create_cycle(profile={"profile_id": "p1", "marketplace": "US", "country_code": "US", "currency": "USD"}, source="report-fallback", window={"start": today, "end": today, "grain": "daily"}, data_quality={}, kpis={"spend": 17.25}, snapshot={}, decisions=[], created_by="test")
        with self.env.store.connection() as conn:
            conn.execute("INSERT OR REPLACE INTO snapshot_lineage(cycle_id,normalized_hash,report_job_ids_json,action_ids_json,source,created_at) VALUES(?,?,?,?,?,?)", (cycle["id"], "0" * 64, "[]", "[]", "test", datetime.now(UTC).isoformat()))
        state = budget_guard.budget_status(self.env.store, "p1", require_fresh=True)
        self.assertTrue(state["fresh"]); self.assertEqual(state["spent_today"], 17.25); self.assertEqual(state["spend_source"], "same_day_lineaged_report")

    def test_pause_is_always_financially_risk_reducing(self):
        self.clear_spend(); task, decision = self.attach_task(self.add_state_decision("c-risk", "PAUSED", action_type="update_state"))
        reserved = self.reserve(task, decision); self.assertEqual(reserved["status"], "reserved")

    def test_monetary_create_requires_fresh_campaign_envelope(self):
        task, decision = self.attach_task(self.add_campaign_decision(5)); self.approve_task(task)
        with self.assertRaisesRegex(ValueError, "fresh complete unpaginated"):
            self.reserve(task, decision)

    def test_owner_cap_allows_nominal_budget_up_to_cap_without_blanket_two_x(self):
        self.clear_spend(); self.record_spend(0); self.live_read(10)
        self.env.store.update_settings({"max_daily_ad_spend": 30, "budget_guard_conservative_pct": 100.0})
        task, decision = self.attach_task(self.add_campaign_decision(15)); self.approve_task(task)
        reserved = self.reserve(task, decision); self.assertEqual(reserved["status"], "reserved")
        state = budget_guard.budget_status(self.env.store, "p1"); self.assertLess(state["pending_reserve"], 20); self.assertEqual(state["hard_cap"], 30); self.assertGreater(state["remaining"], 10)

    def test_nominal_campaign_envelope_cannot_exceed_owner_cap(self):
        self.clear_spend(); self.record_spend(0); self.live_read(25)
        self.env.store.update_settings({"max_daily_ad_spend": 30, "budget_guard_conservative_pct": 100.0})
        task, decision = self.attach_task(self.add_campaign_decision(10)); self.approve_task(task)
        with self.assertRaisesRegex(ValueError, "active/future Campaign daily budgets"):
            self.reserve(task, decision)

    def test_enable_requires_campaign_observation_and_reserves_paused_budget(self):
        self.clear_spend(); self.record_spend(10); self.env.store.update_settings({"budget_guard_conservative_pct": 100.0}); self.live_read(20, states=("PAUSED",))
        task, decision = self.attach_task(self.add_state_decision("c1", "ENABLED", action_type="enable")); reserved = self.reserve(task, decision)
        self.assertEqual(reserved["status"], "reserved"); state = budget_guard.budget_status(self.env.store, "p1"); self.assertGreater(state["pending_reserve"], 20); self.assertEqual(state["spent_today"], 10)

    def test_enable_missing_campaign_is_fail_closed(self):
        self.live_read(10); task, decision = self.attach_task(self.add_state_decision("not-in-read", "ENABLED", action_type="enable"))
        with self.assertRaisesRegex(ValueError, "missing from the fresh complete budget observation"):
            self.reserve(task, decision)

    def test_exploration_spend_is_attributed_when_campaign_names_are_known(self):
        self.clear_spend(); self.record_spend(7, campaign_id="c1"); self.live_read(10, names=("HERMES-SP-EXP-one",))
        state = budget_guard.budget_status(self.env.store, "p1"); self.assertEqual(state["exploration_spend_today"], 7); self.assertEqual(state["exploration_remaining"], 13)

    def test_concurrent_reservations_cannot_oversubscribe_owner_cap(self):
        self.clear_spend(); self.record_spend(30); self.live_read(20); self.env.store.update_settings({"budget_guard_conservative_pct": 100.0, "max_daily_ad_spend": 100.0})
        task1, first = self.attach_task(self.add_campaign_decision(35, plan_key="first")); task2, second = self.attach_task(self.add_campaign_decision(36, plan_key="second")); self.approve_task(task1); self.approve_task(task2)
        barrier = threading.Barrier(3); outcomes: list[tuple[str, str]] = []; lock = threading.Lock()
        def attempt(task: dict, decision: dict, session: str) -> None:
            barrier.wait()
            try:
                self.env.store.reserve_decision(decision["id"], task["id"], session, 900, cooldown_seconds=0, max_actions_per_task=50, max_actions_per_day=250, max_campaign_creates_per_day=2)
                outcome = ("ok", decision["id"])
            except ValueError as exc:
                outcome = ("blocked", str(exc))
            with lock: outcomes.append(outcome)
        threads = [threading.Thread(target=attempt, args=(task1, first, "exec-1")), threading.Thread(target=attempt, args=(task2, second, "exec-2"))]
        for thread in threads: thread.start()
        barrier.wait()
        for thread in threads: thread.join()
        self.assertEqual(sum(kind == "ok" for kind, _ in outcomes), 1, outcomes); self.assertEqual(sum(kind == "blocked" for kind, _ in outcomes), 1, outcomes)

    def test_safety_invariants_and_timezone_are_locked(self):
        with self.assertRaisesRegex(ValueError, "locked safety invariant"): self.env.store.update_settings({"daily_budget_hard_cap_enabled": False})
        with self.assertRaisesRegex(ValueError, "locked safety invariant"): self.env.store.update_settings({budget_reservation.OVERDELIVERY_SETTING: 1.0})
        with self.assertRaisesRegex(ValueError, "locked safety invariant"): self.env.store.update_settings({budget_reservation.SPEND_TIMEZONE_SETTING: "UTC"})

    def test_dashboard_exposes_owner_spend_state_without_profile_or_internal_multiplier(self):
        self.clear_spend(); self.record_spend(12.5); state = self.env.store.dashboard()["budget_guard"]
        self.assertEqual(state["hard_cap"], 100); self.assertEqual(state["spent_today"], 12.5); self.assertEqual(state["remaining"], 87.5); self.assertNotIn("profile_id", state); self.assertNotIn("amazon_overdelivery_multiplier", state)

    def test_spend_increasing_classifier_covers_bid_placement_and_creation(self):
        self.assertTrue(budget_reservation._is_spend_increasing({"action_type": "update_bid", "payload": {"field": "bid", "before": 1, "after": 1.2}}))
        self.assertFalse(budget_reservation._is_spend_increasing({"action_type": "update_bid", "payload": {"field": "bid", "before": 1.2, "after": 1}}))
        self.assertTrue(budget_reservation._is_spend_increasing({"action_type": "update_placement", "payload": {"field": "adjustment_percent", "before": 10, "after": 20}}))
        self.assertTrue(budget_reservation._is_spend_increasing({"action_type": "create_keyword", "payload": {}}))
        self.assertFalse(budget_reservation._is_spend_increasing({"action_type": "add_negative_exact", "payload": {}}))

    def test_stream_duplicate_keys_do_not_double_count_spend(self):
        self.clear_spend(); now = datetime.now(UTC).replace(microsecond=0); date = now.astimezone(LA).date().isoformat()
        events = [{"profile_id": "p1", "dataset_id": "sp-traffic", "event_time": now.isoformat(), "dedupe_key": "same", "payload": {"date": date, "adProduct": "SPONSORED_PRODUCTS", "campaignId": "c1", "cost": 9}}]
        first = self.env.store.ingest_stream_events(events, "test"); second = self.env.store.ingest_stream_events(events, "test")
        self.assertEqual(first["inserted"], 1); self.assertEqual(second["duplicates"], 1); self.assertEqual(budget_guard.budget_status(self.env.store, "p1")["spent_today"], 9)


if __name__ == "__main__":
    unittest.main()
