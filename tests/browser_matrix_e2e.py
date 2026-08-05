#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import tempfile
import threading

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
    "schema": {
        "description": "Create one campaign",
        "parameters": {
            "type": "object",
            "required": ["campaigns"],
            "properties": {"campaigns": {"type": "array", "minItems": 1, "maxItems": 1}},
        },
    },
}


def unavailable(route: Route) -> None:
    route.fulfill(
        status=503,
        content_type="application/json",
        body='{"error":"simulated_dashboard_unavailable"}',
    )


def exercise(browser_type: BrowserType, root: Path) -> None:
    settings = Settings(
        host="127.0.0.1",
        port=0,
        db_path=root / f"{browser_type.name}.db",
        public_origin="",
        control_password_hash=hash_password(PASSWORD),
        agent_token="a" * 48,
        session_ttl_seconds=3600,
        max_sessions=8,
        retention_days=30,
        allow_remote_bind=False,
    )
    store = Store(settings.db_path)
    store.sync_catalog([descriptor_from_payload(TOOL)])
    store.record_runtime_status(
        "hermes-plugin",
        {
            "readiness_protocol": 1,
            "result_outbox": {"pending": 0, "bytes": 0, "over_limit": False},
            "catalog_sync": {"ok": True, "tool_count": 1},
        },
    )
    server = build_server(settings, store=store)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    browser = browser_type.launch(headless=True)
    context = browser.new_context(viewport={"width": 1100, "height": 780})
    page = context.new_page()
    console_errors: list[str] = []
    page_errors: list[str] = []
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    try:
        response = page.goto(base, wait_until="networkidle")
        assert response and response.ok
        page.get_by_placeholder("控制台密码").fill(PASSWORD)
        page.get_by_role("button", name="登录").click()
        page.wait_for_selector("#app:not([hidden])")
        page.locator("#readiness-pill").filter(has_text="READY").wait_for()

        page.get_by_role("button", name="自动运营").click()
        page.locator("#readiness-pill").filter(has_text="WRITABLE").wait_for()
        assert page.locator("#execution-pill").inner_text() == "Executor 可写"

        page.route("**/api/dashboard", unavailable)
        page.get_by_role("button", name="刷新").click()
        page.locator("#notice:not([hidden])").wait_for()
        assert "simulated_dashboard_unavailable" in page.locator("#notice").inner_text()
        page.unroute("**/api/dashboard", unavailable)
        page.get_by_role("button", name="刷新").click()
        page.locator("#readiness-pill").filter(has_text="WRITABLE").wait_for()

        context.clear_cookies()
        page.reload(wait_until="networkidle")
        page.wait_for_selector("#login:not([hidden])")
    finally:
        browser.close()
        server.shutdown()
        server.server_close()
        thread.join()
    assert not console_errors, f"{browser_type.name} console errors: {console_errors}"
    assert not page_errors, f"{browser_type.name} page errors: {page_errors}"


def main() -> int:
    with sync_playwright() as playwright, tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for browser_type in (playwright.chromium, playwright.firefox, playwright.webkit):
            exercise(browser_type, root)
    print("browser-matrix-e2e: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
