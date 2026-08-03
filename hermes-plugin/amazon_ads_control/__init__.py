from __future__ import annotations

import json
import re
from . import client, schemas, tools

TASK_MARKER = re.compile(r"\[ads-task:([a-f0-9]{8,32})\]", re.I)
ADS_HINT = re.compile(r"(^|[-_.])(amazon|ads|campaign|ad_group|keyword|target|portfolio|budget|bid)([-_.]|$)", re.I)
WRITE_HINT = re.compile(r"(^|[-_.])(create|update|delete|archive|pause|resume|enable|disable|set|adjust|apply|mutate|add|remove|copy)([-_.]|$)", re.I)
READ_HINT = re.compile(r"(^|[-_.])(get|list|query|retrieve|check|describe|search|report|status)([-_.]|$)", re.I)


def _ads_kind(tool_name):
    lowered = tool_name.lower()
    if not (lowered.startswith("mcp_amazon_ads_") or lowered.startswith("amazon_ads_") or ADS_HINT.search(lowered)):
        return "other"
    if WRITE_HINT.search(lowered):
        return "write"
    if READ_HINT.search(lowered):
        return "read"
    return "unknown"


def _session(task_id=None, session_id=None, **kwargs):
    return session_id or task_id or kwargs.get("turn_id") or ""


def pre_llm_call(session_id=None, **kwargs):
    state = client.context(session_id)
    if state.get("error"):
        return {"context": "Amazon Ads 控制面当前不可达。禁止执行任何 Amazon Ads 写操作；只做只读诊断并明确报告控制面异常。"}
    task = state.get("task")
    compact = {
        "role": state.get("role"), "mode": state.get("mode"),
        "execution_enabled": state.get("execution_enabled"),
        "task_id": task.get("id") if task else None,
        "task_objective": task.get("payload", {}).get("objective") if task else None,
    }
    return {"context": "[Amazon Ads Control]\n" + json.dumps(compact, ensure_ascii=False) + "\n" + state.get("instructions", "")}


def pre_tool_call(tool_name, args, task_id="", **kwargs):
    # Avoid recursion for this plugin's own control tools and leave unrelated Hermes tools alone.
    if tool_name.startswith("ads_control_"):
        return None
    kind = _ads_kind(tool_name)
    if kind == "other":
        return None
    result = client.request("POST", "/api/agent/tool-check", {
        "tool_name": tool_name, "args": args or {},
        "session_id": _session(task_id=task_id, **kwargs),
    })
    if result.get("error"):
        if kind == "read":
            return None
        return {"action": "block", "message": "Amazon Ads 控制面不可达；写操作和未知操作已 fail-closed"}
    if result.get("allowed") is False:
        message = result.get("reason") or "Amazon Ads control policy denied the tool"
        return {"action": "block", "message": message}
    return None


def post_tool_call(tool_name, args, result, task_id="", duration_ms=0, **kwargs):
    if tool_name.startswith("ads_control_") or _ads_kind(tool_name) == "other":
        return
    client.request("POST", "/api/agent/tool-result", {
        "tool_name": tool_name, "args": args or {}, "result": result,
        "session_id": _session(task_id=task_id, **kwargs), "duration_ms": duration_ms,
    })


def subagent_start(parent_session_id, child_session_id, child_subagent_id, child_role, child_goal, **kwargs):
    match = TASK_MARKER.search(child_goal or "")
    if not match or not child_session_id:
        client.request("POST", "/api/agent/events", {
            "level": "warning", "type": "worker.unbound", "actor": "hermes-main",
            "message": "Delegated child has no Amazon Ads task marker",
            "data": {"child_subagent_id": child_subagent_id, "goal": (child_goal or "")[:500]},
        })
        return
    client.request("POST", "/api/agent/worker-bind", {
        "task_id": match.group(1), "parent_session_id": parent_session_id,
        "worker_session_id": child_session_id, "worker_subagent_id": child_subagent_id,
        "role": "worker", "goal": child_goal,
    })


def subagent_stop(parent_session_id, child_status, child_summary=None, duration_ms=0, **kwargs):
    child_session_id = kwargs.get("child_session_id") or kwargs.get("session_id")
    if child_session_id:
        client.request("POST", "/api/agent/worker-stop", {
            "worker_session_id": child_session_id, "status": child_status,
            "summary": child_summary or "", "duration_ms": duration_ms,
        })
    else:
        client.request("POST", "/api/agent/events", {
            "level": "info", "type": "worker.stop", "actor": "hermes-main",
            "message": f"Subagent stopped: {child_status}",
            "data": {"parent_session_id": parent_session_id, "duration_ms": duration_ms, "summary": (child_summary or "")[:1000]},
        })


def _tool_handler(function):
    """Adapt Hermes' canonical ``handler(args: dict, **context)`` contract."""
    def handler(args, **context):
        if not isinstance(args, dict):
            raise ValueError("tool arguments must be an object")
        return function(**args, **context)
    return handler


def register(ctx):
    ctx.register_tool(name="ads_control_create_task", toolset="amazon-ads-control", schema=schemas.CREATE_TASK, handler=_tool_handler(tools.create_task))
    ctx.register_tool(name="ads_control_status", toolset="amazon-ads-control", schema=schemas.STATUS, handler=_tool_handler(tools.status))
    ctx.register_tool(name="ads_control_task", toolset="amazon-ads-control", schema=schemas.TASK, handler=_tool_handler(tools.task))
    ctx.register_tool(name="ads_control_record_note", toolset="amazon-ads-control", schema=schemas.NOTE, handler=_tool_handler(tools.record_note))
    ctx.register_tool(name="ads_control_complete_task", toolset="amazon-ads-control", schema=schemas.COMPLETE, handler=_tool_handler(tools.complete_task))
    ctx.register_hook("pre_llm_call", pre_llm_call)
    ctx.register_hook("pre_tool_call", pre_tool_call)
    ctx.register_hook("post_tool_call", post_tool_call)
    ctx.register_hook("subagent_start", subagent_start)
    ctx.register_hook("subagent_stop", subagent_stop)
    skill_md = __import__("pathlib").Path(__file__).parent / "skill" / "SKILL.md"
    ctx.register_skill(name="amazon-ads-autopilot", path=skill_md)
