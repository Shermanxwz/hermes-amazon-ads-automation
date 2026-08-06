from __future__ import annotations

import json
from typing import Any

from .schema_validation import validate_instance
from .sealed_envelope import envelope_hash, first, number, policy_for, standing_authorized

# Object identity is intentionally used here. External JSON, Hermes tool calls and
# persisted payloads cannot manufacture this marker. Only controller code in the
# current process may mint a verified-create activation action.
INTERNAL_VERIFIED_CREATE = object()
_ADVERTISED_ASIN_KEYS = {"advertisedasin", "advertisedproductasin", "productadasin"}


def _norm(value: str) -> str:
    return "".join(character for character in str(value).lower() if character.isalnum())


def _advertised_asins(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if _norm(key) in _ADVERTISED_ASIN_KEYS and isinstance(item, str) and item.strip():
                found.add(item.strip().upper())
            if isinstance(item, (dict, list)):
                found.update(_advertised_asins(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_advertised_asins(item))
    return found


def trusted_observed_asins(store: Any, profile_id: str, limit: int = 5000) -> set[str]:
    """Return advertised ASINs from controller-ingested metric rows only.

    Caller-declared flags and arbitrary target-expression ASINs are deliberately
    ignored. Product Ad creation authority must originate in normalized Amazon
    Ads evidence already stored for the same Profile.
    """
    with store.connection() as conn:
        rows = conn.execute(
            "SELECT m.row_json FROM metric_rows m JOIN cycles c ON c.id=m.cycle_id "
            "WHERE m.profile_id=? AND c.source<>'amazon-ads-mcp-live-managed-plan' "
            "ORDER BY m.id DESC LIMIT ?",
            (profile_id, max(1, min(50000, int(limit)))),
        ).fetchall()
    found: set[str] = set()
    for row in rows:
        try:
            value = json.loads(row["row_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        found.update(_advertised_asins(value))
    return found


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
    observed_asins = trusted_observed_asins(service.store, profile_id)
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
        asin = str(first(args, "asin", "advertisedAsin", "advertised_asin") or "").upper()
        internal_verified_create = raw.get("_internal_verified_create") is INTERNAL_VERIFIED_CREATE
        marker = {
            "version": 1,
            "validated": True,
            "scope": "sealed-sp",
            "profile_id": profile_id,
            "ad_product": "SPONSORED_PRODUCTS",
            "observed_in_ads": bool(asin and asin in observed_asins),
            "verified_create": internal_verified_create,
            # Managed-plan callers may describe intent, but cannot self-assert a
            # verified recovery/create purpose. Those authorities are minted by
            # controller strategy or activation state machines only.
            "purpose": "verified_create" if internal_verified_create else "structural_maintenance",
            "desired_state": str(first(args, "state", "status") or "").upper(),
            "asin": asin,
            "authorized_asins": sorted(observed_asins) if policy.sealed_sp_allow_all_observed_asins else [],
            "envelope_hash": envelope_hash(profile_id, policy),
        }
        candidate = {
            "profile_id": profile_id,
            "action_type": action,
            "expected_family": str(tool.get("family") or ""),
            "payload": {
                "approved_args": args,
                "ad_product": "SPONSORED_PRODUCTS",
                "standing_authorization": marker,
            },
        }
        allowed, reason = standing_authorized(service.store, candidate, tool)
        if not allowed:
            raise ValueError(f"actions[{index}]: {reason}")
        copy = dict(raw)
        copy.pop("_internal_verified_create", None)
        copy["standing_marker"] = marker
        validated.append(copy)
    if creates > policy.sealed_sp_max_campaign_creates_per_day:
        raise ValueError("standing plan exceeds the Campaign creation limit")
    if total_budget > float(policy.sealed_sp_max_new_budget_per_day):
        raise ValueError("standing plan exceeds the new-Campaign budget envelope")
    return validated
