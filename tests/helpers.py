from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile

from amazon_ads_control.catalog import descriptor_from_payload
from amazon_ads_control.db import Store
from amazon_ads_control.service import ControlService

UTC = timezone.utc

READ_CAMPAIGN = {
    "registered_name": "mcp_amazon_ads_campaign_management_query_campaign",
    "native_name": "campaign_management-query_campaign",
    "schema": {"name": "query campaign", "description": "Query campaigns", "parameters": {"type": "object"}},
}
READ_TARGET = {
    "registered_name": "mcp_amazon_ads_campaign_management_query_target",
    "native_name": "campaign_management-query_target",
    "schema": {"name": "query target", "description": "Query target", "parameters": {"type": "object", "properties": {"targetId": {"type": "string"}}}},
}
WRITE_TARGET = {
    "registered_name": "mcp_amazon_ads_campaign_management_update_target",
    "native_name": "campaign_management-update_target",
    "schema": {"name": "update target", "description": "Update target bid", "parameters": {"type": "object", "properties": {"targetId": {"type": "string"}, "bid": {"type": "number"}}}},
}
WRITE_CAMPAIGN = {
    "registered_name": "mcp_amazon_ads_campaign_management_update_campaign",
    "native_name": "campaign_management-update_campaign",
    "schema": {"name": "update campaign", "description": "Update campaign budget", "parameters": {"type": "object", "properties": {"campaignId": {"type": "string"}, "budget": {"type": "number"}}}},
}
REPORT_CREATE = {
    "registered_name": "mcp_amazon_ads_reporting_create_report",
    "native_name": "reporting-create_report",
    "schema": {
        "name": "create report", "description": "Create an asynchronous reporting job",
        "parameters": {
            "type": "object", "required": ["reportTypeId"],
            "properties": {"reportTypeId": {"type": "string"}},
            "additionalProperties": False,
        },
    },
}

CRITICAL_ACCOUNT = {
    "registered_name": "mcp_amazon_ads_account_management_update_advertiser_account",
    "native_name": "account_management-update_advertiser_account",
    "schema": {"description": "Update advertiser account settings"},
}


def dates(days=14, lag=2):
    end = datetime.now(UTC).date() - timedelta(days=lag)
    start = end - timedelta(days=days - 1)
    return {"start": start.isoformat(), "end": end.isoformat(), "days": days, "grain": "daily"}


def one_target_snapshot(*, waste=True, profile_id="p1"):
    row = {
        "target_id": "t1", "campaign_id": "c1", "ad_group_id": "g1", "state": "ENABLED",
        "bid": 1.0, "impressions": 1000, "clicks": 15 if waste else 20,
        "spend": 25 if waste else 10, "sales": 0 if waste else 100, "orders": 0 if waste else 5,
        "ad_product": "SPONSORED_PRODUCTS",
    }
    return {
        "source": "amazon-ads-mcp",
        "profile": {"profile_id": profile_id, "name": "US", "marketplace": "US", "currency": "USD"},
        "window": dates(),
        "account": {"impressions": 1000, "clicks": row["clicks"], "spend": row["spend"], "sales": row["sales"], "orders": row["orders"]},
        "targets": [row], "campaigns": [], "search_terms": [], "placements": [], "budget_usage": [], "recommendations": [],
    }


class Environment:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "state.db")
        self.service = ControlService(self.store)

    def close(self):
        self.temp.cleanup()

    def sync_basic_catalog(self):
        return self.store.sync_catalog([descriptor_from_payload(READ_CAMPAIGN), descriptor_from_payload(READ_TARGET), descriptor_from_payload(WRITE_TARGET), descriptor_from_payload(WRITE_CAMPAIGN), descriptor_from_payload(REPORT_CREATE), descriptor_from_payload(CRITICAL_ACCOUNT)])

    def one_decision_task(self, *, autopilot=True):
        self.sync_basic_catalog()
        if autopilot:
            self.store.update_settings({"mode": "autopilot", "execution_enabled": True})
        cycle = self.service.plan_cycle({"snapshot": one_target_snapshot()}, "main")
        task = self.service.create_task({"cycle_id": cycle["id"]}, "main")
        decision = self.store.list_decisions(task_id=task["id"])[0]
        return cycle, task, decision
