from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Any

from .policy import redact

UTC = timezone.utc
READINESS_PROTOCOL = 1


def _env(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(low, min(high, value))


def _time(value: Any) -> datetime | None:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)


def _database_writable(store: Any) -> tuple[bool, str | None]:
    try:
        with store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.rollback()
        return True, None
    except Exception as exc:
        return False, str(exc)[:500]


def readiness_snapshot(store: Any, *, now: datetime | None = None) -> dict[str, Any]:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    store.reconcile_expired_reservations()
    integrity = store.integrity_check()
    dashboard = store.dashboard()
    settings = dashboard.get("settings") or {}
    catalog = dashboard.get("catalog") or {}
    maintenance = (dashboard.get("storage") or {}).get("latest_maintenance") or {}
    plugin = next(
        (
            row
            for row in dashboard.get("runtime_status") or []
            if isinstance(row, dict) and row.get("component") == "hermes-plugin"
        ),
        None,
    )
    state = plugin.get("state") if isinstance(plugin, dict) else {}
    state = state if isinstance(state, dict) else {}
    outbox = state.get("result_outbox") if isinstance(state.get("result_outbox"), dict) else {}
    sync = state.get("catalog_sync") if isinstance(state.get("catalog_sync"), dict) else {}
    updated = _time(plugin.get("updated_at") if plugin else None)
    age = max(0.0, (now - updated).total_seconds()) if updated else None
    try:
        protocol = int(state.get("readiness_protocol") or 0)
    except (TypeError, ValueError):
        protocol = 0
    db_write, db_error = _database_writable(store)
    limits = {
        "heartbeat": _env("ADS_HERMES_HEARTBEAT_MAX_AGE_SECONDS", 120, 15, 3600),
        "callbacks": _env("ADS_PENDING_CALLBACKS_READY_LIMIT", 25, 0, 100000),
        "outbox": _env("ADS_RESULT_OUTBOX_READY_LIMIT", 100, 0, 100000),
        "bytes": _env("ADS_RESULT_OUTBOX_READY_BYTES", 8 * 1024 * 1024, 1024, 1024**3),
    }
    pending = int(outbox.get("pending") or state.get("result_outbox_pending") or 0)
    size = int(outbox.get("bytes") or 0)
    callbacks = int(dashboard.get("pending_callbacks") or 0)
    checks = {
        "database_integrity": bool(integrity.get("ok")),
        "database_writable": db_write,
        "catalog_loaded": int(catalog.get("tools") or 0) > 0,
        "catalog_drift_clear": int(catalog.get("drifted") or 0) == 0,
        "storage_below_hard_limit": str(maintenance.get("pressure") or "normal") != "hard",
        "hermes_plugin_present": plugin is not None,
        "hermes_plugin_protocol_supported": protocol >= READINESS_PROTOCOL,
        "hermes_plugin_heartbeat_fresh": age is not None and age <= limits["heartbeat"],
        "catalog_sync_healthy": bool(sync) and sync.get("ok") is True and not sync.get("error"),
        "result_outbox_below_limit": not bool(outbox.get("over_limit")),
        "result_outbox_backlog_below_threshold": pending <= limits["outbox"] and size <= limits["bytes"],
        "pending_callbacks_below_threshold": callbacks <= limits["callbacks"],
    }
    mode = str(settings.get("mode") or "observe").lower()
    configured = mode == "autopilot" and bool(settings.get("execution_enabled"))
    ready = all(checks.values())
    service_ready = checks["database_integrity"] and checks["database_writable"]
    writable = configured and ready
    blocked = configured and not writable
    operational = "writable" if writable else "blocked" if blocked else "ready" if ready else "degraded" if service_ready else "unavailable"
    return {
        "protocol": READINESS_PROTOCOL,
        "ok": service_ready,
        "service_ready": service_ready,
        "configured": configured,
        "ready": ready,
        "writable": writable,
        "blocked": blocked,
        "degraded": service_ready and not ready,
        "operational_state": operational,
        "autopilot_requested": mode == "autopilot",
        "autopilot_ready": writable,
        "mode": mode,
        "execution_enabled": bool(settings.get("execution_enabled")),
        "checks": checks,
        "blocking_checks": [name for name, passed in checks.items() if not passed],
        "thresholds": {
            "hermes_heartbeat_max_age_seconds": limits["heartbeat"],
            "pending_callbacks": limits["callbacks"],
            "result_outbox_pending": limits["outbox"],
            "result_outbox_bytes": limits["bytes"],
        },
        "observed": {
            "pending_callbacks": callbacks,
            "result_outbox_pending": pending,
            "result_outbox_bytes": size,
            "hermes_plugin_age_seconds": round(age, 3) if age is not None else None,
            "hermes_plugin_readiness_protocol": protocol,
            "hermes_plugin_last_seen": plugin.get("updated_at") if plugin else None,
            "database_write_error": db_error,
        },
        "database": integrity,
        "catalog": catalog,
    }


def _block(service: Any, payload: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    tool_name = str(payload.get("tool_name") or "")
    args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
    session = str(payload.get("session_id") or payload.get("task_id") or "") or None
    worker = service.store.worker_for_session(session)
    role = worker.get("role") if worker else "main"
    task_id = worker.get("task_id") if worker else None
    reason = "runtime readiness gate blocked write: " + ", ".join(state["blocking_checks"])
    if session:
        service.store.touch_worker(session)
    action_id = service.store.record_action(
        decision_id=None,
        task_id=task_id,
        session_id=session,
        tool_call_id=str(payload.get("tool_call_id") or "") or None,
        actor_role=str(role),
        phase="before",
        tool_name=tool_name,
        operation="write",
        allowed=False,
        plan_key=None,
        reservation_token=None,
        args=redact(args),
        reason=reason,
    )
    service.store.event(
        "critical",
        "RUNTIME_NOT_READY",
        str(role),
        task_id,
        f"Blocked {tool_name}: {reason}",
        {"action_id": action_id, "blocking_checks": state["blocking_checks"]},
    )
    return {
        "allowed": False,
        "reason": reason,
        "operation": "write",
        "actor_role": role,
        "task_id": task_id,
        "action_id": action_id,
        "decision_id": None,
        "plan_key": None,
        "reservation_token": None,
        "runtime_readiness": state,
        "tool": None,
    }


def authorize_with_runtime_gate(service: Any, payload: dict[str, Any]) -> dict[str, Any]:
    tool = service.store.get_tool(str(payload.get("tool_name") or ""))
    settings = service.store.get_settings()
    configured = str(settings.get("mode") or "observe").lower() == "autopilot" and bool(settings.get("execution_enabled"))
    if tool and tool.get("semantic") == "write" and configured:
        state = readiness_snapshot(service.store)
        if not state["writable"]:
            return _block(service, payload, state)
    return service.authorize_tool(payload)


def create_task_with_runtime_gate(service: Any, payload: dict[str, Any], actor: str) -> dict[str, Any]:
    settings = service.store.get_settings()
    configured = str(settings.get("mode") or "observe").lower() == "autopilot" and bool(settings.get("execution_enabled"))
    if configured and bool(payload.get("write_allowed", True)):
        state = readiness_snapshot(service.store)
        if not state["writable"]:
            service.store.event(
                "warning",
                "WRITE_TASK_NOT_CREATED",
                actor,
                None,
                "Runtime readiness gate rejected a new write-enabled task",
                {"blocking_checks": state["blocking_checks"]},
            )
            raise ValueError("runtime readiness gate blocks new write task: " + ", ".join(state["blocking_checks"]))
    return service.create_task(payload, actor)
