from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from amazon_ads_control import budget_guard
from amazon_ads_control.catalog import descriptor_from_payload
from helpers import Environment, READ_CAMPAIGN

UTC = timezone.utc


class BudgetActivationReservationV423Tests(unittest.TestCase):
    def setUp(self):
        self.env = Environment()
        self.env.store.upsert_profile({
            "profile_id": "p1", "name": "US", "marketplace": "US",
            "country_code": "US", "currency": "USD", "enabled": True,
        })
        self.env.store.sync_catalog([descriptor_from_payload(READ_CAMPAIGN)])
        self.env.store.update_settings({
            "max_daily_ad_spend": 70.0,
            "exploration_budget_pct": 20.0,
            "budget_guard_exploration_stop_pct": 80.0,
            "budget_guard_conservative_pct": 100.0,
        })

    def tearDown(self):
        self.env.close()

    def record_campaign(self, state: str, created_at: datetime) -> None:
        self.env.store.record_action(
            task_id=None, session_id="budget-read", actor_role="main", phase="after",
            tool_name=READ_CAMPAIGN["registered_name"], operation="read", allowed=True,
            args={"body": {"accessRequestedAccount": {"profileId": "p1"}}},
            success=True, outcome_status="COMPLETED", structured_result=True,
            reason="complete account campaign budget observation", result_summary="one campaign",
            result={"campaigns": [{
                "campaignId": "c-new", "name": "HERMES-SP-new", "state": state,
                "budgets": [{"budgetValue": {"monetaryBudgetValue": {"monetaryBudget": {"value": 30}}}}],
            }]}, duration_ms=1,
        )
        with self.env.store.connection() as conn:
            action_id = conn.execute("SELECT MAX(id) FROM actions").fetchone()[0]
            conn.execute("UPDATE actions SET created_at=? WHERE id=?", (created_at.isoformat(), action_id))

    def test_staged_enable_reuses_create_money_with_only_bounded_state_buffer(self):
        create = {
            "entity_type": "campaign", "entity_id": "planned:new", "action_type": "create_campaign",
            "priority": 50, "rule_id": "create", "reason": "create paused campaign",
            "evidence": {}, "expected_family": "campaign", "risk": "medium", "plan_key": "new",
            "payload": {"approved_args": {"campaigns": [{"name": "HERMES-SP-new", "budget": 30, "state": "PAUSED", "adProduct": "SPONSORED_PRODUCTS"}]}},
        }
        enable = {
            "entity_type": "campaign", "entity_id": "{{decision:new.entity_id}}", "action_type": "enable",
            "priority": 10, "rule_id": "activation", "reason": "verified staged activation",
            "evidence": {}, "expected_family": "campaign", "risk": "medium", "plan_key": "new:verified-enable",
            "payload": {"approved_args": {"campaignId": "{{decision:new.entity_id}}", "state": "ENABLED"}, "activation_phase": True, "activation_source_plan_key": "new", "activation_rank": 30},
        }
        cycle = self.env.store.create_cycle(
            profile={"profile_id": "p1", "marketplace": "US", "country_code": "US", "currency": "USD"},
            source="activation-reservation-test", window={"start": "2026-08-01", "end": "2026-08-01", "grain": "daily"},
            data_quality={}, kpis={}, snapshot={}, decisions=[create, enable], created_by="test",
        )
        task = self.env.service.create_task({"cycle_id": cycle["id"]}, "test-main")
        rows = {item["plan_key"]: item for item in self.env.store.list_decisions(task_id=task["id"])}
        now = datetime.now(UTC).replace(microsecond=0)
        with self.env.store.connection() as conn:
            conn.execute("UPDATE decisions SET status='verified',entity_id='c-new',executed_at=?,verified_at=? WHERE id=?", ((now - timedelta(seconds=2)).isoformat(), (now - timedelta(seconds=2)).isoformat(), rows["new"]["id"]))
            conn.execute("UPDATE decisions SET status='planned' WHERE id=?", (rows["new:verified-enable"]["id"],))
        self.record_campaign("PAUSED", now - timedelta(seconds=1))
        before = budget_guard.budget_status(self.env.store, "p1")
        self.assertEqual(before["spent_today"], 0)
        self.assertGreater(before["pending_reserve"], 30)
        reserve_before = before["pending_reserve"]

        reserved = self.env.store.reserve_decision(
            rows["new:verified-enable"]["id"], task["id"], "executor", 900,
            cooldown_seconds=0, max_actions_per_task=50, max_actions_per_day=250,
            max_campaign_creates_per_day=10,
        )
        self.assertEqual(reserved["status"], "reserved")
        during = budget_guard.budget_status(self.env.store, "p1")
        extra = round(during["pending_reserve"] - reserve_before, 2)
        self.assertGreaterEqual(extra, 0.0)
        self.assertLessEqual(extra, 0.7)  # 1% of the 70 Owner cap, never another 30 budget reserve.

        self.record_campaign("ENABLED", now + timedelta(seconds=1))
        after = budget_guard.budget_status(self.env.store, "p1")
        self.assertLessEqual(after["pending_reserve"], reserve_before + 0.7)
        self.assertLess(after["protected_spend"], 70)


if __name__ == "__main__":
    unittest.main()
