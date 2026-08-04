from __future__ import annotations

import json
import re
from typing import Any

from .approval_gate import _digest

_INSTALLED = False
_PLACEHOLDER = re.compile(r"^\{\{decision:([A-Za-z0-9_.:-]{1,240})\.entity_id\}\}$")
_CREATE_ACTIONS = {
    "create_campaign": ("campaignid", "campaign_id"),
    "create_ad_group": ("adgroupid", "ad_group_id"),
    "create_target": ("targetid", "target_id"),
    "create_keyword": ("keywordid", "keyword_id"),
    "create_ad": ("adid", "ad_id"),
    "create_portfolio": ("portfolioid", "portfolio_id"),
}


def _walk(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key), item
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _refs(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, str):
        match = _PLACEHOLDER.fullmatch(value)
        if match:
            found.add(match.group(1))
    elif isinstance(value, dict):
        for item in value.values():
            found.update(_refs(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_refs(item))
    return found


def _decision_by_reference(store, task_id: str, reference: str):
    direct = store.get_decision(reference)
    if direct and str(direct.get("task_id") or "") == task_id:
        return direct
    matches = [
        item for item in store.list_decisions(task_id=task_id, limit=500)
        if str(item.get("plan_key") or "") == reference
    ]
    return matches[0] if len(matches) == 1 else None


def _render(store, task_id: str, value: Any) -> Any:
    if isinstance(value, str):
        match = _PLACEHOLDER.fullmatch(value)
        if not match:
            return value
        dependency = _decision_by_reference(store, task_id, match.group(1))
        if not dependency:
            raise ValueError(f"structural placeholder dependency {match.group(1)} was not found uniquely")
        if dependency.get("status") not in {"executed", "verified"}:
            raise ValueError(f"structural placeholder dependency {match.group(1)} is not confirmed")
        resolved = str(dependency.get("entity_id") or "")
        if not resolved or resolved.startswith("planned:"):
            raise ValueError(f"structural placeholder dependency {match.group(1)} has no resolved Amazon entity ID")
        return resolved
    if isinstance(value, dict):
        return {key: _render(store, task_id, item) for key, item in value.items()}
    if isinstance(value, list):
        return [_render(store, task_id, item) for item in value]
    return value


def _extract_created_id(action_type: str, result: Any) -> str | None:
    aliases = {item.replace("_", "").lower() for item in _CREATE_ACTIONS.get(action_type, ())}
    if not aliases:
        return None
    candidates: set[str] = set()
    for key, value in _walk(result):
        normalized = key.replace("_", "").replace("-", "").lower()
        if normalized in aliases and isinstance(value, (str, int)) and str(value).strip():
            candidates.add(str(value).strip())
    return next(iter(candidates)) if len(candidates) == 1 else None


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from . import approval_gate
    from .db import Store
    from .service import ControlService, _family_matches

    original_decision_dict = Store._decision_dict
    original_mark_execution = Store.mark_execution
    original_match = ControlService._match_decision
    original_context = ControlService.context
    original_create_plan = ControlService.create_managed_plan

    def decision_dict(row):
        item = original_decision_dict(row)
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        resolved = str(payload.get("resolved_entity_id") or "")
        if resolved:
            item["logical_entity_id"] = item.get("entity_id")
            item["entity_id"] = resolved
        return item

    def decision_plan(decision: dict[str, Any]) -> dict[str, Any]:
        payload = decision.get("payload") if isinstance(decision.get("payload"), dict) else {}
        return {
            "decision_id": str(decision.get("id") or ""),
            "plan_key": str(decision.get("plan_key") or ""),
            "action_type": str(decision.get("action_type") or ""),
            "entity_type": str(decision.get("entity_type") or ""),
            "entity_id": str(decision.get("logical_entity_id") or decision.get("entity_id") or ""),
            "expected_family": str(decision.get("expected_family") or ""),
            "risk": str(decision.get("risk") or "critical"),
            "tool_name": str(payload.get("tool_name") or ""),
            "arguments": payload.get("approved_args") if isinstance(payload.get("approved_args"), dict) else {},
            "arguments_hash": str(payload.get("approved_args_hash") or ""),
            "expected_state": payload.get("expected_state") if isinstance(payload.get("expected_state"), dict) else {},
            "maximum_daily_budget": payload.get("maximum_daily_budget"),
            "depends_on": payload.get("depends_on") if isinstance(payload.get("depends_on"), list) else [],
        }

    def create_managed_plan(self, payload: dict[str, Any], actor: str = "hermes-main"):
        actions = payload.get("actions") if isinstance(payload.get("actions"), list) else []
        supplied_keys = {
            str(item.get("plan_key")): index
            for index, item in enumerate(actions)
            if isinstance(item, dict) and str(item.get("plan_key") or "").strip()
        }
        supplied_count = sum(
            1 for item in actions
            if isinstance(item, dict) and str(item.get("plan_key") or "").strip()
        )
        if len(supplied_keys) != supplied_count:
            raise ValueError("managed structural plan_key values must be unique")
        for index, action in enumerate(actions):
            if not isinstance(action, dict):
                continue
            refs = _refs(action.get("arguments"))
            dependencies = {
                str(item) for item in action.get("depends_on", [])
            } if isinstance(action.get("depends_on"), list) else set()
            if refs - dependencies:
                raise ValueError(f"actions[{index}] placeholders must also appear in depends_on")
            missing = dependencies - set(supplied_keys)
            if missing:
                raise ValueError(
                    f"actions[{index}] references unknown plan_key(s): {', '.join(sorted(missing))}"
                )
            for reference in dependencies:
                if supplied_keys[reference] >= index:
                    raise ValueError(
                        f"actions[{index}] dependency {reference} must precede the dependent action"
                    )
        return original_create_plan(self, payload, actor)

    def match(self, task_id: str, tool: dict[str, Any], args: dict[str, Any]):
        decision, reason = original_match(self, task_id, tool, args)
        if decision:
            return decision, reason
        matches = []
        for item in self.store.list_decisions(task_id=task_id, status="planned", limit=500):
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            if not _family_matches(item, str(tool.get("family") or "")):
                continue
            if str(payload.get("tool_name") or "") != str(tool.get("registered_name") or ""):
                continue
            template = payload.get("approved_args") if isinstance(payload.get("approved_args"), dict) else None
            if template is None:
                continue
            try:
                rendered = _render(self.store, task_id, template)
            except ValueError:
                continue
            if _digest(rendered) == _digest(args):
                matches.append(item)
        if len(matches) == 1:
            return matches[0], "matched exact approved structural template after deterministic ID resolution"
        if len(matches) > 1:
            return None, "write ambiguously matches multiple rendered structural decisions"
        return None, reason

    def mark_execution(self, *args, **kwargs):
        result = original_mark_execution(self, *args, **kwargs)
        decision_id = str(kwargs.get("decision_id") or (args[0] if args else ""))
        outcome = str(kwargs.get("outcome") or "")
        response = kwargs.get("result")
        decision = self.get_decision(decision_id) or {}
        logical = str(decision.get("logical_entity_id") or decision.get("entity_id") or "")
        action_type = str(decision.get("action_type") or "")
        if outcome == "success" and logical.startswith("planned:") and action_type in _CREATE_ACTIONS:
            resolved = _extract_created_id(action_type, response)
            if not resolved:
                with self.connection() as conn:
                    conn.execute(
                        "UPDATE decisions SET status='uncertain',failure=? WHERE id=?",
                        ("successful create response did not contain one unique entity ID", decision_id),
                    )
                self.alert(
                    "critical", "CREATED_ENTITY_ID_UNRESOLVED", decision.get("profile_id"),
                    decision.get("task_id"), decision_id,
                    "Amazon accepted a create operation but the exact created entity ID could not be bound; dependent writes are blocked",
                    {"action_type": action_type},
                )
                return self.get_decision(decision_id) or result
            with self.connection() as conn:
                row = conn.execute("SELECT payload_json FROM decisions WHERE id=?", (decision_id,)).fetchone()
                payload = json.loads(row["payload_json"] or "{}") if row else {}
                payload["resolved_entity_id"] = resolved
                conn.execute(
                    "UPDATE decisions SET payload_json=? WHERE id=?",
                    (json.dumps(payload, ensure_ascii=False), decision_id),
                )
            self.event(
                "info", "decision.entity_bound", "controller", decision.get("task_id"),
                f"Bound created {action_type} to Amazon entity {resolved}",
                {"decision_id": decision_id, "resolved_entity_id": resolved},
            )
            return self.get_decision(decision_id) or result
        return result

    def context(self, session_id):
        result = original_context(self, session_id)
        worker = self.store.worker_for_session(session_id)
        if worker and worker.get("role") == "executor":
            for decision in result.get("decisions", []):
                payload = decision.get("payload") if isinstance(decision.get("payload"), dict) else {}
                template = payload.get("approved_args") if isinstance(payload.get("approved_args"), dict) else None
                if template is not None and decision.get("status") == "planned":
                    try:
                        decision["rendered_arguments"] = _render(self.store, worker["task_id"], template)
                    except ValueError as exc:
                        decision["rendering_blocked"] = str(exc)
        return result

    Store._decision_dict = staticmethod(decision_dict)
    Store.mark_execution = mark_execution
    approval_gate._decision_plan = decision_plan
    ControlService.create_managed_plan = create_managed_plan
    ControlService._match_decision = match
    ControlService.context = context
    _INSTALLED = True
