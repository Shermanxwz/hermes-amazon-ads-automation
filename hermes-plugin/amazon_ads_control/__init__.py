from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import re
import threading
import time
from typing import Any

from . import client, outbox, resources, schemas, tools

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
    return session_id or task_id or kwargs.get("turn_id") or kwargs.get("session_key") or ""


def _result_sender(payload: dict[str, Any]) -> dict[str, Any]:
    return client.request("POST", "/api/agent/tool-result", payload, timeout=15)


def _flush_result_outbox() -> dict[str, Any]:
    if outbox.pending_count() <= 0:
        return {"attempted": 0, "delivered": 0, "remaining": 0}
    return outbox.flush(_result_sender, limit=100)


def _registry_catalog() -> list[dict[str, Any]]:
    from tools.registry import registry

    rows: list[dict[str, Any]] = []
    for name in sorted(registry.get_tool_names_for_toolset(TOOLSET)):
        if name.startswith(PREFIX):
            rows.append({
                "registered_name": name,
                "native_name": name[len(PREFIX):],
                "schema": registry.get_schema(name) or {},
                "enabled": True,
            })
    return rows


def sync_live_catalog(force: bool = False) -> dict[str, Any]:
    global _CATALOG_SYNCED_AT, _CATALOG
    with _CATALOG_LOCK:
        if not force and _CATALOG and time.monotonic() - _CATALOG_SYNCED_AT < 300:
            return {
                "cached": True,
                "tool_count": len(_CATALOG),
                "outbox_flush": _flush_result_outbox(),
            }
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
            response["outbox_flush"] = _flush_result_outbox()
        return response


def pre_llm_call(session_id=None, **kwargs):
    catalog = sync_live_catalog()
    runtime = resources.snapshot()
    outbox_state = outbox.maintenance()
    client.request("POST", "/api/agent/runtime-status", {
        "component": "hermes-plugin",
        "state": {"resources": runtime, "result_outbox": outbox_state},
    }, timeout=5)
    state = client.context(session_id or _session(**kwargs))
    if state.get("error"):
        return {"context": "Amazon Ads 控制面不可达。禁止调用任何 Amazon Ads MCP 工具，并明确报告控制面异常。"}
    task = state.get("task")
    approvals = state.get("approvals") if isinstance(state.get("approvals"), dict) else {}
    pending = approvals.get("pending") if isinstance(approvals.get("pending"), list) else []
    compact_pending = [
        {
            "id": item.get("id"),
            "summary": item.get("summary"),
            "risk": item.get("risk"),
            "actions": len(item.get("decision_ids") or []),
            "payload_hash": item.get("payload_hash"),
            "expires_at": item.get("expires_at"),
            "approve_command": f"/ads-approve {item.get('id')} {str(item.get('payload_hash') or '')[:12]}",
        }
        for item in pending[:10]
    ]
    compact = {
        "role": state.get("role"),
        "mode": state.get("mode"),
        "execution_enabled": state.get("execution_enabled"),
        "catalog": state.get("catalog"),
        "catalog_sync": catalog,
        "reports": state.get("reports"),
        "approvals_pending": compact_pending,
        "runtime_resources": runtime,
        "result_outbox": outbox_state,
        "task_id": task.get("id") if task else None,
        "task_status": task.get("status") if task else None,
        "decisions": [
            {"id": item.get("id"), "action": item.get("action_type"), "entity": item.get("entity_id"),
             "status": item.get("status"), "payload": item.get("payload")}
            for item in state.get("decisions", [])
        ],
    }
    approval_instruction = ""
    if compact_pending:
        approval_instruction = (
            "\n存在待人工批准的高风险计划。Main 必须向用户展示 Profile、动作、预算上限、"
            "Payload Hash、过期时间和精确 /ads-approve 命令。AI 不得代替用户批准，"
            "不得把普通自然语言回答当成授权。"
        )
    return {
        "context": "[Amazon Ads Control v3.2]\n"
        + json.dumps(compact, ensure_ascii=False)
        + approval_instruction
        + "\n"
        + state.get("instructions", "")
    }


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
    outbox_state = outbox.maintenance()
    if outbox_state.get("over_limit"):
        return {
            "action": "block",
            "message": "Amazon Ads durable result outbox reached its bounded safety limit; flush or repair it before new MCP operations",
        }
    result = client.request("POST", "/api/agent/tool-check", {
        "tool_name": tool_name, "args": args or {}, "session_id": session_id, "tool_call_id": tool_call_id,
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
    return outbox.deliver({
        "tool_name": tool_name,
        "args": args or {},
        "result": result,
        "session_id": session_id,
        "duration_ms": duration_ms,
        "tool_call_id": tool_call_id,
        "task_id": authorization.get("task_id"),
        "decision_id": authorization.get("decision_id"),
        "plan_key": authorization.get("plan_key"),
        "reservation_token": authorization.get("reservation_token"),
    }, _result_sender)


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
        "role": role_match.group(1).lower(), "goal": child_goal, "model": kwargs.get("child_model"),
    })


