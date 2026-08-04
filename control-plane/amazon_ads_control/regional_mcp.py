from __future__ import annotations

from collections import Counter
import re
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
_PROFILE_KEYS = {"profileid", "advertisingprofileid", "advertiserprofileid"}


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


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _profile_ids(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if _key(str(key)) in _PROFILE_KEYS and isinstance(item, (str, int)) and str(item).strip():
                found.add(str(item).strip())
            found.update(_profile_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_profile_ids(item))
    return found


def _expected_regions_for_task(store, task_id: str) -> tuple[set[str], str | None]:
    regions: set[str] = set()
    for decision in store.list_decisions(task_id=task_id, limit=500):
        profile = store.get_profile(str(decision.get("profile_id") or ""))
        region = profile_region(profile)
        if not region:
            return set(), "task contains a Profile whose marketplace cannot be mapped to NA/EU/FE"
        regions.add(region)
    if not regions:
        return set(), "task has no Profile-bound decisions"
    if len(regions) > 1:
        return set(), "one task cannot span multiple Amazon Ads MCP regions"
    return regions, None


def _expected_regions_for_args(store, args: dict[str, Any]) -> tuple[set[str], str | None]:
    identifiers = _profile_ids(args)
    if not identifiers:
        return set(), None
    regions: set[str] = set()
    for profile_id in identifiers:
        profile = store.get_profile(profile_id)
        if not profile:
            return set(), f"Profile {profile_id} is not registered in the control plane"
        region = profile_region(profile)
        if not region:
            return set(), f"Profile {profile_id} marketplace cannot be mapped to NA/EU/FE"
        regions.add(region)
    if len(regions) > 1:
        return set(), "one MCP call cannot span Profiles from multiple regions"
    return regions, None


def _tool_call_region_guard(store, tool: dict[str, Any], args: dict[str, Any], session_id: str | None):
    actual = tool_region(tool)
    if not actual:
        return False, "Amazon MCP tool is missing an explicit NA/EU/FE region tag"
    worker = store.worker_for_session(session_id)
    if worker:
        expected, error = _expected_regions_for_task(store, str(worker.get("task_id") or ""))
    else:
        expected, error = _expected_regions_for_args(store, args)
    if error:
        return False, error
    if not expected:
        semantic = str(tool.get("semantic") or "unknown")
        family = str(tool.get("family") or "")
        # Account/Profile discovery is the only regional call allowed before a
        # concrete Profile exists locally. All stateful jobs/writes and scoped
        # entity reads must carry a known Profile or a bound task.
        if semantic == "read" and family in {"profile", "account_admin"}:
            return True, f"regional {actual} account/Profile discovery"
        return False, "regional Amazon MCP call requires a known Profile ID or a Profile-bound task"
    expected_region = next(iter(expected))
    if expected_region != actual:
        return False, f"Profile requires Amazon Ads MCP region {expected_region}, but tool belongs to {actual}"
    return True, f"Profile and Amazon Ads MCP tool both use region {actual}"


def _region_guard(store, decision: dict[str, Any], tool: dict[str, Any]) -> tuple[bool, str]:
    region = tool_region(tool)
    if not region:
        return False, "Amazon MCP tool is missing an explicit NA/EU/FE region tag"
    profile = store.get_profile(str(decision.get("profile_id") or ""))
    expected = profile_region(profile)
    if not expected:
        return False, "Profile marketplace cannot be mapped to an Amazon Ads MCP region"
    if expected != region:
        return False, f"Profile requires Amazon Ads MCP region {expected}, but tool belongs to {region}"
    return True, f"Profile and Amazon Ads MCP tool both use region {region}"


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from .db import Store
    from .policy import redact
    from .service import ControlService

    original_sync_catalog = Store.sync_catalog
    original_authorize = ControlService.authorize_tool
    original_guardrail = ControlService._guardrail_check
    original_create_plan = ControlService.create_managed_plan
    original_context = ControlService.context

    def sync_catalog(self, tools):
        previous_sources = {
            tool.registered_name: str((self.get_tool(tool.registered_name) or {}).get("source") or "")
            for tool in tools
        }
        result = original_sync_catalog(self, tools)
        source_drift = sorted(
            tool.registered_name
            for tool in tools
            if previous_sources.get(tool.registered_name)
            and previous_sources[tool.registered_name] != str(tool.source or "")
        )
        if source_drift:
            with self.connection() as conn:
                placeholders = ",".join("?" for _ in source_drift)
                conn.execute(
                    f"UPDATE mcp_tools SET drifted=1 WHERE registered_name IN ({placeholders})",
                    tuple(source_drift),
                )
            self.alert_once(
                "critical",
                "MCP_REGION_DRIFT",
                None,
                None,
                None,
                "Amazon Ads MCP tool region/source changed; affected tools remain blocked until reviewed",
                {"tools": source_drift},
            )
            result = dict(result)
            result["drifted"] = sorted(set(result.get("drifted") or []) | set(source_drift))
        return result

    def authorize_tool(self, payload: dict[str, Any]):
        tool_name = str(payload.get("tool_name") or "")
        tool = self.store.get_tool(tool_name)
        args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
        session_id = str(payload.get("session_id") or payload.get("task_id") or "") or None
        if tool:
            allowed, reason = _tool_call_region_guard(self.store, tool, args, session_id)
            if not allowed:
                worker = self.store.worker_for_session(session_id)
                actor_role = worker.get("role") if worker else "main"
                task_id = worker.get("task_id") if worker else None
                action_id = self.store.record_action(
                    decision_id=None,
                    task_id=str(task_id) if task_id else None,
                    session_id=session_id,
                    tool_call_id=str(payload.get("tool_call_id") or "") or None,
                    actor_role=str(actor_role),
                    phase="before",
                    tool_name=tool_name,
                    operation=str(tool.get("semantic") or "unknown"),
                    allowed=False,
                    args=redact(args),
                    reason=reason,
                )
                self.store.event(
                    "warning",
                    "tool.region_blocked",
                    str(actor_role),
                    str(task_id) if task_id else None,
                    f"Blocked {tool_name}: {reason}",
                    {"action_id": action_id, "tool_region": tool_region(tool)},
                )
                return {
                    "allowed": False,
                    "reason": reason,
                    "operation": str(tool.get("semantic") or "unknown"),
                    "actor_role": actor_role,
                    "task_id": task_id,
                    "action_id": action_id,
                    "decision_id": None,
                    "plan_key": None,
                    "reservation_token": None,
                    "tool": {
                        key: tool.get(key)
                        for key in ("native_name", "family", "risk", "schema_hash", "source")
                    },
                }
        return original_authorize(self, payload)

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
            if not tool:
                continue
            region = tool_region(tool)
            if not region:
                raise ValueError(
                    f"actions[{index}] uses an Amazon MCP tool without an explicit NA/EU/FE region tag"
                )
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
            "policy": "all Amazon Ads MCP tools require an explicit Profile-matching NA/EU/FE tag; untagged tools fail closed",
        }
        return result

    Store.sync_catalog = sync_catalog
    ControlService.authorize_tool = authorize_tool
    ControlService._guardrail_check = guardrail
    ControlService.create_managed_plan = create_managed_plan
    ControlService.context = context
    _INSTALLED = True
