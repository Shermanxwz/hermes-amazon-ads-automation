from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from importlib import resources
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import Settings
from .db import Store
from .security import SessionStore, constant_token_match, verify_password
from .service import ControlService

MAX_BODY = 1_048_576


@dataclass
class App:
    settings: Settings
    store: Store
    service: ControlService
    sessions: SessionStore


def _json_bytes(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "HermesAdsControl/1.0"
    app: App

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _respond(self, status: int, data: Any, headers: dict[str, str] | None = None) -> None:
        body = _json_bytes(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _static(self, filename: str) -> None:
        safe = filename.strip("/") or "index.html"
        if ".." in safe or safe not in {"index.html", "app.js", "style.css"}:
            self._respond(404, {"error": "not_found"})
            return
        try:
            body = resources.files("amazon_ads_control.static").joinpath(safe).read_bytes()
        except FileNotFoundError:
            self._respond(404, {"error": "not_found"})
            return
        content_type = mimetypes.guess_type(safe)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith(("text/", "application/javascript")) else content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0 or length > MAX_BODY:
            raise ValueError("invalid body size")
        try:
            data = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as exc:
            raise ValueError("invalid JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data

    def _cookie_sid(self) -> str | None:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get("ads_control_session")
        return morsel.value if morsel else None

    def _browser_session(self):
        return self.app.sessions.validate(self._cookie_sid())

    def _require_browser(self, mutate: bool = False) -> bool:
        session = self._browser_session()
        if not session:
            self._respond(401, {"error": "authentication_required"})
            return False
        if mutate:
            origin = self.headers.get("Origin", "").rstrip("/")
            if self.app.settings.public_origin and origin != self.app.settings.public_origin:
                self._respond(403, {"error": "origin_mismatch"})
                return False
            if not constant_token_match(self.headers.get("X-CSRF-Token"), session.csrf):
                self._respond(403, {"error": "csrf_failed"})
                return False
        return True

    def _require_agent(self) -> bool:
        auth = self.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        if not constant_token_match(token, self.app.settings.agent_token):
            self._respond(401, {"error": "invalid_agent_token"})
            return False
        return True

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path, query = parsed.path, parse_qs(parsed.query)
        if path == "/health/live":
            self._respond(200, {"ok": True})
        elif path == "/health/ready":
            try:
                self.app.store.get_settings()
                self._respond(200, {"ok": True, "database": "ready"})
            except Exception as exc:
                self._respond(503, {"ok": False, "error": str(exc)})
        elif path == "/api/session":
            session = self._browser_session()
            self._respond(200, {"authenticated": bool(session), "csrf": session.csrf if session else None})
        elif path == "/api/dashboard":
            if self._require_browser():
                self._respond(200, self.app.store.dashboard())
        elif path == "/api/tasks":
            if self._require_browser():
                self._respond(200, {"tasks": self.app.store.list_tasks(int(query.get("limit", [100])[0]))})
        elif path == "/api/actions":
            if self._require_browser():
                self._respond(200, {"actions": self.app.store.list_actions(int(query.get("limit", [200])[0]), query.get("task_id", [None])[0])})
        elif path == "/api/events":
            if self._require_browser():
                self._respond(200, {"events": self.app.store.list_events(int(query.get("limit", [200])[0]))})
        elif path == "/api/workers":
            if self._require_browser():
                self._respond(200, {"workers": self.app.store.list_workers()})
        elif path == "/api/settings":
            if self._require_browser():
                self._respond(200, self.app.store.get_settings())
        elif path == "/api/agent/context":
            if self._require_agent():
                self._respond(200, self.app.service.context(query.get("session_id", [None])[0]))
        elif path == "/":
            self._static("index.html")
        elif path.startswith("/static/"):
            self._static(path.removeprefix("/static/"))
        else:
            self._respond(404, {"error": "not_found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            data = self._body()
        except ValueError as exc:
            self._respond(400, {"error": str(exc)})
            return
        try:
            if path == "/api/login":
                if not verify_password(str(data.get("password", "")), self.app.settings.control_password_hash):
                    self.app.store.event("warning", "auth.failed", "browser", None, "Dashboard login failed", {})
                    self._respond(401, {"error": "invalid_credentials"})
                    return
                sid, csrf = self.app.sessions.create()
                secure = "; Secure" if self.app.settings.public_origin.startswith("https://") else ""
                headers = {"Set-Cookie": f"ads_control_session={sid}; HttpOnly; SameSite=Strict{secure}; Path=/; Max-Age={self.app.settings.session_ttl_seconds}"}
                self._respond(200, {"ok": True, "csrf": csrf}, headers)
            elif path == "/api/logout":
                if self._require_browser(mutate=True):
                    self.app.sessions.revoke(self._cookie_sid())
                    self._respond(200, {"ok": True}, {"Set-Cookie": "ads_control_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"})
            elif path == "/api/agent/tasks":
                if self._require_agent():
                    self._respond(201, self.app.service.create_task(data, str(data.get("actor", "hermes-main"))))
            elif path == "/api/agent/worker-bind":
                if self._require_agent():
                    self._respond(200, self.app.service.bind_worker(data))
            elif path == "/api/agent/tool-check":
                if self._require_agent():
                    result = self.app.service.authorize_tool(data)
                    self._respond(200 if result["allowed"] else 403, result)
            elif path == "/api/agent/tool-result":
                if self._require_agent():
                    self._respond(200, self.app.service.finish_tool(data))
            elif path == "/api/agent/worker-stop":
                if self._require_agent():
                    self.app.store.finish_worker(
                        str(data.get("worker_session_id", "")), str(data.get("status", "completed")),
                        str(data.get("summary", "")), int(data.get("duration_ms") or 0),
                        data.get("verification") if isinstance(data.get("verification"), dict) else {},
                    )
                    self._respond(200, {"ok": True})
            elif path == "/api/agent/events":
                if self._require_agent():
                    event_id = self.app.store.event(
                        str(data.get("level", "info")), str(data.get("type", "agent.event")),
                        str(data.get("actor", "hermes")), data.get("task_id"),
                        str(data.get("message", "")), data.get("data") if isinstance(data.get("data"), dict) else {},
                    )
                    self._respond(201, {"id": event_id})
            else:
                self._respond(404, {"error": "not_found"})
        except (ValueError, KeyError) as exc:
            self._respond(400, {"error": str(exc)})
        except Exception as exc:
            self.app.store.event("error", "api.error", "controller", None, str(exc), {"path": path})
            self._respond(500, {"error": "internal_error"})

    def do_PUT(self) -> None:
        path = urlparse(self.path).path
        try:
            data = self._body()
        except ValueError as exc:
            self._respond(400, {"error": str(exc)})
            return
        if path != "/api/settings":
            self._respond(404, {"error": "not_found"})
            return
        if not self._require_browser(mutate=True):
            return
        try:
            self._respond(200, self.app.store.update_settings(data))
        except ValueError as exc:
            self._respond(400, {"error": str(exc)})


def build_server(settings: Settings, store: Store | None = None) -> ThreadingHTTPServer:
    store = store or Store(settings.db_path)
    app = App(settings=settings, store=store, service=ControlService(store),
              sessions=SessionStore(settings.session_ttl_seconds, settings.max_sessions))
    handler = type("ConfiguredHandler", (Handler,), {"app": app})
    return ThreadingHTTPServer((settings.host, settings.port), handler)
