from __future__ import annotations

from copy import deepcopy
import math
import re
from typing import Any

_INSTALLED = False
_CREATE_BY_FAMILY = {
    "campaign": "create_campaign",
    "ad_group": "create_ad_group",
    "ad": "create_ad",
    "portfolio": "create_portfolio",
}
_SUPPORTED_CREATE_ACTIONS = {
    "create_campaign", "create_ad_group", "create_target", "create_keyword",
    "create_ad", "create_portfolio",
}
_BLACK_BOX_TOKENS = {
    "composite", "workflow", "bulk", "batch", "multi_step", "multistep",
    "expand", "locale_expansion", "copy_campaign", "clone_campaign",
}
_FORBIDDEN_ARGUMENT_KEYS = {
    "authorization", "proxyauthorization", "apikey", "clientsecret",
    "accesstoken", "refreshtoken", "password", "passwd", "credential",
    "credentials", "secret", "bearertoken",
}


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _expected_create_action(tool: dict[str, Any]) -> str | None:
    native = str(tool.get("native_name") or "").lower().replace("-", "_")
    if "create" not in native and "add" not in native:
        return None
    family = str(tool.get("family") or "")
    if family == "target":
        return "create_keyword" if "keyword" in native else "create_target"
    return _CREATE_BY_FAMILY.get(family)


def _validate_no_credentials(value: Any, path: str = "arguments") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = _normalized(str(key))
            if normalized in _FORBIDDEN_ARGUMENT_KEYS or normalized.endswith("token"):
                raise ValueError(f"forbidden credential field at {path}.{key}")
            _validate_no_credentials(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validate_no_credentials(item, f"{path}[{index}]")


def _contains_exact_scalar(value: Any, expected: str) -> bool:
    if isinstance(value, dict):
        return any(_contains_exact_scalar(item, expected) for item in value.values())
    if isinstance(value, list):
        return any(_contains_exact_scalar(item, expected) for item in value)
    return isinstance(value, (str, int)) and str(value) == expected


def _budget_exposure(value: Any) -> float | None:
    values: list[float] = []

    def visit(item: Any, key: str = "") -> None:
        normalized = _normalized(key)
        if (
            "budget" in normalized
            and isinstance(item, (int, float))
            and not isinstance(item, bool)
        ):
            numeric = float(item)
            if math.isfinite(numeric) and numeric >= 0:
                values.append(numeric)
            return
        if isinstance(item, dict):
            for child_key, child in item.items():
                visit(child, str(child_key))
        elif isinstance(item, list):
            for child in item:
                visit(child, key)

    visit(value)
    return max(values) if values else None


def _is_black_box(tool: dict[str, Any]) -> bool:
    native = str(tool.get("native_name") or "").lower().replace("-", "_")
    return any(token in native for token in _BLACK_BOX_TOKENS)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from . import approval_gate
    from .service import ControlService

    original_create_plan = ControlService.create_managed_plan

    def create_managed_plan(self, payload: dict[str, Any], actor: str = "hermes-main"):
        prepared = deepcopy(payload)
        actions = prepared.get("actions") if isinstance(prepared.get("actions"), list) else []
        if not actions:
            return original_create_plan(self, prepared, actor)
        seen: set[str] = set()
        for index, action in enumerate(actions):
            if not isinstance(action, dict):
                raise ValueError(f"actions[{index}] must be an object")
            plan_key = str(action.get("plan_key") or "").strip()
            if not plan_key:
                raise ValueError(f"actions[{index}].plan_key is required for a managed structural plan")
            if plan_key in seen:
                raise ValueError("managed structural plan_key values must be unique")
            seen.add(plan_key)

            tool_name = str(action.get("tool_name") or "").strip()
            tool = self.store.get_tool(tool_name)
            if not tool:
                raise ValueError(f"actions[{index}] references a tool absent from the live catalog")
            permanent_reason = approval_gate._permanent_block(tool)
            if permanent_reason:
                raise ValueError(permanent_reason)
            if _is_black_box(tool):
                raise ValueError(
                    "black-box expansion/composite workflow must be decomposed into approved atomic actions"
                )

            arguments = action.get("arguments") if isinstance(action.get("arguments"), dict) else {}
            _validate_no_credentials(arguments)
            action_type = str(action.get("action_type") or "").strip()
            expected_create = _expected_create_action(tool)
            entity_id = str(action.get("entity_id") or "").strip()
            if expected_create:
                if expected_create not in _SUPPORTED_CREATE_ACTIONS:
                    raise ValueError(f"actions[{index}] uses an unsupported create family")
                if action_type != expected_create:
                    raise ValueError(
                        f"actions[{index}].action_type must be {expected_create!r} for {tool_name}"
                    )
                if not entity_id or not entity_id.startswith("planned:"):
                    raise ValueError(
                        f"actions[{index}].entity_id must be a planned logical ID for a create operation"
                    )
            else:
                if not entity_id:
                    raise ValueError(
                        f"actions[{index}].entity_id is required for a non-create structural operation"
                    )
                if entity_id.startswith("planned:"):
                    raise ValueError(
                        f"actions[{index}].entity_id must identify the existing Amazon entity"
                    )
                if not _contains_exact_scalar(arguments, entity_id):
                    raise ValueError(
                        f"actions[{index}].entity_id is not present in the exact write arguments"
                    )

            derived_budget = _budget_exposure(arguments)
            declared_budget = action.get("maximum_daily_budget")
            if declared_budget is not None:
                if isinstance(declared_budget, bool) or not isinstance(declared_budget, (int, float)):
                    raise ValueError(f"actions[{index}].maximum_daily_budget must be numeric")
                declared = float(declared_budget)
                if not math.isfinite(declared) or declared < 0:
                    raise ValueError(f"actions[{index}].maximum_daily_budget must be finite and non-negative")
                if derived_budget is not None and declared + 1e-9 < derived_budget:
                    raise ValueError(
                        f"actions[{index}].maximum_daily_budget understates the exact argument exposure"
                    )
            elif derived_budget is not None:
                action["maximum_daily_budget"] = derived_budget

        return original_create_plan(self, prepared, actor)

    ControlService.create_managed_plan = create_managed_plan
    _INSTALLED = True
