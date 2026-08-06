#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import hashlib
import os
import tempfile
import threading

from playwright.sync_api import sync_playwright

from amazon_ads_control.api import build_server
from amazon_ads_control.catalog import descriptor_from_payload
from amazon_ads_control.config import Settings
from amazon_ads_control.db import Store
from amazon_ads_control.reporting import snapshot_hash
from amazon_ads_control.security import hash_password
from amazon_ads_control.service import ControlService
from helpers import one_target_snapshot

PASSWORD = "correct horse battery staple"
CREATE_CAMPAIGN = {
    "registered_name": "mcp_amazon_ads_campaign_management_create_campaign",
    "native_name": "campaign_management-create_campaign",
    "source": "hermes-registry:na",
    "schema": {
        "description": "Create one campaign",
        "parameters": {
            "type": "object",
            "required": ["campaigns"],
            "properties": {
                "campaigns": {
                    "type": "array", "minItems": 1, "maxItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["name", "budget", "state", "adProduct"],
                        "properties": {
                            "name": {"type": "string"},
                            "budget": {"type": "number"},
                            "state": {"type": "string"},
                            "adProduct": {"type": "string"},
                        },
                    },
                }
            },
        },
    },
}


def ingested_lineage(store: Store, snapshot: dict) -> dict:
    profile = snapshot["profile"]
    window = snapshot["window"]
    job = store.create_report_job({
        "profile_id": profile["profile_id"],
        "report_type": "browser-normalized-snapshot",
        "start_date": window["start"],
        "end_date": window["end"],
        "timezone": "UTC",
        "ad_product": "SPONSORED_PRODUCTS",
        "columns": ["impressions", "clicks", "spend", "sales", "orders"],
    }, "browser-e2e-seed")
    report_id = "report-" + job["id"]
    store.transition_report(job["id"], "SUBMITTED", {"report_id": report_id}, "browser-e2e-seed")
    store.transition_report(job["id"], "SUCCEEDED", {"report_id": report_id}, "browser-e2e-seed")
    content_hash = hashlib.sha256((report_id + "-content").encode()).hexdigest()
    normalized_hash = snapshot_hash(snapshot)
    store.transition_report(job["id"], "DOWNLOADED", {"content_hash": content_hash}, "browser-e2e-seed")
    store.transition_report(job["id"], "VALIDATED", {"snapshot": snapshot}, "browser-e2e-seed")
    validated = store.get_report_job(job["id"])
    store.transition_report(job["id"], "INGESTED", {
        "content_hash": content_hash,
        "normalized_hash": normalized_hash,
        "schema_hash": validated["schema_hash"],
        "row_count": validated["row_count"],
    }, "browser-e2e-seed")
    return {"report_job_ids": [job["id"]], "action_ids": [], "normalized_hash": normalized_hash}


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        settings = Settings(
            host="127.0.0.1", port=0, db_path=Path(td) / "state.db", public_origin="",
            control_password_hash=hash_password(PASSWORD), agent_token="a" * 48,
            session_ttl_seconds=3600, max_sessions=8, retention_days=30,
            allow_remote_bind=False,
        )
        store = Store(settings.db_path)
        service = ControlService(store)
        snapshot = one_target_snapshot(waste=False)
        service.plan_cycle({
            "snapshot": snapshot,
            "lineage": ingested_lineage(store, snapshot),
        }, "browser-e2e-seed")
        store.sync_catalog([descriptor_from_payload(CREATE_CAMPAIGN)])
        store.record_runtime_status(
            "hermes-plugin",
            {
                "readiness_protocol": 1,
                "resources": {"tier": "2c2g", "cpu_count": 2, "memory_total_mb": 2048},
                "result_outbox": {"pending": 0, "bytes": 0, "over_limit": False},
                "catalog_sync": {"ok": True, "tool_count": 1},
            },
        )
        managed = service.create_managed_plan({
            "title": "Browser full-managed Campaign",
            "profile": {"profile_id": "p1", "marketplace": "US", "country_code": "US", "currency": "USD"},
            "actions": [{
                "plan_key": "browser-campaign",
                "tool_name": CREATE_CAMPAIGN["registered_name"],
                "action_type": "create_campaign",
                "entity_type": "campaign",
                "entity_id": "planned:browser-campaign",
                "arguments": {"campaigns": [{"name": "HERMES-SP-BROWSER-001", "budget": 19, "state": "PAUSED", "adProduct": "SPONSORED_PRODUCTS"}]},
                "expected_state": {"name": "HERMES-SP-BROWSER-001", "budget": 19, "state": "PAUSED"},
                "maximum_daily_budget": 19,
            }],
        }, "browser-e2e-seed")
        assert managed["standing_authorization"]["applied"] is True
        assert managed["standing_authorization"]["automatic"] is True
        assert managed["task"]["status"] == "planned"
        assert managed["approval"]["status"] == "cancelled"

        server = build_server(settings, store=store)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        console_errors: list[str] = []
        page_errors: list[str] = []
        try:
            with sync_playwright() as playwright:
                launch = {"headless": True}
                if os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE"):
                    launch["executable_path"] = os.environ["PLAYWRIGHT_CHROMIUM_EXECUTABLE"]
                browser = playwright.chromium.launch(**launch)
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                page = context.new_page()

                def capture_console(message) -> None:
                    if message.type != "error":
                        return
                    # /health/ready intentionally returns 503 whenever the
                    # controller is fail-closed (observe, paused, or phase one
                    # of the two-step autopilot transition). UI state and the
                    # readiness payload are asserted separately below.
                    if "503 (Service Unavailable)" not in message.text:
                        console_errors.append(message.text)

                page.on("console", capture_console)
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                response = page.goto(base, wait_until="networkidle")
                assert response and response.ok
                assert response.headers.get("x-frame-options") == "DENY"
                assert "object-src 'none'" in response.headers.get("content-security-policy", "")
                page.get_by_placeholder("控制台密码").fill(PASSWORD)
                page.get_by_role("button", name="登录").click()
                page.wait_for_selector("#app:not([hidden])")
                assert page.get_by_role("heading", name="广告全托管").is_visible()
                page.locator("#mode-pill").filter(has_text="OBSERVE").wait_for()
                assert "不会修改任何广告" in page.locator("#mode-copy").inner_text()
                page.locator("#readiness-pill").filter(has_text="READY").wait_for()
                assert page.locator("#kpis .metric-card").count() == 4
                assert page.locator("#trend-chart svg").is_visible()
                assert page.locator("#activity-list").is_visible()
                assert page.get_by_text("审批", exact=True).count() == 0
                page.locator("#target-acos").fill("27")
                page.locator("#max-campaign-budget").fill("45")
                page.get_by_role("button", name="保存", exact=True).click()
                page.locator("#notice").filter(has_text="运营目标已更新").wait_for()
                current = store.get_settings()
                assert current["target_acos"] == 27
                assert current["sealed_sp_max_campaign_budget"] == 45
                page.get_by_role("button", name="全托管运行").click()
                page.locator("#mode-pill").filter(has_text="AUTOPILOT").wait_for()
                page.locator("#readiness-pill").filter(has_text="WRITABLE").wait_for()
                assert "自动分析、执行和独立回读" in page.locator("#mode-copy").inner_text()
                page.get_by_role("button", name="暂停", exact=True).click()
                page.locator("#mode-pill").filter(has_text="PAUSED").wait_for()
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.locator(".topbar").bounding_box()["width"] <= 390
                assert page.locator(".mode-buttons").is_visible()
                assert page.locator("#kpis").is_visible()
                page.reload(wait_until="networkidle")
                page.locator("#app").wait_for(state="visible")
                page.get_by_role("button", name="退出").click()
                page.wait_for_selector("#login:not([hidden])")
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        assert not console_errors, f"browser console errors: {console_errors}"
        assert not page_errors, f"browser page errors: {page_errors}"
    print("browser-e2e: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
