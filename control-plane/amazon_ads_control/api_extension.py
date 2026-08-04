from __future__ import annotations

from urllib.parse import parse_qs, urlparse

_INSTALLED = False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from .api import Handler

    original_get = Handler.do_GET
    original_post = Handler.do_POST

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

    Handler.do_GET = do_GET
    Handler.do_POST = do_POST
    _INSTALLED = True
