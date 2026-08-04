from __future__ import annotations

import hmac
from typing import Any

from .outcome import parse_tool_outcome
from .service import ControlService

_EXECUTION_RECORDED = {
    "executed", "pending", "uncertain", "failed", "verified", "mismatch",
}


def install() -> None:
    """Make durable post-tool result delivery idempotent.

    The Hermes plugin may resend the original result when the HTTP response was
    lost after the controller committed it. The underlying Amazon mutation is
    never replayed; this adapter only recognizes the same callback by its event
    ID, reservation token and parsed outcome.
    """
    current = ControlService.finish_tool
    if getattr(current, "_ads_result_replay_safe", False):
        return

    def finish_tool(self: ControlService, payload: dict[str, Any]) -> dict[str, Any]:
        event_id = str(payload.get("event_id") or "").strip()
        decision_id = str(payload.get("decision_id") or "").strip()
        token = str(payload.get("reservation_token") or "").strip()
        if event_id and decision_id and token:
            decision = self.store.get_decision(decision_id)
            tool = self.store.get_tool(str(payload.get("tool_name") or ""))
            if decision and tool and str(tool.get("semantic") or "") == "write":
                stored_token = str(decision.get("reservation_token") or "")
                if stored_token and not hmac.compare_digest(stored_token, token):
                    raise ValueError("reservation token mismatch for replayed result")
                if decision.get("status") in _EXECUTION_RECORDED:
                    outcome = parse_tool_outcome(payload.get("result"), operation="write")
                    stored_outcome = str(decision.get("execution_outcome") or "")
                    if stored_outcome and stored_outcome != outcome.status:
                        raise ValueError("replayed result conflicts with recorded outcome")
                    return {
                        "recorded": True,
                        "duplicate": True,
                        "event_id": event_id,
                        "action_id": None,
                        "outcome": outcome.__dict__,
                    }
        return current(self, payload)

    finish_tool._ads_result_replay_safe = True  # type: ignore[attr-defined]
    ControlService.finish_tool = finish_tool
