from __future__ import annotations

import json
from typing import Any

_INSTALLED = False
_UNCERTAIN = {"pending", "uncertain"}
_TERMINAL_BAD = {"failed", "mismatch"}


def _persist_state(store: Any, task_id: str, state: str, decision_id: str) -> None:
    with store.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT payload_json FROM tasks WHERE id=?", (task_id,)).fetchone()
            payload = json.loads(row["payload_json"] or "{}") if row else {}
            payload.update({
                "activation_state": state,
                "activation_blocking_decision_id": decision_id,
            })
            conn.execute(
                "UPDATE tasks SET payload_json=? WHERE id=?",
                (json.dumps(payload, ensure_ascii=False), task_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from . import sealed_activation
    from .db import Store

    original_advance = sealed_activation._advance_activation
    original_mark_execution = Store.mark_execution

    def advance(store: Any, task_id: str) -> dict[str, Any]:
        rows = store.list_decisions(task_id=task_id, limit=500)
        activation = [item for item in rows if sealed_activation._is_activation(item)]
        if not activation:
            return original_advance(store, task_id)
        creates = [
            item for item in rows
            if item.get("action_type") in sealed_activation._CREATE_SPECS
            and not sealed_activation._is_activation(item)
        ]
        uncertain = [item for item in creates + activation if item.get("status") in _UNCERTAIN]
        if uncertain:
            blocker = uncertain[0]
            failed = sealed_activation._fail_blocked_activations(
                store,
                task_id,
                "an activation-related Amazon write is pending or uncertain; no later stage may execute",
            )
            _persist_state(store, task_id, "write_uncertain", str(blocker.get("id") or ""))
            store.alert_once(
                "critical",
                "SEALED_ACTIVATION_WRITE_UNCERTAIN",
                blocker.get("profile_id"),
                task_id,
                blocker.get("id"),
                "An Amazon create or activation write has no certain outcome; all remaining activation stages are quarantined",
                {
                    "decision_id": blocker.get("id"),
                    "decision_status": blocker.get("status"),
                    "blocked_activations": failed,
                },
                window_seconds=86400,
            )
            return {
                "applied": True,
                "state": "write_uncertain",
                "decision_id": blocker.get("id"),
                "failed": failed,
            }
        return original_advance(store, task_id)

    def mark_execution(self, *args, **kwargs):
        result = original_mark_execution(self, *args, **kwargs)
        decision_id = str(kwargs.get("decision_id") or (args[0] if args else ""))
        decision = self.get_decision(decision_id) or {}
        task_id = str(decision.get("task_id") or "")
        relevant = (
            decision.get("action_type") in sealed_activation._CREATE_SPECS
            or sealed_activation._is_activation(decision)
        )
        if not task_id or not relevant:
            return result
        transition = advance(self, task_id)
        if isinstance(result, dict) and transition.get("applied"):
            result = dict(result)
            result["activation_transition"] = transition
            result["activation"] = sealed_activation._task_activation_summary(self, task_id)
            result["task"] = self.get_task(task_id)
        return result

    sealed_activation._advance_activation = advance
    Store.mark_execution = mark_execution
    _INSTALLED = True
