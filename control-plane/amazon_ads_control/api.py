from __future__ import annotations

from dataclasses import dataclass
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from importlib import resources
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import Settings
from .db import Store
from .security import LoginRateLimiter, SessionStore, constant_token_match, verify_password
from .service import ControlService

MAX_BODY = 8 * 1024 * 1024


@dataclass
class App:
    settings: Settings
    store: Store
    service: ControlService
    sessions: SessionStore
    login_limiter: LoginRateLimiter


def _json_bytes(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "HermesAdsControl/2.0"
    app: App

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _security_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:")

    def _respond(self, status: int, data: Any, headers: dict[str, str] | None = None) -> None:
        body = _json_bytes(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._security_headers()
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
        self._security_headers()
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

    @staticmethod
    def _limit(query: dict[str, list[str]], default: int, maximum: int = 1000) -> int:
        try:
            return max(1, min(maximum, int(query.get("limit", [default])[0])))
        except (TypeError, ValueError):
            return default

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path, query = parsed.path, parse_qs(parsed.query)
        try:
            if path == "/health/live":
                self._respond(200, {"ok": True})
            elif path == "/health/ready":
                dashboard = self.app.store.dashboard()
                self._respond(200, {"ok": True, "database": "ready", "mode": dashboard["settings"].get("mode"), "catalog": dashboard["catalog"]})
            elif path == "/api/session":
                session = self._browser_session()
                self._respond(200, {"authenticated": bool(session), "csrf": session.csrf if session else None})
            elif path == "/api/agent/context":
                if self._require_agent():
                    self._respond(200, self.app.service.context(query.get("session_id", [None])[0]))
            elif path == "/":
                self._static("index.html")
            elif path.startswith("/static/"):
                self._static(path.removeprefix("/static/"))
            elif not self._require_browser():
                return
            elif path == "/api/dashboard":
                self._respond(200, self.app.store.dashboard())
            elif path == "/api/cycles":
                self._respond(200, {"cycles": self.app.store.list_cycles(self._limit(query, 50), query.get("profile_id", [None])[0])})
            elif path == "/api/decisions":
                self._respond(200, {"decisions": self.app.store.list_decisions(
                    cycle_id=query.get("cycle_id", [None])[0], task_id=query.get("task_id", [None])[0],
                    status=query.get("status", [None])[0], limit=self._limit(query, 200),
                )})
            elif path == "/api/tasks":
                self._respond(200, {"tasks": self.app.store.list_tasks(self._limit(query, 100), query.get("status", [None])[0])})
            elif path == "/api/actions":
                self._respond(200, {"actions": self.app.store.list_actions(self._limit(query, 200), query.get("task_id", [None])[0])})
            elif path == "/api/verifications":
                self._respond(200, {"verifications": self.app.store.list_verifications(
                    self._limit(query, 200), query.get("task_id", [None])[0], query.get("decision_id", [None])[0]
                )})
            elif path == "/api/events":
                self._respond(200, {"events": self.app.store.list_events(self._limit(query, 200))})
            elif path == "/api/alerts":
                status = query.get("status", ["open"])[0]
                self._respond(200, {"alerts": self.app.store.list_alerts(self._limit(query, 100), None if status == "all" else status)})
            elif path == "/api/workers":
                self._respond(200, {"workers": self.app.store.list_workers(self._limit(query, 100))})
            elif path == "/api/profiles":
                self._respond(200, {"profiles": self.app.store.list_profiles()})
            elif path == "/api/catalog":
                self._respond(200, {"tools": self.app.store.list_tools(self._limit(query, 500, 2000))})
            elif path == "/api/settings":
                self._respond(200, self.app.store.get_settings())
            else:
                self._respond(404, {"error": "not_found"})
        except Exception as exc:
            self.app.store.event("error", "api.get_error", "controller", None, str(exc), {"path": path})
            self._respond(500, {"error": "internal_error"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            data = self._body()
        except ValueError as exc:
            self._respond(400, {"error": str(exc)})
            return
        try:
            if path == "/api/login":
                login_key = "dashboard"
                permitted, retry_after = self.app.login_limiter.allowed(login_key)
                if not permitted:
                    self._respond(429, {"error": "login_rate_limited", "retry_after": retry_after}, {"Retry-After": str(retry_after)})
                    return
                if not verify_password(str(data.get("password", "")), self.app.settings.control_password_hash):
                    permitted, retry_after = self.app.login_limiter.failure(login_key)
                    self.app.store.event("warning", "auth.failed", "browser", None, "Dashboard login failed", {"rate_limited": not permitted})
                    headers = {"Retry-After": str(retry_after)} if not permitted else None
                    self._respond(429 if not permitted else 401, {"error": "login_rate_limited" if not permitted else "invalid_credentials", "retry_after": retry_after}, headers)
                    return
                self.app.login_limiter.success(login_key)
                sid, csrf = self.app.sessions.create()
                secure = "; Secure" if self.app.settings.public_origin.startswith("https://") else ""
                headers = {"Set-Cookie": f"ads_control_session={sid}; HttpOnly; SameSite=Strict{secure}; Path=/; Max-Age={self.app.settings.session_ttl_seconds}"}
                self._respond(200, {"ok": True, "csrf": csrf}, headers)
                return
            if path == "/api/logout":
                if self._require_browser(mutate=True):
                    self.app.sessions.revoke(self._cookie_sid())
                    self._respond(200, {"ok": True}, {"Set-Cookie": "ads_control_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"})
                return
            if not self._require_agent():
                return
            routes = {
                "/api/agent/catalog-sync": lambda: self.app.service.sync_catalog(data),
                "/api/agent/cycles/plan": lambda: self.app.service.plan_cycle(data, str(data.get("actor") or "hermes-main")),
                "/api/agent/tasks": lambda: self.app.service.create_task(data, str(data.get("actor") or "hermes-main")),
                "/api/agent/worker-bind": lambda: self.app.service.bind_worker(data),
                "/api/agent/tool-check": lambda: self.app.service.authorize_tool(data),
                "/api/agent/tool-result": lambda: self.app.service.finish_tool(data),
                "/api/agent/verify": lambda: self.app.service.verify_decision(data),
                "/api/agent/task-finalize": lambda: self.app.service.finalize_task(data, str(data.get("actor") or "hermes-main")),
                "/api/agent/stream-events": lambda: self.app.service.ingest_stream(data),
            }
            if path in routes:
                result = routes[path]()
                if path == "/api/agent/tool-check" and not result.get("allowed", False):
                    self._respond(403, result)
                else:
                    self._respond(201 if path in {"/api/agent/cycles/plan", "/api/agent/tasks"} else 200, result)
            elif path == "/api/agent/worker-stop":
                self.app.store.finish_worker(
                    str(data.get("worker_session_id") or ""), str(data.get("status") or "completed"),
                    str(data.get("summary") or ""), int(data.get("duration_ms") or 0),
                )
                self._respond(200, {"ok": True})
            elif path == "/api/agent/events":
                event_id = self.app.store.event(
                    str(data.get("level") or "info"), str(data.get("type") or "agent.event"),
                    str(data.get("actor") or "hermes"), data.get("task_id"), str(data.get("message") or ""),
                    data.get("data") if isinstance(data.get("data"), dict) else {},
                )
                self._respond(201, {"id": event_id})
            else:
                self._respond(404, {"error": "not_found"})
        except (ValueError, KeyError) as exc:
            self._respond(400, {"error": str(exc)})
        except Exception as exc:
            self.app.store.event("error", "api.post_error", "controller", None, str(exc), {"path": path})
            self._respond(500, {"error": "internal_error"})

    def do_PUT(self) -> None:
        path = urlparse(self.path).path
        try:
            data = self._body()
        except ValueError as exc:
            self._respond(400, {"error": str(exc)})
            return
        if not self._require_browser(mutate=True):
            return
        try:
            if path == "/api/settings":
                self._respond(200, self.app.store.update_settings(data))
            elif path.startswith("/api/catalog/") and path.endswith("/acknowledge"):
                tool_name = path[len("/api/catalog/"):-len("/acknowledge")].strip("/")
                self.app.store.acknowledge_tool_drift(tool_name)
                self._respond(200, {"ok": True})
            elif path.startswith("/api/profiles/"):
                profile_id = path.removeprefix("/api/profiles/").strip("/")
                current = self.app.store.get_profile(profile_id) or {"profile_id": profile_id}
                current.update(data)
                current["profile_id"] = profile_id
                self._respond(200, self.app.store.upsert_profile(current))
            else:
                self._respond(404, {"error": "not_found"})
        except (ValueError, KeyError) as exc:
            self._respond(400, {"error": str(exc)})


def build_server(settings: Settings, store: Store | None = None) -> ThreadingHTTPServer:
    store = store or Store(settings.db_path)
    app = App(settings=settings, store=store, service=ControlService(store),
              sessions=SessionStore(settings.session_ttl_seconds, settings.max_sessions),
              login_limiter=LoginRateLimiter())
    handler = type("ConfiguredHandler", (Handler,), {"app": app})
    return ThreadingHTTPServer((settings.host, settings.port), handler)
