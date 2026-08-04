#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import os
import tempfile
import threading

from playwright.sync_api import sync_playwright

from amazon_ads_control.api import build_server
from amazon_ads_control.catalog import descriptor_from_payload
from amazon_ads_control.config import Settings
from amazon_ads_control.db import Store
from amazon_ads_control.security import hash_password
from amazon_ads_control.service import ControlService

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
                        "required": ["name", "budget", "state"],
                        "properties": {
                            "name": {"type": "string"},
                            "budget": {"type": "number"},
                            "state": {"type": "string"},
                        },
                    },
                }
            },
        },
    },
}


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        settings = Settings(
            host="127.0.0.1", port=0, db_path=Path(td)/"state.db", public_origin="",
            control_password_hash=hash_password(PASSWORD), agent_token="a"*48,
            session_ttl_seconds=3600, max_sessions=8, retention_days=30,
            allow_remote_bind=False,
        )
        store = Store(settings.db_path)
        service = ControlService(store)
        store.sync_catalog([descriptor_from_payload(CREATE_CAMPAIGN)])
        managed = service.create_managed_plan({
            "title": "Browser exact Campaign approval",
            "profile": {
                "profile_id": "profile-browser", "marketplace": "US",
                "country_code": "US", "currency": "USD",
            },
            "actions": [{
                "plan_key": "browser-campaign",
                "tool_name": CREATE_CAMPAIGN["registered_name"],
                "action_type": "create_campaign",
                "entity_type": "campaign",
                "entity_id": "planned:browser-campaign",
                "arguments": {"campaigns": [{
                    "name": "Browser Approved Campaign", "budget": 19, "state": "PAUSED",
                }]},
                "expected_state": {
                    "name": "Browser Approved Campaign", "budget": 19, "state": "PAUSED",
                },
                "maximum_daily_budget": 19,
            }],
        }, "browser-e2e-seed")
        approval_hash = managed["approval"]["payload_hash"]

        server = build_server(settings)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        console_errors: list[str] = []
        page_errors: list[str] = []
        try:
            with sync_playwright() as p:
                launch={"headless":True}
                if os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE"):
                    launch["executable_path"]=os.environ["PLAYWRIGHT_CHROMIUM_EXECUTABLE"]
                browser = p.chromium.launch(**launch)
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                page = context.new_page()
                page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                response = page.goto(base, wait_until="networkidle")
                assert response and response.ok, f"dashboard load failed: {response.status if response else 'none'}"
                assert response.headers.get("x-frame-options") == "DENY"
                assert "object-src 'none'" in response.headers.get("content-security-policy", "")
                page.get_by_placeholder("控制台密码").fill(PASSWORD)
                page.get_by_role("button", name="登录").click()
                page.wait_for_selector("#app:not([hidden])")
                assert page.get_by_text("本页怎么理解").is_visible()
                assert page.get_by_text("Main 主控").first.is_visible()
                assert page.locator("#mode-help").inner_text().startswith("仅观察")

                page.get_by_role("button", name="审批", exact=True).click()
                page.locator("#approvals.tab.active").wait_for()
                assert page.get_by_text("Browser exact Campaign approval", exact=True).is_visible()
                action = page.locator("#approval-list details.approval-action").first
                action.locator("summary").click()
                assert action.get_attribute("open") is not None
                assert "browser-campaign" in action.inner_text()
                argument_json = action.locator("pre").nth(0).inner_text()
                expected_json = action.locator("pre").nth(1).inner_text()
                assert "Browser Approved Campaign" in argument_json
                assert '"budget": 19' in argument_json
                assert '"state": "PAUSED"' in argument_json
                assert "Browser Approved Campaign" in expected_json
                assert page.get_by_text(approval_hash, exact=True).is_visible()
                assert page.get_by_role("button", name="批准", exact=True).is_visible()

                page.get_by_role("button", name="决策", exact=True).click()
                assert page.locator("#decisions").get_attribute("class") == "tab active"
                page.get_by_role("button", name="Profiles / MCP").click()
                assert page.get_by_text("目标 ACOS", exact=False).first.is_visible()

                page.get_by_role("button", name="自动运营").click()
                page.locator("#mode-pill").filter(has_text="AUTOPILOT").wait_for()
                assert "Executor 可写" in page.locator("#execution-pill").inner_text()
                assert "Executor 只执行" in page.locator("#mode-help").inner_text()

                page.get_by_role("button", name="暂停", exact=True).click()
                page.locator("#mode-pill").filter(has_text="PAUSED").wait_for()
                assert "阻断" in page.locator("#mode-help").inner_text()

                page.set_viewport_size({"width": 390, "height": 844})
                page.get_by_role("button", name="总览", exact=True).click()
                assert page.locator("header").bounding_box()["width"] <= 390
                assert page.locator(".controls").is_visible()
                page.reload(wait_until="networkidle")
                page.locator("#app").wait_for(state="visible")
                page.get_by_role("button", name="退出").click()
                page.wait_for_selector("#login:not([hidden])")
                browser.close()
        finally:
            server.shutdown(); server.server_close(); thread.join()
        assert not console_errors, f"browser console errors: {console_errors}"
        assert not page_errors, f"browser page errors: {page_errors}"
    print("browser-e2e: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
