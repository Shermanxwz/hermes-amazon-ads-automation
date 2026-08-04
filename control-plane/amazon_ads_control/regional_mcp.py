from __future__ import annotations

from collections import Counter
from typing import Any

_INSTALLED = False

_REGION_COUNTRIES = {
    "na": {
        "US", "CA", "MX", "BR",
    },
    "fe": {
        "AU", "NZ", "JP", "IN", "CN", "SG",
    },
    "eu": {
        "GB", "UK", "DE", "FR", "IT", "ES", "NL", "SE", "PL", "TR",
        "BE", "CH", "AT", "IE", "DK", "FI", "NO", "LU", "AE", "SA",
        "IL", "EG", "MA", "BH", "KW", "QA", "ZA",
    },
}


def profile_region(profile: dict[str, Any] | None) -> str | None:
    if not profile:
        return None
    explicit = str(profile.get("region") or "").strip().lower()
    if explicit in _REGION_COUNTRIES:
        return explicit
    country = str(profile.get("country_code") or profile.get("marketplace") or "").strip().upper()
    country = country.rsplit(".", 1)[-1]
    for region, countries in _REGION_COUNTRIES.items():
        if country in countries:
            return region
    return None


def tool_region(tool: dict[str, Any] | None) -> str | None:
    source = str((tool or {}).get("source") or "")
    prefix = "hermes-registry:"
    if source.startswith(prefix):
        region = source[len(prefix):].strip().lower()
        if region in _REGION_COUNTRIES:
            return region
    return None


def _region_guard(store, decision: dict[str, Any], tool: dict[str, Any]) -> tuple[bool, str]:
    region = tool_region(tool)
    if not region:
        return True, "Amazon MCP region is not explicitly tagged"
    profile = store.get_profile(str(decision.get("profile_id") or ""))
    expected = profile_region(profile)
    if not expected:
        return False, "profile marketplace cannot be mapped to an Amazon Ads MCP region"
    if expected != region:
        return False, f"profile requires Amazon Ads MCP region {expected}, but tool belongs to {region}"
    return True, f"profile and Amazon Ads MCP tool both use region {region}"


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from .service import ControlService

    original_guardrail = ControlService._guardrail_check
    original_create_plan = ControlService.create_managed_plan
    original_context = ControlService.context

    def guardrail(self, decision, tool, settings):
        allowed, reason = original_guardrail(self, decision, tool, settings)
        if not allowed:
            return allowed, reason
        region_allowed, region_reason = _region_guard(self.store, decision, tool)
        if not region_allowed:
            return False, region_reason
        return True, f"{reason}; {region_reason}"

    def create_managed_plan(self, payload: dict[str, Any], actor: str = "hermes-main"):
        profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
        expected_region = profile_region(profile)
        actions = payload.get("actions") if isinstance(payload.get("actions"), list) else []
        for index, action in enumerate(actions):
            if not isinstance(action, dict):
                continue
            tool = self.store.get_tool(str(action.get("tool_name") or ""))
            region = tool_region(tool)
            if not region:
                continue
            if not expected_region:
                raise ValueError(
                    "managed structural plan profile requires a recognized country_code/marketplace for regional MCP routing"
                )
            if region != expected_region:
                raise ValueError(
                    f"actions[{index}] uses Amazon Ads MCP region {region}, but profile requires {expected_region}"
                )
        return original_create_plan(self, payload, actor)

    def context(self, session_id):
        result = original_context(self, session_id)
        tools = self.store.list_tools()
        regions = Counter(tool_region(item) or "untagged" for item in tools)
        profiles = {
            str(item.get("profile_id")): profile_region(item) or "unknown"
            for item in self.store.list_profiles()
        }
        result["regional_mcp"] = {
            "tool_counts": dict(sorted(regions.items())),
            "profile_regions": profiles,
            "write_policy": "profile region must exactly match the MCP tool region",
        }
        return result

    ControlService._guardrail_check = guardrail
    ControlService.create_managed_plan = create_managed_plan
    ControlService.context = context
    _INSTALLED = True
