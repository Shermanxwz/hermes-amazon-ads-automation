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


# ---------------------------------------------------------------------------
# Gateway idle detection
# ---------------------------------------------------------------------------
#
# The hermes-plugin heartbeat (POST /api/agent/runtime-status) is only emitted
# from `pre_llm_call`, which only fires when the hermes gateway actually
# invokes the LLM. When the gateway is idle (no chat session, no scheduled
# task, no platform binding) the heartbeat naturally goes stale even though
# the gateway is healthy. Without an exemption, the readiness gate would
# permanently 503 on a clean idle install (no writes are even possible because
# nothing is invoking the LLM in the first place).
#
# To distinguish "idle but healthy" from "active but stale", we look at
# authoritative activity tables inside the control plane's own state.db. If
# there is no in-flight task / decision / worker / approval / hermes-session,
# AND the most recent activity across those tables is older than
# ADS_RUNTIME_IDLE_WINDOW_SECONDS (default 10 minutes), the gateway is
# genuinely idle and the heartbeat_fresh check is exempted. Any subsequent
# activity (a fresh task, a worker that just bound, a new pending approval,
# etc.) immediately re-arms the heartbeat check because the activity tables
# are now non-empty / non-stale.
#
# This intentionally avoids:
#  - synthetic DB writes (would mask the symptom and go stale themselves)
#  - background heartbeat daemons (out of scope, would require a new process)
#  - weakening any non-heartbeat check (outbox/catalog/DB still gate writes)

_INFLIGHT_TASK_STATUSES = ("pending", "running", "executing", "verifying")
_INFLIGHT_DECISION_STATUSES = ("pending", "executed", "uncertain")
_INFLIGHT_APPROVAL_STATUSES = ("pending", "approved", "expired_in_flight")
_INFLIGHT_SESSION_WHERE = "ended_at IS NULL"


