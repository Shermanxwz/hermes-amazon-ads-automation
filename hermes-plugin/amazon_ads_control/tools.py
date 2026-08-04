from __future__ import annotations

import json
from . import client


def _session(kwargs):
    return kwargs.get("session_id") or kwargs.get("task_id") or kwargs.get("turn_id") or ""


def sync_catalog(**kwargs):
    # Actual registry collection lives in the plugin module so hooks and this tool share a cache.
    from . import sync_live_catalog
    return json.dumps(sync_live_catalog(force=True), ensure_ascii=False)


def plan_cycle(snapshot, policy=None, **kwargs):
    return json.dumps(client.request("POST", "/api/agent/cycles/plan", {
        "snapshot": snapshot, "policy": policy or {}, "actor": "hermes-main",
        "parent_session_id": _session(kwargs),
    }, timeout=30), ensure_ascii=False)


def create_task(cycle_id, limit=25, **kwargs):
    return json.dumps(client.request("POST", "/api/agent/tasks", {
        "cycle_id": cycle_id, "limit": limit, "parent_session_id": _session(kwargs), "actor": "hermes-main",
    }), ensure_ascii=False)


def status(**kwargs):
    return json.dumps(client.context(_session(kwargs)), ensure_ascii=False)


def record_note(message, task_id=None, level="info", **kwargs):
    return json.dumps(client.request("POST", "/api/agent/events", {
        "level": level, "type": "agent.note", "actor": "hermes", "task_id": task_id,
        "message": message, "data": {"session_id": _session(kwargs)},
    }), ensure_ascii=False)


def read_evidence(decision_id, limit=20, **kwargs):
    return json.dumps(client.request("POST", "/api/agent/read-evidence", {
        "decision_id": decision_id, "limit": limit, "session_id": _session(kwargs),
    }), ensure_ascii=False)


def verify_decision(decision_id, evidence_action_id, message="", **kwargs):
    return json.dumps(client.request("POST", "/api/agent/verify", {
        "decision_id": decision_id, "evidence_action_id": evidence_action_id, "message": message,
        "session_id": _session(kwargs),
    }), ensure_ascii=False)


def finalize_task(task_id, summary, **kwargs):
    return json.dumps(client.request("POST", "/api/agent/task-finalize", {
        "task_id": task_id, "summary": summary, "actor": "hermes-main",
    }), ensure_ascii=False)


def ingest_stream_events(events, **kwargs):
    return json.dumps(client.request("POST", "/api/agent/stream-events", {"events": events}, timeout=15), ensure_ascii=False)
