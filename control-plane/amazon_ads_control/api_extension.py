from __future__ import annotations

from importlib import resources
from urllib.parse import parse_qs, unquote, urlparse

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
            self.app.store.reconcile_expired_reservations()
            integrity = self.app.store.integrity_check()
            dashboard = self.app.store.dashboard()
            settings = dashboard.get("settings") if isinstance(dashboard.get("settings"), dict) else {}
            catalog = dashboard.get("catalog") if isinstance(dashboard.get("catalog"), dict) else {}
            storage = dashboard.get("storage") if isinstance(dashboard.get("storage"), dict) else {}
            maintenance = storage.get("latest_maintenance") if isinstance(storage.get("latest_maintenance"), dict) else {}
            runtime = dashboard.get("runtime_status") if isinstance(dashboard.get("runtime_status"), list) else []
            plugin = next((item for item in runtime if item.get("component") == "hermes-plugin"), None)
            plugin_state = plugin.get("state") if isinstance(plugin, dict) and isinstance(plugin.get("state"), dict) else {}
            outbox = plugin_state.get("result_outbox") if isinstance(plugin_state.get("result_outbox"), dict) else {}
            checks = {
                "database_integrity": bool(integrity.get("ok")),
                "catalog_loaded": int(catalog.get("tools") or 0) > 0,
                "catalog_drift_clear": int(catalog.get("drifted") or 0) == 0,
                "storage_below_hard_limit": str(maintenance.get("pressure") or "normal") != "hard",
                "result_outbox_below_limit": not bool(outbox.get("over_limit")),
            }
            mode = str(settings.get("mode") or "observe")
            execution_enabled = bool(settings.get("execution_enabled"))
            autopilot_requested = mode == "autopilot"
            autopilot_ready = all(checks.values()) and execution_enabled
            service_ready = checks["database_integrity"]
            status = 200 if service_ready and (not autopilot_requested or autopilot_ready) else 503
            self._respond(status, {
                "ok": service_ready,
                "service_ready": service_ready,
                "autopilot_ready": autopilot_ready,
                "autopilot_requested": autopilot_requested,
                "checks": checks,
                "database": integrity,
                "mode": mode,
                "execution_enabled": execution_enabled,
                "catalog": catalog,
                "pending_callbacks": int(dashboard.get("pending_callbacks") or 0),
                "hermes_plugin_last_seen": plugin.get("updated_at") if isinstance(plugin, dict) else None,
            })
            return
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
            "/api/agent/report-evidence": "report_evidence",
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
        tool_name = unquote(path[len("/api/catalog/"):-len("/acknowledge")].strip("/"))
        expected_hash = str(data.get("schema_hash") or "").strip().lower()
        if len(expected_hash) != 64 or any(char not in "0123456789abcdef" for char in expected_hash):
            self._respond(400, {"error": "a full 64-character schema_hash is required"})
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
                        raise ValueError("schema hash changed; refresh and review the current schema")
                    if not row["drifted"]:
                        raise ValueError("tool has no pending schema drift")
                    cursor = conn.execute(
                        "UPDATE mcp_tools SET drifted=0 WHERE registered_name=? AND enabled=1 AND drifted=1 AND lower(schema_hash)=?",
                        (tool_name, expected_hash),
                    )
                    if cursor.rowcount != 1:
                        raise ValueError("schema drift changed concurrently; refresh and retry")
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
            self.app.store.event(
                "warning", "mcp.catalog.drift_acknowledged", "operator", None,
                f"Acknowledged schema drift for {tool_name}",
                {"schema_hash": expected_hash},
            )
            self._respond(200, {"ok": True, "schema_hash": expected_hash})
        except (ValueError, KeyError) as exc:
            self._respond(400, {"error": str(exc)})
        except Exception as exc:
            self.app.store.event("error", "api.put_error", "controller", None, str(exc), {"path": path})
            self._respond(500, {"error": "internal_error"})

    Handler._static = static
    Handler.do_GET = do_GET
    Handler.do_POST = do_POST
    Handler.do_PUT = do_PUT
    _INSTALLED = True