def _table_has_inflight(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> bool:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return False
    return int(row[0] or 0) > 0


def _latest_iso(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> datetime | None:
    row = conn.execute(sql, params).fetchone()
    if row is None or row[0] is None:
        return None
    return _time(row[0])


def gateway_is_idle(store: Any, *, now: datetime | None = None, window_seconds: int | None = None) -> bool:
    """Return True when no LLM-driven activity is in flight and the most
    recent activity timestamp is older than the idle window.

    The control plane's own state.db is the authoritative source here: if
    there are no pending tasks / decisions / workers / approvals / hermes
    sessions, then by construction no LLM call is currently being routed
    through the hermes gateway against this control plane, so the
    hermes-plugin heartbeat cannot be expected to be fresh.

    The window is configurable via ``ADS_RUNTIME_IDLE_WINDOW_SECONDS``
    (default 600s = 10 minutes). When set to 0, only the in-flight check
    matters; when set to a very large value, even brief quiet spells look
    idle. The default 10 minutes matches the production heartbeat threshold
    (120s) plus a generous slack for benign gateway quiet periods between
    chat sessions.
    """
    now = (now or datetime.now(UTC)).astimezone(UTC)
    window = window_seconds if window_seconds is not None else _env(
        "ADS_RUNTIME_IDLE_WINDOW_SECONDS", 600, 0, 86400,
    )
    cutoff = now.timestamp() - float(window)
    with store.connection() as conn:
        # 1) in-flight activity — if anything is actively running, NOT idle.
        if _table_has_inflight(
            conn,
            "SELECT COUNT(*) FROM tasks WHERE status IN ("
            + ",".join("?" for _ in _INFLIGHT_TASK_STATUSES)
            + ")",
            _INFLIGHT_TASK_STATUSES,
        ):
            return False
        if _table_has_inflight(
            conn,
            "SELECT COUNT(*) FROM decisions WHERE status IN ("
            + ",".join("?" for _ in _INFLIGHT_DECISION_STATUSES)
            + ")",
            _INFLIGHT_DECISION_STATUSES,
        ):
            return False
        if _table_has_inflight(
            conn, "SELECT COUNT(*) FROM workers WHERE status='running'",
        ):
            return False
        if _table_has_inflight(
            conn,
            "SELECT COUNT(*) FROM approval_requests WHERE status IN ("
            + ",".join("?" for _ in _INFLIGHT_APPROVAL_STATUSES)
            + ")",
            _INFLIGHT_APPROVAL_STATUSES,
        ):
            return False
        if _table_has_inflight(
            conn,
            f"SELECT COUNT(*) FROM hermes_sessions WHERE {_INFLIGHT_SESSION_WHERE}",
        ):
            return False
        # 2) most recent activity across the activity tables — must be older
        #    than the idle window. If the DB has never seen activity at all
        #    (fresh install), treat that as idle.
        latest_candidates = [
            _latest_iso(conn, "SELECT MAX(created_at) FROM tasks"),
            _latest_iso(conn, "SELECT MAX(created_at) FROM decisions"),
            _latest_iso(conn, "SELECT MAX(last_seen_at) FROM workers"),
            _latest_iso(conn, "SELECT MAX(requested_at) FROM approval_requests"),
            _latest_iso(conn, "SELECT MAX(last_seen_at) FROM hermes_sessions"),
        ]
        # also consider recorded tool actions: a write-tool action means a
        # tool check fired, which means the gateway was active recently.
        latest_candidates.append(_latest_iso(conn, "SELECT MAX(created_at) FROM actions"))
    latest = max((t for t in latest_candidates if t is not None), default=None)
    if latest is None:
        return True  # fresh install, never seen activity → idle
    return latest.timestamp() <= cutoff


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
        "idle_window": _env("ADS_RUNTIME_IDLE_WINDOW_SECONDS", 600, 0, 86400),
    }
    pending = int(outbox.get("pending") or state.get("result_outbox_pending") or 0)
    size = int(outbox.get("bytes") or 0)
    callbacks = int(dashboard.get("pending_callbacks") or 0)
    mode = str(settings.get("mode") or "observe").lower()
    configured = mode == "autopilot" and bool(settings.get("execution_enabled"))
    # Detect gateway idle state once, then use it to exempt the heartbeat
    # freshness check ONLY when configured (autopilot). A non-autopilot
    # install never blocks on heartbeat anyway, but the exemption is
    # documented as a no-op in observe mode so the readiness logic stays
    # consistent across modes.
    idle = gateway_is_idle(store, now=now, window_seconds=limits["idle_window"])
    heartbeat_fresh = age is not None and age <= limits["heartbeat"]
    heartbeat_fresh_effective = heartbeat_fresh or (configured and idle)
    heartbeat_exempted = (not heartbeat_fresh) and heartbeat_fresh_effective
    checks = {
        "database_integrity": bool(integrity.get("ok")),
        "database_writable": db_write,
        "catalog_loaded": int(catalog.get("tools") or 0) > 0,
        "catalog_drift_clear": int(catalog.get("drifted") or 0) == 0,
        "storage_below_hard_limit": str(maintenance.get("pressure") or "normal") != "hard",
        "hermes_plugin_present": plugin is not None,
        "hermes_plugin_protocol_supported": protocol >= READINESS_PROTOCOL,
        "hermes_plugin_heartbeat_fresh": heartbeat_fresh_effective,
        "catalog_sync_healthy": bool(sync) and sync.get("ok") is True and not sync.get("error"),
        "result_outbox_below_limit": not bool(outbox.get("over_limit")),
        "result_outbox_backlog_below_threshold": pending <= limits["outbox"] and size <= limits["bytes"],
        "pending_callbacks_below_threshold": callbacks <= limits["callbacks"],
    }
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
            "runtime_idle_window_seconds": limits["idle_window"],
        },
        "observed": {
            "pending_callbacks": callbacks,
            "result_outbox_pending": pending,
            "result_outbox_bytes": size,
            "hermes_plugin_age_seconds": round(age, 3) if age is not None else None,
            "hermes_plugin_readiness_protocol": protocol,
            "hermes_plugin_last_seen": plugin.get("updated_at") if plugin else None,
            "gateway_idle": idle,
            "heartbeat_idle_exempted": heartbeat_exempted,
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
