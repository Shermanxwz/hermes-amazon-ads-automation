#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import tempfile
import threading
import traceback

from playwright.sync_api import BrowserType, Route, sync_playwright

from amazon_ads_control.api import build_server
from amazon_ads_control.catalog import descriptor_from_payload
from amazon_ads_control.config import Settings
from amazon_ads_control.db import Store
from amazon_ads_control.security import hash_password

PASSWORD = "correct horse battery staple"
TOOL = {
    "registered_name": "mcp_amazon_ads_campaign_management_create_campaign",
    "native_name": "campaign_management-create_campaign",
    "source": "hermes-registry:na",
    "schema": {"description": "Create one campaign", "parameters": {"type": "object", "required": ["campaigns"], "properties": {"campaigns": {"type": "array", "minItems": 1, "maxItems": 1}}}},
}


def unavailable(route: Route) -> None:
    route.fulfill(status=503, content_type="application/json", body='{"error":"simulated_dashboard_unavailable"}')


def exercise(browser_type: BrowserType, root: Path) -> None:
    browser_name = browser_type.name
    stage = "settings"
    settings = Settings(host="127.0.0.1", port=0, db_path=root / f"{browser_name}.db", public_origin="", control_password_hash=hash_password(PASSWORD), agent_token="a" * 48, session_ttl_seconds=3600, max_sessions=8, retention_days=30, allow_remote_bind=False)
    store = Store(settings.db_path)
    store.sync_catalog([descriptor_from_payload(TOOL)])
    store.record_runtime_status("hermes-plugin", {"readiness_protocol": 1, "result_outbox": {"pending": 0, "bytes": 0, "over_limit": False}, "catalog_sync": {"ok": True, "tool_count": 1}})
    server = build_server(settings, store=store)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    browser = None
    console_errors: list[str] = []
    expected_fault_errors: list[str] = []
    page_errors: list[str] = []
    fault_injection_active = [False]
    try:
        stage = "launch"
        browser = browser_type.launch(headless=True)
        context = browser.new_context(viewport={"width": 1100, "height": 780})
        page = context.new_page()
        def capture_console(message) -> None:
            if message.type == "error":
                (expected_fault_errors if fault_injection_active[0] else console_errors).append(message.text)
        page.on("console", capture_console)
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        stage = "initial-load"
        response = page.goto(base, wait_until="domcontentloaded")
        assert response and response.ok
        page.get_by_placeholder("控制台密码").fill(PASSWORD)
        page.get_by_role("button", name="登录").click()
        page.wait_for_selector("#app:not([hidden])")
        page.locator("#readiness-pill").filter(has_text="READY").wait_for()
        assert page.locator("#kpis .metric-card").count() == 4
        assert page.get_by_text("审批", exact=True).count() == 0
        stage = "autopilot"
        page.get_by_role("button", name="全托管运行").click()
        page.locator("#readiness-pill").filter(has_text="WRITABLE").wait_for()
        assert page.locator("#mode-pill").inner_text() == "AUTOPILOT"
        stage = "fault-injection"
        fault_injection_active[0] = True
        page.route("**/api/dashboard", unavailable)
        page.get_by_role("button", name="刷新").click()
        page.locator("#notice").filter(has_text="simulated_dashboard_unavailable").wait_for(state="visible")
        page.unroute("**/api/dashboard", unavailable)
        page.wait_for_timeout(50)
        fault_injection_active[0] = False
        stage = "fault-recovery"
        page.get_by_role("button", name="刷新").click()
        page.locator("#readiness-pill").filter(has_text="WRITABLE").wait_for()
        stage = "responsive"
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.locator(".mode-buttons").is_visible()
        assert page.locator("#activity-list").is_visible()
        stage = "session-expiry"
        context.clear_cookies()
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#login:not([hidden])")
    except Exception:
        print(f"browser-matrix: {browser_name}: FAILED at {stage}", flush=True)
        traceback.print_exc()
        raise
    finally:
        if browser is not None:
            browser.close()
        server.shutdown(); server.server_close(); thread.join()
    assert not console_errors, f"{browser_name} console errors: {console_errors}"
    assert not page_errors, f"{browser_name} page errors: {page_errors}"
    print(f"browser-matrix: {browser_name}: PASS", flush=True)


def main() -> int:
    with sync_playwright() as playwright, tempfile.TemporaryDirectory() as td:
        for browser_type in (playwright.chromium, playwright.firefox, playwright.webkit):
            exercise(browser_type, Path(td))
    print("browser-matrix-e2e: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
