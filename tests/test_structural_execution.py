from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from amazon_ads_control.approval_gate import _approval_plan, _digest
from amazon_ads_control.catalog import descriptor_from_payload
from amazon_ads_control.db import Store
from amazon_ads_control.service import ControlService

UTC = timezone.utc
LA = ZoneInfo("America/Los_Angeles")
READ_CAMPAIGN = {"registered_name": "mcp_amazon_ads_campaign_management_query_campaign", "native_name": "campaign_management-query_campaign", "source": "hermes-registry:na", "schema": {"description": "Query campaigns", "parameters": {"type": "object", "properties": {}}}}
CREATE_CAMPAIGN = {"registered_name": "mcp_amazon_ads_campaign_management_create_campaign", "native_name": "campaign_management-create_campaign", "source": "hermes-registry:na", "schema": {"description": "Create one campaign", "parameters": {"type": "object", "required": ["campaigns"], "properties": {"campaigns": {"type": "array", "minItems": 1, "maxItems": 1, "items": {"type": "object", "required": ["name", "budget"], "properties": {"name": {"type": "string"}, "budget": {"type": "number", "minimum": 1}}}}}}}}
CREATE_AD_GROUP = {"registered_name": "mcp_amazon_ads_ad_group_management_create_ad_group", "native_name": "ad_group_management-create_ad_group", "source": "hermes-registry:na", "schema": {"description": "Create one ad group", "parameters": {"type": "object", "required": ["adGroups"], "properties": {"adGroups": {"type": "array", "minItems": 1, "maxItems": 1, "items": {"type": "object", "required": ["campaignId", "name", "defaultBid"], "properties": {"campaignId": {"type": "string"}, "name": {"type": "string"}, "defaultBid": {"type": "number", "minimum": 0.02}}}}}}}}


class StructuralExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.store = Store(Path(self.temp.name) / "state.db"); self.service = ControlService(self.store)
        self.store.sync_catalog([descriptor_from_payload(READ_CAMPAIGN), descriptor_from_payload(CREATE_CAMPAIGN), descriptor_from_payload(CREATE_AD_GROUP)])
        self.store.update_settings({"mode": "autopilot", "execution_enabled": True})

    def tearDown(self): self.temp.cleanup()

    def fresh_budget_read(self) -> None:
        self.store.record_action(task_id=None, session_id="main-budget-read", actor_role="main", phase="after", tool_name=READ_CAMPAIGN["registered_name"], operation="read", allowed=True, args={"body": {"accessRequestedAccount": {"profileId": "p1"}}}, success=True, outcome_status="COMPLETED", structured_result=True, reason="fresh full account campaign budget observation", result_summary="empty account", result={"campaigns": []}, duration_ms=1)
        now = datetime.now(UTC).replace(microsecond=0); date = now.astimezone(LA).date().isoformat()
        self.store.ingest_stream_events([{"profile_id": "p1", "dataset_id": "sp-traffic", "event_time": now.isoformat(), "dedupe_key": f"structural-zero:{date}", "payload": {"date": date, "adProduct": "SPONSORED_PRODUCTS", "campaignId": "fixture", "cost": 0}}])

    def plan(self):
        return self.service.create_managed_plan({"title": "Create exact campaign hierarchy", "profile": {"profile_id": "p1", "marketplace": "US", "country_code": "US", "currency": "USD"}, "actions": [{"plan_key": "campaign-step", "tool_name": CREATE_CAMPAIGN["registered_name"], "action_type": "create_campaign", "entity_type": "campaign", "entity_id": "planned:campaign-step", "arguments": {"campaigns": [{"name": "Exact Campaign", "budget": 25}]}, "expected_state": {"name": "Exact Campaign", "budget": 25}, "maximum_daily_budget": 25}, {"plan_key": "ad-group-step", "depends_on": ["campaign-step"], "tool_name": CREATE_AD_GROUP["registered_name"], "action_type": "create_ad_group", "entity_type": "ad_group", "entity_id": "planned:ad-group-step", "arguments": {"adGroups": [{"campaignId": "{{decision:campaign-step.entity_id}}", "name": "Exact Ad Group", "defaultBid": 0.75}]}, "expected_state": {"campaignId": "{{decision:campaign-step.entity_id}}", "name": "Exact Ad Group", "defaultBid": 0.75}}]}, "main")

    def approve_and_bind(self, result):
        approval = result["approval"]; phrase = f"APPROVE {approval['id']} {approval['payload_hash'][:12]}"; self.store.approve_approval(approval["id"], "operator", approval["payload_hash"], phrase); task_id = result["task"]["id"]
        self.service.bind_worker({"task_id": task_id, "worker_session_id": "executor", "worker_subagent_id": "executor-sub", "role": "executor", "goal": f"[ads-task:{task_id}] [ads-role:executor] exact structural plan", "model": "MiniMax-M3"}); return task_id, approval

    def test_created_id_renders_only_the_approved_dependent_payload(self):
        result = self.plan(); task_id, approval = self.approve_and_bind(result); decisions = {item["plan_key"]: item for item in self.store.list_decisions(task_id=task_id)}; campaign = decisions["campaign-step"]; ad_group = decisions["ad-group-step"]; campaign_args = {"campaigns": [{"name": "Exact Campaign", "budget": 25}]}
        self.fresh_budget_read(); authorized = self.service.authorize_tool({"tool_name": CREATE_CAMPAIGN["registered_name"], "args": campaign_args, "session_id": "executor", "tool_call_id": "campaign-call"}); self.assertTrue(authorized["allowed"], authorized)
        self.store.mark_execution(decision_id=campaign["id"], reservation_token=authorized["reservation_token"], tool_name=CREATE_CAMPAIGN["registered_name"], outcome="success", result={"campaigns": [{"campaignId": "AMZ-CAMPAIGN-123"}]})
        bound = self.store.get_decision(campaign["id"]); self.assertEqual(bound["entity_id"], "AMZ-CAMPAIGN-123"); self.assertEqual(bound["logical_entity_id"], "planned:campaign-step")
        current_hash = _digest(_approval_plan(self.store.get_task(task_id), self.store.list_decisions(task_id=task_id, limit=500))); self.assertEqual(current_hash, approval["payload_hash"])
        rendered = {"adGroups": [{"campaignId": "AMZ-CAMPAIGN-123", "name": "Exact Ad Group", "defaultBid": 0.75}]}; context = self.service.context("executor"); dependent = next(item for item in context["decisions"] if item["id"] == ad_group["id"])
        self.assertEqual(dependent["rendered_arguments"], rendered); self.assertEqual(dependent["payload"]["expected_state"], {"campaignId": "AMZ-CAMPAIGN-123", "name": "Exact Ad Group", "defaultBid": 0.75}); self.assertEqual(dependent["payload"]["approved_expected_state"]["campaignId"], "{{decision:campaign-step.entity_id}}")
        tampered = self.service.authorize_tool({"tool_name": CREATE_AD_GROUP["registered_name"], "args": {"adGroups": [{"campaignId": "OTHER-CAMPAIGN", "name": "Exact Ad Group", "defaultBid": 0.75}]}, "session_id": "executor", "tool_call_id": "tampered-call"}); self.assertFalse(tampered["allowed"])
        allowed = self.service.authorize_tool({"tool_name": CREATE_AD_GROUP["registered_name"], "args": rendered, "session_id": "executor", "tool_call_id": "ad-group-call"}); self.assertTrue(allowed["allowed"], allowed)

    def test_missing_created_id_quarantines_and_blocks_dependents(self):
        result = self.plan(); task_id, _approval = self.approve_and_bind(result); decisions = {item["plan_key"]: item for item in self.store.list_decisions(task_id=task_id)}; campaign = decisions["campaign-step"]; self.fresh_budget_read()
        authorized = self.service.authorize_tool({"tool_name": CREATE_CAMPAIGN["registered_name"], "args": {"campaigns": [{"name": "Exact Campaign", "budget": 25}]}, "session_id": "executor", "tool_call_id": "campaign-call"}); self.assertTrue(authorized["allowed"], authorized)
        execution = self.store.mark_execution(decision_id=campaign["id"], reservation_token=authorized["reservation_token"], tool_name=CREATE_CAMPAIGN["registered_name"], outcome="success", result={"status": "SUCCESS"}); self.assertEqual(execution["status"], "uncertain")
        context = self.service.context("executor"); dependent = next(item for item in context["decisions"] if item["plan_key"] == "ad-group-step"); self.assertIn("rendering_blocked", dependent)

    def test_forward_or_undeclared_dependencies_are_rejected(self):
        payload = {"title": "Invalid hierarchy", "profile": {"profile_id": "p1"}, "actions": [{"plan_key": "child", "depends_on": ["parent"], "tool_name": CREATE_AD_GROUP["registered_name"], "action_type": "create_ad_group", "entity_id": "planned:child", "arguments": {"adGroups": [{"campaignId": "{{decision:parent.entity_id}}", "name": "x", "defaultBid": 0.5}]}, "expected_state": {"name": "x"}}, {"plan_key": "parent", "tool_name": CREATE_CAMPAIGN["registered_name"], "action_type": "create_campaign", "entity_id": "planned:parent", "arguments": {"campaigns": [{"name": "x", "budget": 10}]}, "expected_state": {"name": "x"}}]}
        with self.assertRaisesRegex(ValueError, "must precede"): self.service.create_managed_plan(payload, "main")
        payload["actions"][0]["depends_on"] = []
        with self.assertRaisesRegex(ValueError, "must also appear"): self.service.create_managed_plan(payload, "main")


if __name__ == "__main__": unittest.main()
