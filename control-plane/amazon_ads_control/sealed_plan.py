from __future__ import annotations

from typing import Any

from .schema_validation import validate_instance
from .sealed_envelope import envelope_hash, first, number, policy_for, standing_authorized


def validate_standing_plan(service: Any, payload: dict[str, Any]):
    profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
    profile_id = str(profile.get("profile_id") or profile.get("id") or "").strip()
    existing = service.store.get_profile(profile_id) if profile_id else None
    if not existing or not existing.get("enabled"):
        raise ValueError("standing authorization requires a registered enabled Profile")
    policy = policy_for(service.store, profile_id)
    if not policy.sealed_sp_autonomy_enabled:
        raise ValueError("sealed Sponsored Products autonomy is disabled")
    actions = payload.get("actions")
    if not isinstance(actions, list) or not actions or len(actions) > 50:
        raise ValueError("actions must contain 1-50 atomic operations")
    creates, total_budget, validated = 0, 0.0, []
    for index, raw in enumerate(actions):
        if not isinstance(raw, dict):
            raise ValueError(f"actions[{index}] must be an object")
        tool = service.store.get_tool(str(raw.get("tool_name") or ""))
        args = raw.get("arguments") if isinstance(raw.get("arguments"), dict) else None
        if not tool or not tool.get("enabled") or tool.get("semantic") != "write" or args is None:
            raise ValueError(f"actions[{index}] requires an enabled live catalog write tool and arguments")
        errors = validate_instance(args, tool.get("schema") if isinstance(tool.get("schema"), dict) else {})
        if errors:
            raise ValueError(f"actions[{index}] violates live MCP schema: {'; '.join(errors[:5])}")
        action = str(raw.get("action_type") or "").lower()
        if action == "create_campaign":
            creates += 1
            total_budget += number(args, "budget", "dailyBudget", "budgetAmount") or 0.0
        marker = {
            "version": 1, "validated": True, "scope": "sealed-sp", "profile_id": profile_id,
            "ad_product": "SPONSORED_PRODUCTS", "observed_in_ads": bool(raw.get("observed_in_ads", True)),
            "verified_create": bool(raw.get("verified_create", False)),
            "purpose": str(raw.get("purpose") or "structural_maintenance"),
            "desired_state": str(first(args, "state", "status") or "").upper(),
            "asin": str(first(args, "asin", "advertisedAsin", "advertised_asin") or "").upper(),
            "authorized_asins": raw.get("authorized_asins") if isinstance(raw.get("authorized_asins"), list) else [],
            "envelope_hash": envelope_hash(profile_id, policy),
        }
        candidate = {"profile_id": profile_id, "action_type": action, "expected_family": str(tool.get("family") or ""),
                     "payload": {"approved_args": args, "ad_product": "SPONSORED_PRODUCTS", "standing_authorization": marker}}
        allowed, reason = standing_authorized(service.store, candidate, tool)
        if not allowed:
            raise ValueError(f"actions[{index}]: {reason}")
        copy = dict(raw)
        copy["standing_marker"] = marker
        validated.append(copy)
    if creates > policy.sealed_sp_max_campaign_creates_per_day:
        raise ValueError("standing plan exceeds the Campaign creation limit")
    if total_budget > float(policy.sealed_sp_max_new_budget_per_day):
        raise ValueError("standing plan exceeds the new-Campaign budget envelope")
    return validated
