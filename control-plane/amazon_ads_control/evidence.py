from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Iterable


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def key_norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def numeric_equal(left: Any, right: Any, *, tolerance: float = 0.01) -> bool:
    try:
        return math.isclose(float(left), float(right), rel_tol=1e-6, abs_tol=tolerance)
    except (TypeError, ValueError):
        return str(left).strip().lower() == str(right).strip().lower()


def iter_objects(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from iter_objects(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_objects(item)


def field_values(obj: dict[str, Any], aliases: str) -> list[Any]:
    wanted = {key_norm(item) for item in aliases.split("|") if item.strip()}
    return [value for key, value in obj.items() if key_norm(key) in wanted]


def object_contains_scalar(obj: dict[str, Any], wanted: Any) -> bool:
    needle = str(wanted).strip().lower()
    if not needle:
        return True
    for value in obj.values():
        if isinstance(value, (str, int, float)) and str(value).strip().lower() == needle:
            return True
    return False


def _identifier_constraints(decision: dict[str, Any]) -> list[tuple[str, Any]]:
    payload = decision.get("payload") if isinstance(decision.get("payload"), dict) else {}
    constraints: list[tuple[str, Any]] = []
    entity_id = str(decision.get("entity_id") or "").strip()
    if entity_id:
        constraints.append((
            "target_id|targetId|keyword_id|keywordId|campaign_id|campaignId|ad_group_id|adGroupId|recommendation_id|recommendationId|id",
            entity_id,
        ))
    for aliases, wanted in (payload.get("match_fields") or {}).items() if isinstance(payload.get("match_fields"), dict) else []:
        normalized = key_norm(str(aliases))
        if any(token in normalized for token in ("id", "placement", "keywordtext", "searchterm", "matchtype")):
            constraints.append((str(aliases), wanted))
    return constraints


def object_matches_identifiers(obj: dict[str, Any], decision: dict[str, Any]) -> bool:
    constraints = _identifier_constraints(decision)
    if not constraints:
        return False
    matched = 0
    for aliases, wanted in constraints:
        values = field_values(obj, aliases)
        if values and any(numeric_equal(item, wanted) for item in values):
            matched += 1
        elif aliases.startswith("target_id|") and object_contains_scalar(obj, wanted):
            matched += 1
        else:
            return False
    return matched == len(constraints)


def expected_differences(expected: dict[str, Any], obj: dict[str, Any]) -> dict[str, Any]:
    differences: dict[str, Any] = {}
    for aliases, wanted in expected.items():
        values = field_values(obj, str(aliases))
        if not values:
            differences[str(aliases)] = {"expected": wanted, "actual": "[missing]"}
        elif not any(numeric_equal(item, wanted) for item in values):
            differences[str(aliases)] = {"expected": wanted, "actual": values[:10]}
    return differences


def select_entity_object(actual: Any, decision: dict[str, Any], expected: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return one exact entity object; never combine fields across sibling rows."""
    candidates = [obj for obj in iter_objects(actual) if object_matches_identifiers(obj, decision)]
    if not candidates:
        raise ValueError("read evidence does not contain one object for the planned entity")
    if expected:
        exact = [obj for obj in candidates if not expected_differences(expected, obj)]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            raise ValueError("read evidence ambiguously contains multiple matching entity objects")
    if len(candidates) != 1:
        raise ValueError("read evidence ambiguously contains multiple objects for the planned entity")
    return candidates[0]


def before_state(decision: dict[str, Any]) -> dict[str, Any]:
    payload = decision.get("payload") if isinstance(decision.get("payload"), dict) else {}
    field = str(payload.get("field") or "").strip()
    if field and "before" in payload:
        return {field: payload["before"]}
    explicit = payload.get("expected_before")
    return explicit if isinstance(explicit, dict) else {}


def verify_before_state(actual: Any, decision: dict[str, Any]) -> tuple[dict[str, Any], str]:
    expected = before_state(decision)
    if not expected:
        raise ValueError("planned write has no machine-verifiable before state")
    entity = select_entity_object(actual, decision)
    differences = expected_differences(expected, entity)
    if differences:
        raise ValueError(f"fresh Amazon state no longer matches planned before value: {differences}")
    return entity, canonical_hash(entity)
