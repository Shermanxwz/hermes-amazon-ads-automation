from __future__ import annotations

import unittest

from helpers import Environment, READ_CAMPAIGN, WRITE_CAMPAIGN, one_target_snapshot

PRODUCT = "SPONSORED_PRODUCTS"


class BudgetExposureV3Tests(unittest.TestCase):
    def setUp(self):
        self.env = Environment(strict_writes=True)
        self.env.sync_basic_catalog()
        self.env.store.update_settings({
            "mode": "autopilot",
            "execution_enabled": True,
            "max_profile_daily_budget_increase_pct": 10,
            # This test isolates the older cumulative +10% guard. Give the new
            # account hard cap enough headroom, but still prove it has a fresh
            # full-account Campaign budget observation before either write.
            "max_daily_ad_spend": 1000,
        })

    def tearDown(self):
        self.env.close()

    def test_two_individually_safe_increases_cannot_exceed_profile_cap(self):
        snapshot = one_target_snapshot(waste=False)
        snapshot["targets"] = []
        snapshot["campaigns"] = [
            {"campaign_id": "c1", "ad_product": PRODUCT, "state": "ENABLED", "budget": 100, "clicks": 20, "spend": 10, "sales": 100, "orders": 5},
            {"campaign_id": "c2", "ad_product": PRODUCT, "state": "ENABLED", "budget": 100, "clicks": 20, "spend": 10, "sales": 100, "orders": 5},
        ]
        snapshot["budget_usage"] = [
            {"campaign_id": "c1", "budget_usage_percent": 95},
            {"campaign_id": "c2", "budget_usage_percent": 95},
        ]
        cycle = self.env.plan(snapshot)
        task = self.env.service.create_task({"cycle_id": cycle["id"]}, "main")
        decisions = {item["entity_id"]: item for item in self.env.store.list_decisions(task_id=task["id"])}
        self.assertEqual(set(decisions), {"c1", "c2"})

        self.env.store.record_action(
            task_id=None, session_id="main-budget-read", actor_role="main", phase="after",
            tool_name=READ_CAMPAIGN["registered_name"], operation="read", allowed=True,
            args={"body": {"accessRequestedAccount": {"profileId": "p1"}}},
            success=True, outcome_status="COMPLETED", structured_result=True,
            reason="fresh full account campaign budget observation", result_summary="2 campaigns",
            result={"campaigns": [
                {"campaignId": "c1", "budget": 100, "state": "ENABLED"},
                {"campaignId": "c2", "budget": 100, "state": "ENABLED"},
            ]}, duration_ms=1,
        )

        self.env.service.bind_worker({"task_id": task["id"], "worker_session_id": "exec", "role": "executor", "goal": "executor"})

        for index, campaign_id in enumerate(("c1", "c2"), start=1):
            call_id = f"read-{index}"
            allowed = self.env.service.authorize_tool({
                "tool_name": READ_CAMPAIGN["registered_name"], "args": {"campaignId": campaign_id},
                "session_id": "exec", "tool_call_id": call_id,
            })
            self.assertTrue(allowed["allowed"])
            read = self.env.service.finish_tool({
                "tool_name": READ_CAMPAIGN["registered_name"], "args": {"campaignId": campaign_id},
                "result": {"campaignId": campaign_id, "budget": 100},
                "session_id": "exec", "task_id": task["id"], "tool_call_id": call_id,
            })
            self.env.service.prepare_write({
                "decision_id": decisions[campaign_id]["id"], "evidence_action_id": read["action_id"], "session_id": "exec",
            })
            result = self.env.service.authorize_tool({
                "tool_name": WRITE_CAMPAIGN["registered_name"],
                "args": {"campaignId": campaign_id, "budget": 115},
                "session_id": "exec", "tool_call_id": f"write-{index}",
            })
            if campaign_id == "c1":
                self.assertTrue(result["allowed"])
            else:
                self.assertFalse(result["allowed"])
                self.assertIn("cumulative budget increase", result["reason"])


if __name__ == "__main__":
    unittest.main()