def subagent_stop(parent_session_id, child_status, child_summary=None, duration_ms=0, **kwargs):
    child_session_id = kwargs.get("child_session_id") or kwargs.get("session_id")
    if child_session_id:
        client.request("POST", "/api/agent/worker-stop", {
            "worker_session_id": child_session_id, "status": child_status,
            "summary": child_summary or "", "duration_ms": duration_ms,
        })


def _session_event(state: str, **kwargs):
    session_id = _session(**kwargs)
    if not session_id:
        return
    client.request("POST", "/api/agent/session-event", {
        "session_id": session_id,
        "state": state,
        "model": kwargs.get("model") or kwargs.get("model_name"),
        "provider": kwargs.get("provider"),
        "surface": kwargs.get("surface") or kwargs.get("platform"),
    }, timeout=5)


def on_session_start(**kwargs):
    _session_event("started", **kwargs)


def on_session_end(**kwargs):
    _session_event("ended", **kwargs)


def on_session_finalize(**kwargs):
    _session_event("ended", **kwargs)


def on_session_reset(**kwargs):
    _session_event("reset", **kwargs)


def post_llm_call(**kwargs):
    _session_event("active", **kwargs)


def _tool_handler(function):
    def handler(args, **context):
        if not isinstance(args, dict):
            raise ValueError("tool arguments must be an object")
        return function(**args, **context)
    return handler


def _pending_approvals() -> list[dict[str, Any]]:
    state = client.context("")
    approvals = state.get("approvals") if isinstance(state.get("approvals"), dict) else {}
    return approvals.get("pending") if isinstance(approvals.get("pending"), list) else []


def _approvals_command(raw_args: str = "") -> str:
    del raw_args
    pending = _pending_approvals()
    if not pending:
        return "当前没有待批准的 Amazon Ads 计划。"
    lines = ["待批准 Amazon Ads 计划："]
    for item in pending:
        digest = str(item.get("payload_hash") or "")
        lines.append(
            f"- {item.get('id')} | {item.get('risk')} | {item.get('summary')} | "
            f"{len(item.get('decision_ids') or [])} actions | hash {digest[:12]} | "
            f"expires {item.get('expires_at')}"
        )
    lines.append("批准：/ads-approve <approval_id> <hash前12位>")
    lines.append("拒绝：/ads-reject <approval_id> <原因>")
    return "\n".join(lines)


def _approve_command(raw_args: str = "") -> str:
    parts = raw_args.split()
    if len(parts) != 2:
        return "用法：/ads-approve <approval_id> <payload_hash前12位>"
    approval_id, prefix = parts
    pending = {str(item.get("id")): item for item in _pending_approvals()}
    approval = pending.get(approval_id)
    if not approval:
        return "找不到该待批准计划，或它已过期/已处理。"
    payload_hash = str(approval.get("payload_hash") or "")
    if len(prefix) < 12 or not hmac_compare(prefix, payload_hash[:len(prefix)]):
        return "Payload Hash 不匹配，未批准。请重新查看 /ads-approvals。"
    confirmation = f"APPROVE {approval_id} {payload_hash[:12]}"
    result = client.operator_request("POST", f"/api/operator/approvals/{approval_id}/approve", {
        "payload_hash": payload_hash,
        "confirmation": confirmation,
        "actor": "hermes-user-command",
    })
    if result.get("error"):
        return f"批准失败：{result.get('error')} {result.get('detail') or ''}".strip()
    return f"已批准计划 {approval_id}。授权仅绑定当前 Payload Hash，逐决策一次性消费。"


