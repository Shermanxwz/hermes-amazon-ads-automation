from __future__ import annotations

from typing import Any

from .strategy_core import PlanResult
from .strategy_gold import OptimizationEngine as GoldEngine
from .strategy_v4_controls import global_budget, lifecycle
from .strategy_v4_policy import StrategyPolicy
from .strategy_v4_support import gate, hourly


class OptimizationEngine(GoldEngine):
    """Ads-only ACOS controller with attribution-delay and posterior risk gates."""

    def plan(self, snapshot: dict[str, Any], policy: StrategyPolicy) -> PlanResult:
        result = super().plan(snapshot, policy)
        quality = result.data_quality
        quality["decision_os_version"] = "4.0"
        summary = quality.get("strategy_summary")
        if isinstance(summary, dict):
            summary.update({"objective": "ads_attributed_target_acos", "mode": "posterior-sealed"})
        if not quality.get("eligible_for_writes"):
            quality["probabilistic_acos"] = {"enabled": True, "gated": 0}
            return result
        decisions, suppressed, ctx = gate(result.decisions, snapshot, policy, quality.get("end_age_days"))
        if policy.enable_global_budget_allocator:
            decisions = global_budget(self, snapshot, policy, ctx, quality.get("end_age_days"), decisions)
        if policy.enable_hourly_pacing:
            decisions.extend(hourly(self, snapshot, policy, ctx))
        if policy.allow_state_changes and policy.sealed_sp_autonomy_enabled:
            decisions.extend(lifecycle(snapshot, policy, ctx, quality.get("end_age_days")))
        result.decisions = self._dedupe(decisions)[:policy.max_decisions_per_cycle]
        quality["probabilistic_acos"] = {"enabled": True, "suppressed": suppressed, "gated": len(suppressed)}
        quality["sealed_sp_autonomy"] = {
            "enabled": policy.sealed_sp_autonomy_enabled,
            "namespace": policy.sealed_sp_namespace,
            "max_campaign_budget": float(policy.sealed_sp_max_campaign_budget),
            "max_new_budget_per_day": float(policy.sealed_sp_max_new_budget_per_day),
            "max_campaign_creates_per_day": policy.sealed_sp_max_campaign_creates_per_day,
            "ad_products": ["SPONSORED_PRODUCTS"],
        }
        return result


__all__ = ["OptimizationEngine", "StrategyPolicy"]
