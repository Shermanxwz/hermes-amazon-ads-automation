from __future__ import annotations

import json
from . import client


def _session(kwargs):
    return kwargs.get("session_id") or kwargs.get("task_id") or ""


def create_task(title, kind, objective, write_allowed=None, constraints=None, evidence=None, expected_actions=None, **kwargs):
    payload = {
        "title": title, "kind": kind, "objective": objective,
        "write_allowed": kind in {"optimization", "recovery"} if write_allowed is None else write_allowed,
        "constraints": constraints or {}, "evidence": evidence or {}, "expected_actions": expected_actions or [],
        "parent_session_id": _session(kwargs), "actor": "hermes-main",
    }
    return json.dumps(client.request("POST", "/api/agent/tasks", payload), ensure_ascii=False)


def status(**kwargs):
    return json.dumps(client.context(_session(kwargs)), ensure_ascii=False)


def task(task_id, **kwargs):
    # Dashboard API is browser-only; context includes the currently bound task. This method
    # intentionally remains minimal and asks the control plane to emit a lookup note.
    current = client.context(_session(kwargs))
    if current.get("task", {}).get("id") == task_id:
        return json.dumps(current["task"], ensure_ascii=False)
    return json.dumps({"task_id": task_id, "message": "Task is not bound to this session; use the dashboard or delegate it with the task marker."}, ensure_ascii=False)


def record_note(message, task_id=None, level="info", **kwargs):
    return json.dumps(client.request("POST", "/api/agent/events", {
        "level": level, "type": "agent.note", "actor": "hermes", "task_id": task_id,
        "message": message, "data": {"session_id": _session(kwargs)},
    }), ensure_ascii=False)


def complete_task(status, summary, verification, **kwargs):
    session_id = _session(kwargs)
    return json.dumps(client.request("POST", "/api/agent/worker-stop", {
        "worker_session_id": session_id,
        "status": status,
        "summary": summary,
        "verification": verification,
    }), ensure_ascii=False)
