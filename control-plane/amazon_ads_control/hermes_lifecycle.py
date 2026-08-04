from __future__ import annotations

from urllib.parse import urlparse

_INSTALLED = False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from .api import Handler
    from .service import ControlService

    original_context = ControlService.context
    original_post = Handler.do_POST

    def hermes_session_event(self, payload):
        session_id = str(payload.get("session_id") or "").strip()
        state = str(payload.get("state") or "active").strip().lower()
        if not session_id:
            raise ValueError("session_id is required")
        if state not in {"started", "active", "reset", "ended"}:
            raise ValueError("invalid Hermes session state")
        self.store.record_hermes_session(
            session_id,
            state,
            str(payload.get("model") or "") or None,
            str(payload.get("provider") or "") or None,
            str(payload.get("surface") or "") or None,
        )
        return {"recorded": True, "session_id": session_id, "state": state}

    def context(self, session_id):
        result = original_context(self, session_id)
        if session_id:
            with self.store.connection() as conn:
                row = conn.execute(
                    "SELECT * FROM hermes_sessions WHERE session_id=?",
                    (session_id,),
                ).fetchone()
            result["hermes_session"] = dict(row) if row else None
        return result

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/api/agent/session-event":
            return original_post(self)
        try:
            data = self._body()
        except ValueError as exc:
            self._respond(400, {"error": str(exc)})
            return
        if not self._require_agent():
            return
        try:
            self._respond(200, self.app.service.hermes_session_event(data))
        except (ValueError, KeyError) as exc:
            self._respond(400, {"error": str(exc)})
        except Exception as exc:
            self.app.store.event(
                "error", "api.hermes_session_error", "controller", None, str(exc), {"path": path}
            )
            self._respond(500, {"error": "internal_error"})

    ControlService.hermes_session_event = hermes_session_event
    ControlService.context = context
    Handler.do_POST = do_POST
    _INSTALLED = True
