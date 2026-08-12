from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Iterable


REQUIRED_SP_OPERATIONS: tuple[str, ...] = (
    "profile.read",
    "campaign.read", "campaign.create", "campaign.update",
    "ad_group.read", "ad_group.create", "ad_group.update",
    "ad.read", "ad.create", "ad.update",
    "target.read", "target.create", "target.update",
    "negative.create",
    "report.create", "report.status", "report.download",
    "budget.read", "recommendation.read",
)

DIRECT_API_FALLBACKS: dict[str, str] = {
    "profile.read": "GET /v2/profiles",
    "campaign.read": "POST /sp/campaigns/list",
    "campaign.create": "POST /sp/campaigns",
    "campaign.update": "PUT /sp/campaigns",
    "ad_group.read": "POST /sp/adGroups/list",
    "ad_group.create": "POST /sp/adGroups",
    "ad_group.update": "PUT /sp/adGroups",
    "ad.read": "POST /sp/productAds/list",
    "ad.create": "POST /sp/productAds",
    "ad.update": "PUT /sp/productAds",
    "target.read": "POST /sp/targets/list",
    "target.create": "POST /sp/targets",
    "target.update": "PUT /sp/targets",
    "negative.create": "POST /sp/negativeKeywords",
    "report.create": "POST /reporting/reports",
    "report.status": "GET /reporting/reports/{reportId}",
    "report.download": "GET report.url",
    "budget.read": "POST /sp/campaigns/budgetUsage",
    "recommendation.read": "GET /recommendations",
}


@dataclass(frozen=True)
class OperationRoute:
    operation: str
    primary: str | None
    fallback: str | None
    verified: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "primary": self.primary,
            "fallback": self.fallback,
            "verified": self.verified,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class CapabilityAttestation:
    profile_id: str
    region: str
    tool_manifest_hash: str
    routes: tuple[OperationRoute, ...]
    sealed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "profile_id": self.profile_id,
            "region": self.region,
            "tool_manifest_hash": self.tool_manifest_hash,
            "sealed": self.sealed,
            "routes": [route.as_dict() for route in self.routes],
            "missing": [route.operation for route in self.routes if not route.verified],
        }


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _tool_name(tool: dict[str, Any]) -> str:
    return str(tool.get("registered_name") or tool.get("name") or "")


def _native_name(tool: dict[str, Any]) -> str:
    return str(tool.get("native_name") or _tool_name(tool)).lower().replace("-", "_")


def _semantic(tool: dict[str, Any]) -> str:
    return str(tool.get("semantic") or tool.get("classification", {}).get("authority") or "").lower()


def _is_read(tool: dict[str, Any]) -> bool:
    semantic = _semantic(tool)
    annotations = tool.get("annotations") if isinstance(tool.get("annotations"), dict) else {}
    return semantic in {"read", "read_only"} or annotations.get("readOnlyHint") is True


def _is_write(tool: dict[str, Any]) -> bool:
    semantic = _semantic(tool)
    return semantic in {"write", "planned_executor_only"}


def _matches(tool: dict[str, Any], operation: str) -> bool:
    name = _native_name(tool)
    domain, verb = operation.split(".", 1)
    aliases = {
        "profile": ("profile", "advertiser_account"),
        "campaign": ("campaign",),
        "ad_group": ("ad_group", "adgroup"),
        "ad": ("product_ad", "ad_association", "_ad"),
        "target": ("target", "keyword"),
        "negative": ("negative",),
        "report": ("report", "reporting"),
        "budget": ("budget_usage", "budget"),
        "recommendation": ("recommend",),
    }
    actions = {
        "read": ("query", "get", "list", "read", "retrieve", "status"),
        "create": ("create", "add", "submit"),
        "update": ("update", "set", "pause", "resume", "enable", "disable"),
        "status": ("status", "get", "query", "retrieve"),
        "download": ("download", "get"),
    }
    if not any(alias in name for alias in aliases[domain]):
        return False
    if not any(action in name for action in actions[verb]):
        return False
    if verb == "read" and not _is_read(tool):
        return False
    if verb in {"create", "update"} and not _is_write(tool):
        return False
    if operation == "negative.create" and "negative" not in name:
        return False
    if re.search(r"(?:billing|invoice|permission|role|delete|archive)", name):
        return False
    return True


def attest_profile_capabilities(
    *,
    profile_id: str,
    region: str,
    tools: Iterable[dict[str, Any]],
    direct_api_operations: Iterable[str] = REQUIRED_SP_OPERATIONS,
) -> CapabilityAttestation:
    normalized_tools = [dict(tool) for tool in tools if isinstance(tool, dict)]
    direct = set(direct_api_operations)
    manifest = [
        {
            "name": _tool_name(tool),
            "native": _native_name(tool),
            "semantic": _semantic(tool),
            "schema_hash": tool.get("schema_hash") or tool.get("schemaHash"),
            "source": tool.get("source"),
        }
        for tool in normalized_tools
    ]
    manifest_hash = hashlib.sha256(_canonical(sorted(manifest, key=lambda item: item["name"])).encode()).hexdigest()
    routes: list[OperationRoute] = []
    for operation in REQUIRED_SP_OPERATIONS:
        matches = sorted(_tool_name(tool) for tool in normalized_tools if _matches(tool, operation))
        primary = matches[0] if matches else None
        fallback = DIRECT_API_FALLBACKS.get(operation) if operation in direct else None
        verified = bool(primary or fallback)
        reason = (
            "live MCP atomic tool with deterministic Direct Ads API fallback" if primary and fallback else
            "live MCP atomic tool" if primary else
            "Direct Ads API deterministic fallback" if fallback else
            "required SP operation is unavailable"
        )
        routes.append(OperationRoute(operation, primary, fallback, verified, reason))
    return CapabilityAttestation(
        profile_id=profile_id,
        region=region.lower(),
        tool_manifest_hash=manifest_hash,
        routes=tuple(routes),
        sealed=all(route.verified for route in routes),
    )


def validate_attestation(value: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if value.get("version") != 1:
        errors.append("unsupported attestation version")
    if not value.get("profile_id"):
        errors.append("profile_id is required")
    if value.get("region") not in {"na", "eu", "fe"}:
        errors.append("region must be na, eu or fe")
    routes = value.get("routes")
    if not isinstance(routes, list):
        return errors + ["routes must be an array"]
    by_operation = {str(row.get("operation")): row for row in routes if isinstance(row, dict)}
    for operation in REQUIRED_SP_OPERATIONS:
        row = by_operation.get(operation)
        if not row:
            errors.append(f"missing route: {operation}")
        elif row.get("verified") is not True:
            errors.append(f"unverified route: {operation}")
    if bool(value.get("sealed")) != (not errors):
        errors.append("sealed flag does not match route verification")
    return errors
