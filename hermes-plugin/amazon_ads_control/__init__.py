from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import re
import threading
import time
from typing import Any

from . import client, schemas, tools

PREFIX = "mcp_amazon_ads_"
TOOLSET = "mcp-amazon-ads"
TASK_MARKER = re.compile(r"\[ads-task:([a-f0-9]{8,32})\]", re.I)
ROLE_MARKER = re.compile(r"\[ads-role:(executor|verifier)\]", re.I)
_CATALOG_LOCK = threading.Lock()
_CATALOG: dict[str, dict[str, Any]] = {}
_CATALOG_SYNCED_AT = 0.0
_PENDING_LOCK = threading.Lock()
_PENDING_BY_CALL: dict[str, dict[str, Any]] = {}
_PENDING_FALLBACK: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)


def _session(task_id=None, session_id=None, **kwargs):
    return session_id or task_id or kwargs.get("turn_id") or ""


def _registry_catalog() -> list[dict[str, Any]]:
    from tools.registry import registry

    names = registry.get_tool_names_for_toolset(TOOLSET)
    rows: list[dict[str, Any]] = []
    for name in sorted(names):
        if not name.startswith(PREFIX):
            continue
        schema = registry.get_schema(name) or {}
        rows.append({
            "registered_name": name,
            "native_name": name[len(PREFIX):],
            "server_name": "amazon-ads",
            "schema": schema,
            "source": "hermes-registry",
            "enabled": True,
        })
    return rows


def sync_live_catalog(force: bool = False) -> dict[str, Any]:
    global _CATALOG_SYNCED_AT, _CATALOG
    with _CATALOG_LOCK:
        if not force and _CATALOG and time.monotonic() - _CATALOG_SYNCED_AT < 300:
            return {"cached": True, "tool_count": len(_CATALOG)}
        try:
            rows = _registry_catalog()
        except Exception as exc:
            return {"error": "hermes_registry_unavailable", "detail": str(exc)}
        if not rows:
            return {"error": "amazon_ads_toolset_empty", "toolset": TOOLSET}
        response = client.request("POST", "/api/agent/catalog-sync", {"tools": rows}, timeout=15)
        if not response.get("error"):
            _CATALOG = {row["registered_name"]: row for row in rows}
            _CATALOG_SYNCED_AT = time.monotonic()
        return response


def pre_llm_call(session_id=None, **kwargs):
    catalog = sync_live_catalog()
    state = client.context(session_id)
    if state.get("error"):
        return {"context": "Amazon Ads 控制面不可达。禁止调用任何 Amazon Ads MCP 工具，并明确报告控制面异常。"}
    task = state.get("task")
    compact = {
        "role": state.get("role"), "mode": state.get("mode"),
        "execution_enabled": state.get("execution_enabled"),
        "catalog": state.get("catalog"), "catalog_sync": catalog,
        "task_id": task.get("id") if task else None,
        "task_status": task.get("status") if task else None,
        "decisions": [
            {"id": item.get("id"), "action": item.get("action_type"), "entity": item.get("entity_id"),
             "status": item.get("status"), "payload": item.get("payload")}
            for item in state.get("decisions", [])
        ],
    }
    return {"context": "[Amazon Ads Control v2]\n" + json.dumps(compact, ensure_ascii=False) + "\n" + state.get("instructions", "")}


def _remember_authorization(tool_call_id: str | None, session_id: str, tool_name: str, result: dict[str, Any]) -> None:
    with _PENDING_LOCK:
        if tool_call_id:
            _PENDING_BY_CALL[tool_call_id] = result
        else:
            _PENDING_FALLBACK[(session_id, tool_name)].append(result)


def _take_authorization(tool_call_id: str | None, session_id: str, tool_name: str) -> dict[str, Any]:
    with _PENDING_LOCK:
        if tool_call_id:
            return _PENDING_BY_CALL.pop(tool_call_id, {})
        queue = _PENDING_FALLBACK.get((session_id, tool_name), [])
        result = queue.pop(0) if queue else {}
        if not queue:
            _PENDING_FALLBACK.pop((session_id, tool_name), None)
        return result


