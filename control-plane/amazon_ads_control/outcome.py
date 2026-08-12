from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

_FAILURE_STATES = {"error", "failed", "failure", "cancelled", "canceled", "rejected", "invalid", "timeout"}
_SUCCESS_STATES = {"success", "succeeded", "completed", "complete", "ok", "accepted", "done"}
_PENDING_STATES = {"pending", "in_progress", "in-progress", "processing", "queued", "submitted"}


@dataclass(frozen=True)
class Outcome:
    status: str  # success | partial | failure | pending | unknown
    summary: str
    success_count: int = 0
    error_count: int = 0
    structured: bool = False
    payload: Any = None

    @property
    def terminal_success(self) -> bool:
        return self.status == "success"


def _as_payload(result: Any) -> tuple[Any, bool]:
    if isinstance(result, (dict, list)):
        return result, True
    if isinstance(result, str):
        text = result.strip()
        if not text:
            return "", False
        try:
            return json.loads(text), True
        except json.JSONDecodeError:
            return text, False
    return result, False


def _count_collection(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return len(value)
    return 1


def parse_tool_outcome(result: Any, *, operation: str = "unknown") -> Outcome:
    payload, structured = _as_payload(result)
    if not structured:
        text = str(payload)
        return Outcome("unknown", text[:1000] or "unstructured empty response", structured=False, payload=payload)

    if isinstance(payload, list):
        errors = 0
        explicit_successes = 0
        unclassified = 0
        for item in payload:
            if not isinstance(item, dict):
                unclassified += 1
                continue
            item_status = str(item.get("status") or item.get("state") or "").strip().lower()
            if item.get("error") or item.get("success") is False or item_status in _FAILURE_STATES:
                errors += 1
            elif item.get("success") is True or item_status in _SUCCESS_STATES:
                explicit_successes += 1
            else:
                unclassified += 1
        if operation == "write" and unclassified:
            if errors or explicit_successes:
                return Outcome("partial", f"write list is not fully classified: {explicit_successes} explicit success, {errors} error, {unclassified} unknown", explicit_successes, errors, True, payload)
            return Outcome("unknown", "write list has no explicit success/failure signal", 0, 0, True, payload)
        successes = explicit_successes + unclassified
        status = "partial" if errors and successes else "failure" if errors else "success"
        return Outcome(status, f"list result: {successes} success, {errors} error", successes, errors, True, payload)

    if not isinstance(payload, dict):
        return Outcome("unknown", f"structured scalar response: {payload!r}", structured=True, payload=payload)

    status_value = str(payload.get("status") or payload.get("state") or "").strip().lower()
    explicit_success = payload.get("success")
    explicit_error = payload.get("error")
    errors_value = payload.get("errors")
    if errors_value is None and isinstance(explicit_error, list):
        errors_value = explicit_error
    successes_value = payload.get("successes")
    if successes_value is None and isinstance(explicit_success, list):
        successes_value = explicit_success

    # Amazon bulk responses commonly use {success: [...], error: [...]}.
    success_count = _count_collection(successes_value) if successes_value is not None else 0
    error_count = _count_collection(errors_value) if errors_value is not None else 0
    try:
        success_count = max(success_count, int(payload.get("successCount") or payload.get("success_count") or 0))
        error_count = max(error_count, int(payload.get("errorCount") or payload.get("error_count") or 0))
    except (TypeError, ValueError):
        pass
    if isinstance(explicit_error, (str, dict)) and explicit_error:
        error_count = max(error_count, 1)
    if explicit_success is True:
        success_count = max(success_count, 1)
    if explicit_success is False:
        error_count = max(error_count, 1)

    if status_value in _PENDING_STATES:
        return Outcome("pending", f"asynchronous operation is {status_value}", success_count, error_count, True, payload)
    if status_value in _FAILURE_STATES:
        return Outcome("failure", f"operation status is {status_value}", success_count, max(1, error_count), True, payload)
    if status_value in _SUCCESS_STATES and not error_count:
        return Outcome("success", f"operation status is {status_value}", max(1, success_count), 0, True, payload)
    if error_count and success_count:
        return Outcome("partial", f"bulk result: {success_count} success, {error_count} error", success_count, error_count, True, payload)
    if error_count:
        return Outcome("failure", f"result contains {error_count} error item(s)", success_count, error_count, True, payload)
    if success_count:
        return Outcome("success", f"result contains {success_count} successful item(s)", success_count, 0, True, payload)

    # Resource identifiers prove that a read/job produced a resource, but they do not
    # prove that a state-changing write was accepted or applied. Writes require an
    # explicit status/success signal and are independently verified afterwards.
    if any(key in payload for key in ("campaignId", "adGroupId", "adId", "targetId", "reportId", "exportId", "subscriptionId")):
        if operation in {"read", "job"}:
            return Outcome("success", "response contains a retrieved/created resource identifier", 1, 0, True, payload)
        return Outcome("unknown", "resource identifier alone does not confirm a write", 0, 0, True, payload)

    # A 200-like JSON object without explicit outcome is not enough to commit a write.
    return Outcome("unknown", "structured response has no explicit success/failure signal", 0, 0, True, payload)
