from __future__ import annotations

from importlib import resources
from urllib.parse import parse_qs, unquote, urlparse

from .runtime_readiness import (
    authorize_with_runtime_gate,
    create_task_with_runtime_gate,
    readiness_snapshot,
)

_INSTALLED = False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from .api import Handler

    original_get = Handler.do_GET
    original_post = Handler.do_POST
    original_put = Handler.do_PUT
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
        if parsed.path == "/health/ready":
            state = readiness_snapshot(self.app.store)
            status = (
                200
                if state["service_ready"]
                and (not state["autopilot_requested"] or state["writable"])
                else 503
            )
            self._respond(status, state)
            return
        if parsed.path != "/api/reports":
            return original_get(self)
        if not self._require_browser():
            return
        query = parse_qs(parsed.query)
        self._respond(
            200,
            {
                "reports": self.app.store.list_report_jobs(
                    self._limit(query, 100),
                    query.get("profile_id", [None])[0],
                    query.get("status", [None])[0],
                )
            },
        )

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        routes = {
            "/api/agent/reports": "create_report",
            "/api/agent/reports/transition": "transition_report",
            "/api/agent/report-evidence": "report_evidence",
            "/api/agent/prepare-write": "prepare_write",
            "/api/agent/runtime-status": "runtime_status",
            # Runtime readiness is enforced at the network boundary used by
            # Hermes. Direct service methods remain deterministic and easy to
            # exercise in unit tests.
            "/api/agent/tool-check": "authorize_tool_runtime",
            "/api/agent/tasks": "create_task_runtime",
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
            if method == "authorize_tool_runtime":
                result = authorize_with_runtime_gate(self.app.service, data)
                self._respond(200 if result.get("allowed", False) else 403, result)
                return
            if method == "create_task_runtime":
                result = create_task_with_runtime_gate(
                    self.app.service,
                    data,
                    str(data.get("actor") or "hermes-main"),
                )
                self._respond(201, result)
                return
            result = getattr(self.app.service, method)(data)
            self._respond(201 if method == "create_report" else 200, result)
        except (ValueError, KeyError) as exc:
            self._respond(400, {"error": str(exc)})
        except Exception as exc:
            self.app.store.event(
                "error",
                "api.closed_loop_error",
                "controller",
                None,
                str(exc),
                {"path": path},
            )
            self._respond(500, {"error": "internal_error"})

    def do_PUT(self) -> None:
        path = urlparse(self.path).path
        if not (path.startswith("/api/catalog/") and path.endswith("/acknowledge")):
            return original_put(self)
        try:
            data = self._body()
        except ValueError as exc:
            self._respond(400, {"error": str(exc)})
            return
        if not self._require_browser(mutate=True):
            return
        tool_name = unquote(
            path[len("/api/catalog/") : -len("/acknowledge")].strip("/")
        )
        expected_hash = str(data.get("schema_hash") or "").strip().lower()
        if len(expected_hash) != 64 or any(
            char not in "0123456789abcdef" for char in expected_hash
        ):
            self._respond(
                400, {"error": "a full 64-character schema_hash is required"}
            )
            return
        try:
            with self.app.store.connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    row = conn.execute(
                        "SELECT schema_hash,enabled,drifted FROM mcp_tools WHERE registered_name=?",
                        (tool_name,),
                    ).fetchone()
                    if not row or not row["enabled"]:
                        raise KeyError("enabled MCP tool not found")
                    if str(row["schema_hash"]).lower() != expected_hash:
                        raise ValueError(
                            "schema hash changed; refresh and review the current schema"
                        )
                    if not row["drifted"]:
                        raise ValueError("tool has no pending schema drift")
                    cursor = conn.execute(
                        "UPDATE mcp_tools SET drifted=0 WHERE registered_name=? AND enabled=1 AND drifted=1 AND lower(schema_hash)=?",
                        (tool_name, expected_hash),
                    )
                    if cursor.rowcount != 1:
                        raise ValueError(
                            "schema drift changed concurrently; refresh and retry"
                        )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
            self.app.store.event(
                "warning",
                "mcp.catalog.drift_acknowledged",
                "operator",
                None,
                f"Acknowledged schema drift for {tool_name}",
                {"schema_hash": expected_hash},
            )
            self._respond(200, {"ok": True, "schema_hash": expected_hash})
        except (ValueError, KeyError) as exc:
            self._respond(400, {"error": str(exc)})
        except Exception as exc:
            self.app.store.event(
                "error",
                "api.put_error",
                "controller",
                None,
                str(exc),
                {"path": path},
            )
            self._respond(500, {"error": "internal_error"})

    Handler._static = static
    Handler.do_GET = do_GET
    Handler.do_POST = do_POST
    Handler.do_PUT = do_PUT
    _INSTALLED = True
