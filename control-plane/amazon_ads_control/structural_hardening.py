from __future__ import annotations

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


def _expected_create_action(tool: dict[str, Any]) -> str | None:
    native = str(tool.get("native_name") or "").lower().replace("-", "_")
    if "create" not in native and "add" not in native:
        return None
    family = str(tool.get("family") or "")
    if family == "target":
        return "create_keyword" if "keyword" in native else "create_target"
    return _CREATE_BY_FAMILY.get(family)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from .service import ControlService

    original_create_plan = ControlService.create_managed_plan

    def create_managed_plan(self, payload: dict[str, Any], actor: str = "hermes-main"):
        actions = payload.get("actions") if isinstance(payload.get("actions"), list) else []
        if not actions:
            return original_create_plan(self, payload, actor)
        seen: set[str] = set()
        for index, action in enumerate(actions):
            if not isinstance(action, dict):
                continue
            plan_key = str(action.get("plan_key") or "").strip()
            if not plan_key:
                raise ValueError(f"actions[{index}].plan_key is required for a managed structural plan")
            if plan_key in seen:
                raise ValueError("managed structural plan_key values must be unique")
            seen.add(plan_key)
            tool_name = str(action.get("tool_name") or "").strip()
            tool = self.store.get_tool(tool_name)
            if not tool:
                continue
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
                if entity_id and not entity_id.startswith("planned:"):
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
        return original_create_plan(self, payload, actor)

    ControlService.create_managed_plan = create_managed_plan
    _INSTALLED = True