def pre_tool_call(tool_name, args, task_id="", tool_call_id=None, **kwargs):
    if tool_name.startswith("ads_control_") or not tool_name.startswith(PREFIX):
        return None
    session_id = _session(task_id=task_id, **kwargs)
    sync = sync_live_catalog()
    if sync.get("error"):
        return {"action": "block", "message": "Amazon Ads MCP catalog refresh is unavailable; operation failed closed"}
    result = client.request("POST", "/api/agent/tool-check", {
        "tool_name": tool_name, "args": args or {}, "session_id": session_id,
        "tool_call_id": tool_call_id,
    })
    if result.get("error"):
        return {"action": "block", "message": "Amazon Ads control plane unavailable; operation failed closed"}
    if result.get("allowed") is False:
        return {"action": "block", "message": result.get("reason") or "Amazon Ads policy denied the tool"}
    _remember_authorization(tool_call_id, session_id, tool_name, result)
    return None


def post_tool_call(tool_name, args, result, task_id="", duration_ms=0, tool_call_id=None, **kwargs):
    if tool_name.startswith("ads_control_") or not tool_name.startswith(PREFIX):
        return
    session_id = _session(task_id=task_id, **kwargs)
    authorization = _take_authorization(tool_call_id, session_id, tool_name)
    client.request("POST", "/api/agent/tool-result", {
        "tool_name": tool_name, "args": args or {}, "result": result,
        "session_id": session_id, "duration_ms": duration_ms, "tool_call_id": tool_call_id,
        "task_id": authorization.get("task_id"), "decision_id": authorization.get("decision_id"),
        "plan_key": authorization.get("plan_key"), "reservation_token": authorization.get("reservation_token"),
    }, timeout=15)


def subagent_start(parent_session_id, child_session_id, child_subagent_id, child_role, child_goal, **kwargs):
    match = TASK_MARKER.search(child_goal or "")
    role_match = ROLE_MARKER.search(child_goal or "")
    if not match or not role_match or not child_session_id:
        client.request("POST", "/api/agent/events", {
            "level": "warning", "type": "worker.unbound", "actor": "hermes-main",
            "message": "Delegated Amazon Ads child requires both task and role markers",
            "data": {"child_subagent_id": child_subagent_id, "goal": (child_goal or "")[:500]},
        })
        return
    client.request("POST", "/api/agent/worker-bind", {
        "task_id": match.group(1), "parent_session_id": parent_session_id,
        "worker_session_id": child_session_id, "worker_subagent_id": child_subagent_id,
        "role": role_match.group(1).lower(), "goal": child_goal,
        "model": kwargs.get("child_model"),
    })


def subagent_stop(parent_session_id, child_status, child_summary=None, duration_ms=0, **kwargs):
    child_session_id = kwargs.get("child_session_id") or kwargs.get("session_id")
    if child_session_id:
        client.request("POST", "/api/agent/worker-stop", {
            "worker_session_id": child_session_id, "status": child_status,
            "summary": child_summary or "", "duration_ms": duration_ms,
        })


def _tool_handler(function):
    def handler(args, **context):
        if not isinstance(args, dict):
            raise ValueError("tool arguments must be an object")
        return function(**args, **context)
    return handler


def register(ctx):
    registrations = (
        ("ads_control_sync_catalog", schemas.SYNC_CATALOG, tools.sync_catalog),
        ("ads_control_plan_cycle", schemas.PLAN_CYCLE, tools.plan_cycle),
        ("ads_control_create_task", schemas.CREATE_TASK, tools.create_task),
        ("ads_control_status", schemas.STATUS, tools.status),
        ("ads_control_record_note", schemas.NOTE, tools.record_note),
        ("ads_control_read_evidence", schemas.READ_EVIDENCE, tools.read_evidence),
        ("ads_control_verify_decision", schemas.VERIFY, tools.verify_decision),
        ("ads_control_finalize_task", schemas.FINALIZE, tools.finalize_task),
        ("ads_control_ingest_stream_events", schemas.STREAM, tools.ingest_stream_events),
    )
    for name, schema, handler in registrations:
        ctx.register_tool(name=name, toolset="amazon-ads-control", schema=schema, handler=_tool_handler(handler))
    for name, hook in (
        ("pre_llm_call", pre_llm_call), ("pre_tool_call", pre_tool_call), ("post_tool_call", post_tool_call),
        ("subagent_start", subagent_start), ("subagent_stop", subagent_stop),
    ):
        ctx.register_hook(name, hook)
    ctx.register_skill(name="amazon-ads-autopilot", path=Path(__file__).parent / "skill" / "SKILL.md")
