from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .strategy_v4_policy import StrategyPolicy

DESTRUCTIVE = re.compile(r"(?:^|[-_.])(delete|archive|remove|purge|terminate)(?:$|[-_.])", re.I)
ALLOWED_ACTIONS = {
    "create_campaign", "create_ad_group", "create_ad", "create_target", "create_keyword",
    "create_negative", "create_negative_keyword", "create_negative_target",
    "pause", "enable", "resume", "disable", "update_state",
}
ALLOWED_FAMILIES = {"campaign", "ad_group", "ad", "target"}


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def walk(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key), item
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


def norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def values(value: Any, *names: str) -> list[Any]:
    wanted = {norm(name) for name in names}
    return [item for key, item in walk(value) if norm(key) in wanted]


def first(value: Any, *names: str) -> Any:
    found = values(value, *names)
    return found[0] if found else None


def number(value: Any, *names: str) -> float | None:
    for item in values(value, *names):
        try:
            result = float(item)
        except (TypeError, ValueError):
            continue
        if result >= 0:
            return result
    return None


def envelope_hash(profile_id: str, policy: StrategyPolicy) -> str:
    return digest({
        "version": 1, "profile_id": profile_id, "scope": "sealed-sp",
        "namespace": policy.sealed_sp_namespace,
        "max_campaign_budget": str(policy.sealed_sp_max_campaign_budget),
        "max_new_budget_per_day": str(policy.sealed_sp_max_new_budget_per_day),
        "max_campaign_creates_per_day": policy.sealed_sp_max_campaign_creates_per_day,
        "allow_all_observed_asins": policy.sealed_sp_allow_all_observed_asins,
    })


def policy_for(store: Any, profile_id: str) -> StrategyPolicy:
    merged = dict(store.get_settings())
    profile = store.get_profile(profile_id) or {}
    if isinstance(profile.get("strategy"), dict):
        merged.update(profile["strategy"])
    return StrategyPolicy.from_mapping(merged)


def marker(decision: dict[str, Any]) -> dict[str, Any] | None:
    payload = decision.get("payload") if isinstance(decision.get("payload"), dict) else {}
    value = payload.get("standing_authorization")
    return value if isinstance(value, dict) else None


def marker_shape_valid(decision: dict[str, Any]) -> bool:
    item = marker(decision)
    return bool(item and item.get("validated") is True and item.get("scope") == "sealed-sp"
                and item.get("ad_product") == "SPONSORED_PRODUCTS" and item.get("envelope_hash"))


def standing_authorized(store: Any, decision: dict[str, Any], tool: dict[str, Any] | None) -> tuple[bool, str]:
    item = marker(decision)
    if not item:
        return False, "decision has no standing authorization"
    profile_id = str(decision.get("profile_id") or "")
    profile = store.get_profile(profile_id)
    policy = policy_for(store, profile_id)
    if not profile or not profile.get("enabled"):
        return False, "standing authorization requires an enabled Profile"
    if not policy.sealed_sp_autonomy_enabled:
        return False, "sealed Sponsored Products autonomy is disabled"
    if item.get("validated") is not True or str(item.get("profile_id") or "") != profile_id:
        return False, "standing authorization identity is invalid"
    if item.get("ad_product") != "SPONSORED_PRODUCTS" or item.get("envelope_hash") != envelope_hash(profile_id, policy):
        return False, "standing authorization scope changed after planning"
    action = str(decision.get("action_type") or "").lower()
    payload = decision.get("payload") if isinstance(decision.get("payload"), dict) else {}
    native = str((tool or {}).get("native_name") or "")
    family = str((tool or {}).get("family") or decision.get("expected_family") or "")
    args = payload.get("approved_args") if isinstance(payload.get("approved_args"), dict) else payload
    if DESTRUCTIVE.search(native) or DESTRUCTIVE.search(action) or DESTRUCTIVE.search(canonical(args)):
        return False, "delete/archive/remove operations remain permanently blocked"
    if family not in ALLOWED_FAMILIES or action not in ALLOWED_ACTIONS:
        return False, "operation is outside the sealed SP envelope"
    products = {str(x).upper() for x in values(args, "adProduct", "ad_product", "advertisingType") if x not in (None, "")}
    products.add(str(payload.get("ad_product") or item.get("ad_product") or "").upper())
    if products - {"", "SP", "SPONSORED_PRODUCTS", "SPONSOREDPRODUCTS"}:
        return False, "standing authorization is Sponsored Products only"
    state = str(first(args, "state", "status") or payload.get("after") or item.get("desired_state") or "").upper()
    if action == "create_campaign":
        name, budget = str(first(args, "name", "campaignName") or ""), number(args, "budget", "dailyBudget", "budgetAmount")
        if not name.startswith(policy.sealed_sp_namespace + "-") or state != "PAUSED":
            return False, "autonomous Campaign must use the namespace and be created PAUSED"
        if budget is None or budget <= 0 or budget > float(policy.sealed_sp_max_campaign_budget):
            return False, "autonomous Campaign budget exceeds the envelope"
    elif action in {"pause", "disable", "enable", "resume", "update_state"}:
        if state not in {"PAUSED", "ENABLED"}:
            return False, "only PAUSED and ENABLED transitions are allowed"
        if state == "ENABLED" and item.get("verified_create") is not True and item.get("purpose") != "verified_recovery":
            return False, "ENABLED requires verified creation or recovery"
    elif action == "create_ad":
        asin = str(first(args, "asin", "advertisedAsin", "advertised_asin") or item.get("asin") or "").upper()
        if not asin or item.get("observed_in_ads") is not True:
            return False, "Product Ad requires an ASIN observed in trusted Ads data"
        if not policy.sealed_sp_allow_all_observed_asins and asin not in set(item.get("authorized_asins") or []):
            return False, "ASIN is outside the standing authorization"
    return True, "within sealed Sponsored Products standing authorization"
