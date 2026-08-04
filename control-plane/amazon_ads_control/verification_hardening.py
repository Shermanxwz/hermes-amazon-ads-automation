from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

_INSTALLED = False
UTC = timezone.utc

_ENTITY_ID_KEYS = {
    "campaign": {"campaignid"},
    "adgroup": {"adgroupid"},
    "target": {"targetid", "targetingclauseid", "producttargetingid", "keywordid"},
    "keyword": {"keywordid", "targetid"},
    "ad": {"adid", "productadid"},
    "productad": {"adid", "productadid"},
    "portfolio": {"portfolioid"},
    "budget": {"budgetid", "campaignbudgetid"},
    "profile": {"profileid", "advertisingprofileid", "advertiserprofileid"},
    "placement": {"placementid"},
    "searchterm": {"searchtermid", "queryid"},
}


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _identifier_keys(entity_type: str | None) -> set[str]:
    normalized = _key(str(entity_type or ""))
    aliases = {"id", "entityid"}
    aliases.update(_ENTITY_ID_KEYS.get(normalized, set()))
    if normalized:
        aliases.add(f"{normalized}id")
    return aliases


def _same_identifier(left: Any, right: str) -> bool:
    return isinstance(left, (str, int)) and str(left).strip() == right


def _entity_candidates(
    value: Any,
    entity_id: str,
    entity_type: str | None,
    path: str = "$",
) -> list[tuple[str, dict[str, Any]]]:
    candidates: list[tuple[str, dict[str, Any]]] = []
    aliases = _identifier_keys(entity_type)
    if isinstance(value, dict):
        direct_match = any(
            _key(str(key)) in aliases and _same_identifier(item, entity_id)
            for key, item in value.items()
        )
        if direct_match:
            candidates.append((path, value))
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                candidates.extend(
                    _entity_candidates(item, entity_id, entity_type, f"{path}.{key}")
                )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, (dict, list)):
                candidates.extend(
                    _entity_candidates(item, entity_id, entity_type, f"{path}[{index}]")
                )
    return candidates


def select_entity_scope(
    actual: Any,
    entity_id: str,
    entity_type: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Return the one entity object whose direct identifier matches the plan.

    Verification must never combine an identifier from one response object with
    expected fields from another. Ambiguous or identifier-free evidence fails
    closed and requires a new, more specific read.
    """
    wanted = str(entity_id or "").strip()
    if not wanted:
        raise ValueError("planned decision has no entity_id for scoped verification")
    candidates = _entity_candidates(actual, wanted, entity_type)
    if not candidates:
        raise ValueError("read evidence does not contain the planned entity as an identifiable object")
    if len(candidates) != 1:
        paths = ", ".join(path for path, _item in candidates[:5])
        raise ValueError(
            "read evidence contains multiple objects for the planned entity; "
            f"verification is ambiguous ({paths})"
        )
    path, entity = candidates[0]
    return entity, path


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from .service import ControlService, _expected_differences, _family_matches

    def verify_decision(self, payload: dict[str, Any]) -> dict[str, Any]:
        decision_id = str(payload.get("decision_id") or "").strip()
        session_id = str(payload.get("session_id") or "").strip()
        try:
            evidence_action_id = int(payload.get("evidence_action_id"))
        except (TypeError, ValueError) as exc:
            raise ValueError("evidence_action_id is required") from exc
        if not decision_id or not session_id:
            raise ValueError("decision_id and session_id are required")
        worker = self.store.worker_for_session(session_id)
        if not worker or worker.get("role") != "verifier":
            raise ValueError("only a bound verifier may verify a decision")
        task = self.store.get_task(worker["task_id"])
        if not task or task.get("verifier_session_id") != session_id:
            raise ValueError("session is not the task's current verifier")
        decision = self.store.get_decision(decision_id)
        if not decision or decision.get("task_id") != worker.get("task_id"):
            raise ValueError("decision does not belong to verifier task")
        action = self.store.get_action(evidence_action_id)
        if not action:
            raise ValueError("read evidence action was not found")
        if (
            action.get("session_id") != session_id
            or action.get("task_id") != worker.get("task_id")
            or action.get("phase") != "after"
            or action.get("operation") != "read"
            or not action.get("allowed")
            or action.get("structured_result") is not True
            or not isinstance(action.get("result"), (dict, list))
        ):
            raise ValueError("evidence must be a structured cataloged read from the current verifier")
        tool = self.store.get_tool(str(action.get("tool_name") or ""))
        if not tool or not _family_matches(decision, str(tool.get("family") or "")):
            raise ValueError("read evidence tool family does not match the decision")
        try:
            read_at = datetime.fromisoformat(str(action.get("created_at") or ""))
            executed_at = datetime.fromisoformat(
                str(decision.get("executed_at") or decision.get("reserved_at") or "")
            )
            if read_at.tzinfo is None:
                read_at = read_at.replace(tzinfo=UTC)
            if executed_at.tzinfo is None:
                executed_at = executed_at.replace(tzinfo=UTC)
        except ValueError as exc:
            raise ValueError("verification timestamps are invalid") from exc
        if read_at < executed_at:
            raise ValueError("read evidence predates the write attempt")
        max_age = int(self.store.get_settings().get("read_evidence_max_age_seconds", 600))
        if (datetime.now(UTC) - read_at.astimezone(UTC)).total_seconds() > max_age:
            raise ValueError("read evidence is too old")

        actual, evidence_path = select_entity_scope(
            action["result"],
            str(decision.get("entity_id") or ""),
            str(decision.get("entity_type") or ""),
        )
        decision_payload = (
            decision.get("payload") if isinstance(decision.get("payload"), dict) else {}
        )
        expected = decision_payload.get("expected_state")
        if not isinstance(expected, dict) or not expected:
            field = str(decision_payload.get("field") or "")
            expected = {field: decision_payload.get("after")} if field else {}
        differences = _expected_differences(expected, actual)
        status = "verified" if expected and not differences else "mismatch"
        default_message = (
            f"state matches at {evidence_path}"
            if status == "verified"
            else f"state at {evidence_path} does not match expected write"
        )
        return self.store.record_verification(
            decision_id=decision_id,
            task_id=worker["task_id"],
            verifier_session_id=session_id,
            evidence_action_id=evidence_action_id,
            expected=expected,
            actual=actual,
            differences=differences,
            status=status,
            message=str(payload.get("message") or default_message),
        )

    ControlService.verify_decision = verify_decision
    _INSTALLED = True
