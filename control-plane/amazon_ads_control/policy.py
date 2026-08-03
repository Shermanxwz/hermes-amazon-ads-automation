from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

READ_VERBS = (
    "get", "list", "query", "retrieve", "check", "describe", "search", "report", "status",
)
WRITE_VERBS = (
    "create", "update", "delete", "archive", "pause", "resume", "enable", "disable",
    "set", "adjust", "apply", "mutate", "add", "remove", "copy",
)
SECRET_KEYS = re.compile(r"(secret|token|authorization|password|cookie|client[_-]?id)", re.I)
SECRET_TEXT_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)((?:access|refresh|id)[_-]?token\s*[:=]\s*[\"']?)[^\s,;\"']+"),
    re.compile(r"(?i)((?:client[_-]?secret|password|cookie)\s*[:=]\s*[\"']?)[^\s,;\"']+"),
)
AMAZON_HINT = re.compile(r"(^|[-_.])(amazon|ads|campaign|ad_group|keyword|target|portfolio|budget|bid)([-_.]|$)", re.I)


def is_amazon_ads_tool(tool_name: str) -> bool:
    lowered = tool_name.lower()
    return lowered.startswith("mcp_amazon_ads_") or lowered.startswith("amazon_ads_") or bool(AMAZON_HINT.search(lowered))


def classify_tool(tool_name: str) -> str:
    lowered = tool_name.lower().replace("-", "_")
    if not is_amazon_ads_tool(lowered):
        return "other"
    segments = [part for part in re.split(r"[^a-z0-9]+", lowered) if part]
    has_write = any(verb in segments or f"_{verb}_" in f"_{lowered}_" for verb in WRITE_VERBS)
    has_read = any(verb in segments or f"_{verb}_" in f"_{lowered}_" for verb in READ_VERBS)
    if has_write:
        return "write"
    if has_read:
        return "read"
    return "unknown"


def redact_text(value: str) -> str:
    text = value[:8000] + ("…" if len(value) > 8000 else "")
    for pattern in SECRET_TEXT_PATTERNS:
        text = pattern.sub(r"\1[redacted]", text)
    return text


def redact(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        return "[truncated]"
    if isinstance(value, dict):
        out = {}
        for key, item in list(value.items())[:100]:
            out[str(key)] = "[redacted]" if SECRET_KEYS.search(str(key)) else redact(item, depth=depth + 1)
        return out
    if isinstance(value, list):
        return [redact(item, depth=depth + 1) for item in value[:100]]
    if isinstance(value, str):
        return redact_text(value)[:4000]
    return value


def _find_key(value: Any, wanted: str) -> list[Any]:
    found: list[Any] = []
    normalized = wanted.lower().replace("-", "_")
    if isinstance(value, dict):
        for key, item in value.items():
            key_norm = str(key).lower().replace("-", "_")
            if key_norm == normalized or key_norm.endswith("_" + normalized):
                found.append(item)
            found.extend(_find_key(item, wanted))
    elif isinstance(value, list):
        for item in value:
            found.extend(_find_key(item, wanted))
    return found


def match_planned_action(tool_name: str, args: dict[str, Any], actions: list[Any]) -> tuple[dict[str, Any] | None, str]:
    args_text = str(args).lower()
    for index, raw in enumerate(actions):
        if not isinstance(raw, dict):
            continue
        patterns = raw.get("tool_contains", [])
        if isinstance(patterns, str):
            patterns = [patterns]
        if patterns and not all(str(pattern).lower() in tool_name.lower() for pattern in patterns):
            continue
        entity_id = str(raw.get("entity_id", "")).strip()
        if entity_id and entity_id.lower() not in args_text:
            continue
        field = str(raw.get("field", "")).strip()
        if field and "after" in raw:
            values = _find_key(args, field)
            if not values:
                continue
            expected = raw.get("after")
            if not any(str(value) == str(expected) for value in values):
                continue
        item = dict(raw)
        item.setdefault("plan_key", str(raw.get("idempotency_key") or f"plan-{index}"))
        return item, "matched planned action"
    return None, "write does not match any planned action"


@dataclass(frozen=True)
class Guardrails:
    max_bid_change_pct: int = 15
    max_budget_change_pct: int = 20
    max_actions_per_task: int = 50
    max_actions_per_day: int = 250
    block_deletes: bool = True

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "Guardrails":
        return cls(
            max_bid_change_pct=int(data.get("max_bid_change_pct", 15)),
            max_budget_change_pct=int(data.get("max_budget_change_pct", 20)),
            max_actions_per_task=int(data.get("max_actions_per_task", 50)),
            max_actions_per_day=int(data.get("max_actions_per_day", 250)),
            block_deletes=bool(data.get("block_deletes", True)),
        )


def validate_write(tool_name: str, args: dict[str, Any], guardrails: Guardrails) -> tuple[bool, str]:
    lowered = tool_name.lower()
    if guardrails.block_deletes and any(x in lowered for x in ("delete", "archive")):
        return False, "delete/archive operations are disabled"
    pct = args.get("change_percent", args.get("changePercent"))
    if pct is not None:
        try:
            pct_value = abs(float(pct))
        except (TypeError, ValueError):
            return False, "change_percent must be numeric"
        limit = guardrails.max_budget_change_pct if "budget" in lowered else guardrails.max_bid_change_pct
        if pct_value > limit:
            return False, f"requested change {pct_value:g}% exceeds {limit}% guardrail"
    return True, "within configured guardrails"
