"""Offline replay and shadow-evaluation helpers for deterministic ad decisions.

This module never claims causal lift. It replays only data that would have been visible at the
snapshot time and compares generated rules with optional operator labels or later performance
proxies. Real production acceptance still requires account history and shadow/canary operation.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from typing import Any, Iterable

from .strategy import OptimizationEngine, StrategyPolicy


def replay_cases(cases: Iterable[dict[str, Any]], policy: StrategyPolicy | None = None) -> dict[str, Any]:
    engine = OptimizationEngine()
    policy = policy or StrategyPolicy()
    totals = Counter()
    results: list[dict[str, Any]] = []
    for index, case in enumerate(cases):
        if not isinstance(case, dict) or not isinstance(case.get("snapshot"), dict):
            raise ValueError(f"case {index} requires a snapshot object")
        plan = engine.plan(case["snapshot"], policy)
        rules = [decision.rule_id for decision in plan.decisions]
        expected = {str(item) for item in case.get("expected_rule_ids", [])}
        forbidden = {str(item) for item in case.get("forbidden_rule_ids", [])}
        missing = sorted(expected - set(rules))
        unexpected = sorted(forbidden & set(rules))
        status = "pass" if not missing and not unexpected else "fail"
        totals[status] += 1
        totals["cases"] += 1
        totals["eligible"] += int(bool(plan.data_quality.get("eligible_for_writes")))
        totals["decisions"] += len(plan.decisions)
        for rule in rules:
            totals[f"rule:{rule}"] += 1
        results.append({
            "id": str(case.get("id") or index),
            "status": status,
            "eligible_for_writes": bool(plan.data_quality.get("eligible_for_writes")),
            "data_quality": plan.data_quality,
            "kpis": plan.kpis,
            "rules": rules,
            "missing_expected": missing,
            "forbidden_triggered": unexpected,
            "decisions": [decision.as_dict() for decision in plan.decisions],
        })
    return {
        "summary": {
            "cases": totals["cases"], "passed": totals["pass"], "failed": totals["fail"],
            "eligible": totals["eligible"], "decisions": totals["decisions"],
            "rules": {key.removeprefix("rule:"): value for key, value in sorted(totals.items()) if key.startswith("rule:")},
        },
        "results": results,
        "causal_claim": False,
        "note": "Replay validates deterministic policy behavior; it does not prove incremental advertising lift.",
    }
