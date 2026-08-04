from __future__ import annotations

from importlib import resources
from urllib.parse import parse_qs, urlparse

_INSTALLED = False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from .api import Handler

    original_get = Handler.do_GET
    original_post = Handler.do_POST
    original_static = Handler._static

    def static(self, filename: str) -> None:
        safe = filename.strip("/") or "index.html"
        if safe != "app_v3.js":
            return original_static(self, filename)
        try:
            body = resources.files("amazon_ads_control.static").joinpath(safe).read_bytes()
        except FileNotFoundError:
            self._respond(404, {"error": "not_found"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/javascript; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/reports":
            return original_get(self)
        if not self._require_browser():
            return
        query = parse_qs(parsed.query)
        self._respond(200, {
            "reports": self.app.store.list_report_jobs(
                self._limit(query, 100),
                query.get("profile_id", [None])[0],
                query.get("status", [None])[0],
            )
        })

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        routes = {
            "/api/agent/reports": "create_report",
            "/api/agent/reports/transition": "transition_report",
            "/api/agent/prepare-write": "prepare_write",
            "/api/agent/runtime-status": "runtime_status",
        }
        method = routes.get(path)
        if not method:
            return original_post(self)
        try:
            data = self._body()
        except ValueError as exc:
            self._respond(400, {"error": str(exc)})
            return
        if not self._require_agent():
            return
        try:
            result = getattr(self.app.service, method)(data)
            self._respond(201 if method == "create_report" else 200, result)
        except (ValueError, KeyError) as exc:
            self._respond(400, {"error": str(exc)})
        except Exception as exc:
            self.app.store.event("error", "api.closed_loop_error", "controller", None, str(exc), {"path": path})
            self._respond(500, {"error": "internal_error"})

    Handler._static = static
    Handler.do_GET = do_GET
    Handler.do_POST = do_POST
    _INSTALLED = True