def _reject_command(raw_args: str = "") -> str:
    parts = raw_args.strip().split(maxsplit=1)
    if not parts:
        return "用法：/ads-reject <approval_id> <原因>"
    approval_id = parts[0]
    reason = parts[1] if len(parts) > 1 else "operator rejected"
    result = client.operator_request("POST", f"/api/operator/approvals/{approval_id}/reject", {
        "reason": reason,
        "actor": "hermes-user-command",
    })
    if result.get("error"):
        return f"拒绝失败：{result.get('error')} {result.get('detail') or ''}".strip()
    return f"已拒绝计划 {approval_id}。"


def hmac_compare(left: str, right: str) -> bool:
    import hmac
    return hmac.compare_digest(left, right)


def register(ctx):
    registrations = (
        ("ads_control_sync_catalog", schemas.SYNC_CATALOG, tools.sync_catalog),
        ("ads_control_create_report_job", schemas.CREATE_REPORT, tools.create_report_job),
        ("ads_control_report_evidence", schemas.REPORT_EVIDENCE, tools.report_evidence),
        ("ads_control_transition_report", schemas.TRANSITION_REPORT, tools.transition_report),
        ("ads_control_plan_cycle", schemas.PLAN_CYCLE, tools.plan_cycle),
        ("ads_control_create_task", schemas.CREATE_TASK, tools.create_task),
        ("ads_control_create_managed_plan", schemas.MANAGED_PLAN, tools.create_managed_plan),
        ("ads_control_request_approval", schemas.REQUEST_APPROVAL, tools.request_approval),
        ("ads_control_status", schemas.STATUS, tools.status),
        ("ads_control_record_note", schemas.NOTE, tools.record_note),
        ("ads_control_prepare_write", schemas.PREPARE_WRITE, tools.prepare_write),
        ("ads_control_read_evidence", schemas.READ_EVIDENCE, tools.read_evidence),
        ("ads_control_verify_decision", schemas.VERIFY, tools.verify_decision),
        ("ads_control_finalize_task", schemas.FINALIZE, tools.finalize_task),
        ("ads_control_ingest_stream_events", schemas.STREAM, tools.ingest_stream_events),
    )
    for name, schema, handler in registrations:
        ctx.register_tool(
            name=name,
            toolset="amazon-ads-control",
            schema=schema,
            handler=_tool_handler(handler),
            description=schema.get("description", ""),
        )
    for name, hook in (
        ("pre_llm_call", pre_llm_call),
        ("post_llm_call", post_llm_call),
        ("pre_tool_call", pre_tool_call),
        ("post_tool_call", post_tool_call),
        ("on_session_start", on_session_start),
        ("on_session_end", on_session_end),
        ("on_session_finalize", on_session_finalize),
        ("on_session_reset", on_session_reset),
        ("subagent_start", subagent_start),
        ("subagent_stop", subagent_stop),
    ):
        ctx.register_hook(name, hook)
    if hasattr(ctx, "register_command"):
        ctx.register_command(
            "ads-approvals",
            handler=_approvals_command,
            description="列出等待用户批准的 Amazon Ads 高风险计划",
        )
        ctx.register_command(
            "ads-approve",
            handler=_approve_command,
            description="批准一个精确 Payload Hash 绑定的 Amazon Ads 计划",
        )
        ctx.register_command(
            "ads-reject",
            handler=_reject_command,
            description="拒绝一个 Amazon Ads 高风险计划",
        )
    ctx.register_skill(name="amazon-ads-autopilot", path=Path(__file__).parent / "skill" / "SKILL.md")
