from __future__ import annotations

from dataclasses import dataclass, fields
from decimal import Decimal
from typing import Any

from .strategy_core import StrategyPolicy as CorePolicy, _clamp


def _dec(value: Any, default: str) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def _flag(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


@dataclass(frozen=True)
class StrategyPolicy(CorePolicy):
    posterior_prior_clicks: Decimal = Decimal("24")
    posterior_prior_cvr_pct: Decimal = Decimal("8")
    posterior_prior_aov_orders: Decimal = Decimal("3")
    posterior_default_aov: Decimal = Decimal("30")
    posterior_reduce_probability: Decimal = Decimal("0.90")
    posterior_scale_probability: Decimal = Decimal("0.80")
    posterior_min_confidence: Decimal = Decimal("0.30")
    delay_curve: tuple[Decimal, ...] = (
        Decimal("0.58"), Decimal("0.76"), Decimal("0.87"), Decimal("0.93"),
        Decimal("0.97"), Decimal("0.99"), Decimal("1"),
    )
    enable_global_budget_allocator: bool = True
    enable_hourly_pacing: bool = True
    hourly_max_bid_change_pct: Decimal = Decimal("8")
    hourly_overpace_ratio: Decimal = Decimal("1.25")
    hourly_underpace_ratio: Decimal = Decimal("0.72")
    sealed_sp_autonomy_enabled: bool = True
    sealed_sp_allow_all_observed_asins: bool = True
    sealed_sp_namespace: str = "HERMES-SP"
    sealed_sp_max_campaign_budget: Decimal = Decimal("50")
    sealed_sp_max_new_budget_per_day: Decimal = Decimal("100")
    sealed_sp_max_campaign_creates_per_day: int = 2
    allow_campaign_creation: bool = True
    allow_state_changes: bool = True
    auto_write_ad_products: tuple[str, ...] = ("SPONSORED_PRODUCTS",)

    @classmethod
    def from_mapping(cls, value: dict[str, Any] | None) -> "StrategyPolicy":
        raw = value or {}
        base = CorePolicy.from_mapping(raw)
        data = {f.name: getattr(base, f.name) for f in fields(CorePolicy)}
        curve = raw.get("attribution_delay_curve", raw.get("delay_curve", cls.delay_curve))
        if isinstance(curve, str):
            curve = [x.strip() for x in curve.split(",") if x.strip()]
        clean: list[Decimal] = []
        last = Decimal("0")
        if isinstance(curve, (list, tuple)):
            for item in curve:
                last = _clamp(_dec(item, "0"), last, Decimal("1"))
                clean.append(last)
        if not clean:
            clean = list(cls.delay_curve)
        if clean[-1] < 1:
            clean.append(Decimal("1"))
        data.update({
            "posterior_prior_clicks": max(Decimal("1"), _dec(raw.get("posterior_prior_clicks"), "24")),
            "posterior_prior_cvr_pct": _clamp(_dec(raw.get("posterior_prior_cvr_pct"), "8"), Decimal("0.01"), Decimal("95")),
            "posterior_prior_aov_orders": max(Decimal("0.1"), _dec(raw.get("posterior_prior_aov_orders"), "3")),
            "posterior_default_aov": max(Decimal("0.01"), _dec(raw.get("posterior_default_aov"), "30")),
            "posterior_reduce_probability": _clamp(_dec(raw.get("posterior_reduce_probability"), "0.90"), Decimal("0.5"), Decimal("0.999")),
            "posterior_scale_probability": _clamp(_dec(raw.get("posterior_scale_probability"), "0.80"), Decimal("0.5"), Decimal("0.999")),
            "posterior_min_confidence": _clamp(_dec(raw.get("posterior_min_confidence"), "0.30"), Decimal("0"), Decimal("1")),
            "delay_curve": tuple(clean),
            "enable_global_budget_allocator": _flag(raw.get("enable_global_budget_allocator"), True),
            "enable_hourly_pacing": _flag(raw.get("enable_hourly_pacing"), True),
            "hourly_max_bid_change_pct": _clamp(_dec(raw.get("hourly_max_bid_change_pct"), "8"), Decimal("1"), Decimal("15")),
            "hourly_overpace_ratio": _clamp(_dec(raw.get("hourly_overpace_ratio"), "1.25"), Decimal("1.01"), Decimal("3")),
            "hourly_underpace_ratio": _clamp(_dec(raw.get("hourly_underpace_ratio"), "0.72"), Decimal("0.1"), Decimal("0.99")),
            "sealed_sp_autonomy_enabled": _flag(raw.get("sealed_sp_autonomy_enabled"), True),
            "sealed_sp_allow_all_observed_asins": _flag(raw.get("sealed_sp_allow_all_observed_asins"), True),
            "sealed_sp_namespace": str(raw.get("sealed_sp_namespace") or "HERMES-SP")[:40],
            "sealed_sp_max_campaign_budget": max(Decimal("1"), _dec(raw.get("sealed_sp_max_campaign_budget"), "50")),
            "sealed_sp_max_new_budget_per_day": max(Decimal("1"), _dec(raw.get("sealed_sp_max_new_budget_per_day"), "100")),
            "sealed_sp_max_campaign_creates_per_day": max(1, int(raw.get("sealed_sp_max_campaign_creates_per_day") or 2)),
            "allow_campaign_creation": _flag(raw.get("allow_campaign_creation"), True),
            "allow_state_changes": _flag(raw.get("allow_state_changes"), True),
            "auto_write_ad_products": ("SPONSORED_PRODUCTS",),
        })
        return cls(**data)
