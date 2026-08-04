from __future__ import annotations

from typing import Any

_INSTALLED = False
_ALLOWED_NESTED_MULTI = {
    "expression", "expressions", "targetingexpression", "targetingexpressions",
    "predicate", "predicates", "clause", "clauses", "filter", "filters",
}
_ENTITY_ARRAYS = {
    "campaign", "campaigns", "adgroup", "adgroups", "target", "targets",
    "keyword", "keywords", "ad", "ads", "productad", "productads",
    "portfolio", "portfolios", "negativekeyword", "negativekeywords",
    "negativetarget", "negativetargets", "recommendation", "recommendations",
    "operation", "operations", "entity", "entities", "item", "items",
    "request", "requests",
}


def _leaf(path: str) -> str:
    value = path.rsplit(".", 1)[-1].split("[", 1)[0]
    return "".join(character for character in value.lower() if character.isalnum())


def _violation(value: Any, maximum: int, path: str = "$") -> str | None:
    if isinstance(value, list):
        leaf = _leaf(path)
        if len(value) > maximum and leaf not in _ALLOWED_NESTED_MULTI:
            kind = "entity batch" if leaf in _ENTITY_ARRAYS else "unrecognized multi-item list"
            return f"{path} contains {len(value)} items; autonomous {kind} limit is {maximum}"
        for index, item in enumerate(value):
            violation = _violation(item, maximum, f"{path}[{index}]")
            if violation:
                return violation
    elif isinstance(value, dict):
        for key, item in value.items():
            violation = _violation(item, maximum, f"{path}.{key}")
            if violation:
                return violation
    return None


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from . import service as service_module

    service_module._write_batch_violation = _violation
    _INSTALLED = True
