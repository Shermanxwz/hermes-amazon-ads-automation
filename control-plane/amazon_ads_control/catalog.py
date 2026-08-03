from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Iterable

SERVER_NAME = "amazon-ads"
TOOLSET_NAME = "mcp-amazon-ads"
REGISTERED_PREFIX = "mcp_amazon_ads_"

_READ_PREFIXES = (
    "get", "list", "query", "retrieve", "check", "describe", "search", "read",
    "fetch", "inspect", "forecast", "recommend", "report", "status", "download",
)
_WRITE_PREFIXES = (
    "create", "update", "delete", "archive", "pause", "resume", "enable", "disable",
    "set", "adjust", "apply", "mutate", "add", "remove", "copy", "launch", "expand",
    "associate", "disassociate", "invite", "accept", "reject", "execute", "start", "cancel",
)
_HIGH_RISK_WORDS = (
    "delete", "archive", "billing", "invoice", "payment", "user", "permission", "role",
    "account", "terms", "locale", "expand", "launch", "create_account",
)

_FAMILY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("profile", ("profile", "ads_account", "advertiser_account", "manager_account")),
    ("ad_group", ("ad_group", "adgroup")),
    ("target", ("target", "keyword", "product_target")),
    ("portfolio", ("portfolio",)),
    ("recommendation", ("recommend", "guidance", "opportunity")),
    ("budget", ("budget_rule", "budget_usage", "budget_recommend")),
    ("stream", ("stream", "subscription")),
    ("eligibility", ("eligibility", "eligible")),
    ("report", ("report", "reporting", "export", "snapshot")),
    ("promotion", ("promotion", "promo")),
    ("billing", ("billing", "invoice")),
    ("account_admin", ("user", "permission", "role", "invitation", "account_link")),
    ("amc", ("amc", "workflow", "data_source")),
    ("ad", ("ad_association", "creative", "_ad", "ad_")),
    ("campaign", ("campaign",)),
)



def _tokens(name: str) -> list[str]:
    return [part for part in re.split(r"[^a-z0-9]+", name.lower()) if part]


def native_name_from_registered(name: str) -> str:
    if name.startswith(REGISTERED_PREFIX):
        return name[len(REGISTERED_PREFIX):]
    return name


def registered_name(native_name: str) -> str:
    sanitized = re.sub(r"[-.]", "_", native_name)
    return REGISTERED_PREFIX + sanitized


def is_registered_amazon_tool(name: str) -> bool:
    return name.startswith(REGISTERED_PREFIX)


def infer_semantic(native_name: str, schema: dict[str, Any] | None = None, family: str | None = None) -> str:
    """Classify once at catalog sync time; runtime authorization uses the exact row.

    The result is deliberately conservative: ambiguity is ``unknown`` and is blocked.
    """
    lowered = native_name.lower().replace(".", "-")
    words = _tokens(lowered)
    description = str((schema or {}).get("description", "")).lower()
    leading = words[-1] if words else ""
    # Amazon MCP tool names often use domain-action (e.g. campaign_management-query_campaign).
    action_candidates = [part for part in words if part in _READ_PREFIXES or part in _WRITE_PREFIXES]
    if any(part in _WRITE_PREFIXES for part in action_candidates):
        # Creating/polling an Ads reporting or export job does not mutate ad delivery.
        # It is still bounded and audited separately from normal reads.
        if family == "report":
            return "job"
        return "write"
    if any(part in _READ_PREFIXES for part in action_candidates):
        return "read"
    if leading in _WRITE_PREFIXES:
        return "job" if family == "report" else "write"
    if leading in _READ_PREFIXES:
        return "read"
    # Description fallback is used only during catalog import and remains auditable.
    if re.search(r"\b(create|update|delete|modify|apply|launch|pause|resume|enable|disable)\b", description):
        return "job" if family == "report" else "write"
    if re.search(r"\b(get|list|query|retrieve|read|return|check|report|forecast)\b", description):
        return "read"
    return "unknown"


def infer_family(native_name: str) -> str:
    lowered = native_name.lower().replace("-", "_").replace(".", "_")
    for family, needles in _FAMILY_RULES:
        if any(needle in lowered for needle in needles):
            return family
    return "other"


def infer_risk(native_name: str, semantic: str, family: str) -> str:
    lowered = native_name.lower().replace("-", "_")
    if semantic == "read":
        return "low"
    if semantic == "job":
        return "medium"
    if semantic == "unknown":
        return "critical"
    if family in {"billing", "account_admin"} or any(word in lowered for word in _HIGH_RISK_WORDS):
        return "critical"
    if family in {"campaign", "ad_group", "ad", "portfolio", "stream", "amc"} and any(
        word in lowered for word in ("create", "delete", "archive", "launch", "expand")
    ):
        return "high"
    return "medium"


def stable_schema_hash(schema: dict[str, Any]) -> str:
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ToolDescriptor:
    registered_name: str
    native_name: str
    server_name: str
    semantic: str
    family: str
    risk: str
    schema: dict[str, Any]
    schema_hash: str
    enabled: bool = True
    source: str = "hermes-registry"

    def as_dict(self) -> dict[str, Any]:
        return {
            "registered_name": self.registered_name,
            "native_name": self.native_name,
            "server_name": self.server_name,
            "semantic": self.semantic,
            "family": self.family,
            "risk": self.risk,
            "schema": self.schema,
            "schema_hash": self.schema_hash,
            "enabled": self.enabled,
            "source": self.source,
        }


def descriptor_from_payload(raw: dict[str, Any]) -> ToolDescriptor:
    name = str(raw.get("registered_name") or raw.get("name") or "").strip()
    if not name:
        raise ValueError("catalog tool requires registered_name")
    native = str(raw.get("native_name") or native_name_from_registered(name)).strip()
    server = str(raw.get("server_name") or SERVER_NAME).strip()
    schema = raw.get("schema") if isinstance(raw.get("schema"), dict) else {}
    family = str(raw.get("family") or infer_family(native)).lower()
    semantic = str(raw.get("semantic") or infer_semantic(native, schema, family)).lower()
    if semantic not in {"read", "job", "write", "unknown"}:
        raise ValueError(f"invalid semantic for {name}: {semantic}")
    risk = str(raw.get("risk") or infer_risk(native, semantic, family)).lower()
    if risk not in {"low", "medium", "high", "critical"}:
        raise ValueError(f"invalid risk for {name}: {risk}")
    return ToolDescriptor(
        registered_name=name,
        native_name=native,
        server_name=server,
        semantic=semantic,
        family=family,
        risk=risk,
        schema=schema,
        schema_hash=str(raw.get("schema_hash") or stable_schema_hash(schema)),
        enabled=bool(raw.get("enabled", True)),
        source=str(raw.get("source") or "hermes-registry"),
    )


def catalog_digest(tools: Iterable[ToolDescriptor | dict[str, Any]]) -> str:
    rows: list[dict[str, Any]] = []
    for tool in tools:
        row = tool.as_dict() if isinstance(tool, ToolDescriptor) else dict(tool)
        rows.append({
            "registered_name": row.get("registered_name"),
            "semantic": row.get("semantic"),
            "family": row.get("family"),
            "risk": row.get("risk"),
            "schema_hash": row.get("schema_hash"),
        })
    encoded = json.dumps(sorted(rows, key=lambda item: str(item["registered_name"])), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
