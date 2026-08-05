from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from typing import Any, Iterable

UTC = timezone.utc
TRUSTED_SOURCES = {"amazon-ads-mcp", "amazon-ads-api", "amazon-ads-report", "amazon-marketing-stream"}
ACTIVE_STATES = {"ENABLED", "ACTIVE"}
PLACEMENTS = {
    "PLACEMENT_TOP": "PLACEMENT_TOP", "TOP_OF_SEARCH": "PLACEMENT_TOP", "TOP OF SEARCH": "PLACEMENT_TOP",
    "PLACEMENT_PRODUCT_PAGE": "PLACEMENT_PRODUCT_PAGE", "PRODUCT_PAGES": "PLACEMENT_PRODUCT_PAGE",
    "PRODUCT PAGE": "PLACEMENT_PRODUCT_PAGE", "PLACEMENT_REST_OF_SEARCH": "PLACEMENT_REST_OF_SEARCH",
    "REST_OF_SEARCH": "PLACEMENT_REST_OF_SEARCH", "REST OF SEARCH": "PLACEMENT_REST_OF_SEARCH",
}


def _parse_decimal(value: Any) -> Decimal | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        value = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None
    return value if value.is_finite() else None


def _d(value: Any, default: str = "0") -> Decimal:
    parsed = _parse_decimal(value)
    return parsed if parsed is not None else Decimal(default)


def _i(value: Any, default: int = 0) -> int:
    parsed = _parse_decimal(value)
    return int(parsed) if parsed is not None and parsed == parsed.to_integral_value() else default


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _clamp(value: Decimal, minimum: Decimal, maximum: Decimal) -> Decimal:
    return min(maximum, max(minimum, value))


def _q(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    return numerator / denominator if denominator > 0 else None


def _stable_key(parts: Iterable[Any]) -> str:
    raw = "|".join(json.dumps(p, ensure_ascii=False, sort_keys=True, default=str) for p in parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _dt(value: Any) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


@dataclass(frozen=True)
class StrategyPolicy:
    target_acos: Decimal = Decimal("30")
    max_acos: Decimal = Decimal("45")
    min_clicks: int = 8
    min_orders: int = 2
    min_spend: Decimal = Decimal("10")
    waste_clicks: int = 12
    waste_spend: Decimal = Decimal("20")
    harvest_orders: int = 2
    harvest_max_acos: Decimal = Decimal("30")
    bid_increase_pct: Decimal = Decimal("10")
    bid_decrease_pct: Decimal = Decimal("12")
    severe_bid_decrease_pct: Decimal = Decimal("20")
    budget_increase_pct: Decimal = Decimal("15")
    budget_decrease_pct: Decimal = Decimal("12")
    placement_increase_points: Decimal = Decimal("10")
    placement_decrease_points: Decimal = Decimal("10")
    max_bid_change_pct: Decimal = Decimal("20")
    max_budget_change_pct: Decimal = Decimal("25")
    max_placement_change_points: Decimal = Decimal("25")
    attribution_lag_days: int = 2
    min_window_days: int = 7
    max_data_age_days: int = 7
    cooldown_hours: int = 24
    learning_min_clicks: int = 12
    stable_min_orders: int = 4
    scale_min_orders: int = 5
    min_confidence_to_reduce: Decimal = Decimal("0.55")
    min_confidence_to_scale: Decimal = Decimal("0.70")
    max_decisions_per_cycle: int = 25
    min_bid: Decimal = Decimal("0.02")
    max_bid: Decimal = Decimal("1000")
    min_budget: Decimal = Decimal("1")
    max_budget: Decimal = Decimal("1000000")
    allow_bid_changes: bool = True
    allow_budget_changes: bool = True
    allow_budget_decreases: bool = True
    allow_negatives: bool = True
    allow_harvest: bool = True
    allow_placement_changes: bool = True
    allow_campaign_creation: bool = False
    allow_state_changes: bool = False
    allow_official_recommendation_apply: bool = False
    recommendation_types: tuple[str, ...] = ("BID", "BUDGET", "KEYWORD", "TARGET")
    # Routine autonomous writes default to Sponsored Products only. Other ad
    # products remain observable and require an explicit operator-approved policy.
    auto_write_ad_products: tuple[str, ...] = ("SPONSORED_PRODUCTS",)

    @classmethod
    def from_mapping(cls, value: dict[str, Any] | None) -> "StrategyPolicy":
        d = value or {}
        recs = d.get("recommendation_types", cls.recommendation_types)
        if isinstance(recs, str):
            recs = [x.strip() for x in recs.split(",") if x.strip()]
        products = d.get("auto_write_ad_products", cls.auto_write_ad_products)
        if isinstance(products, str):
            products = [x.strip() for x in products.split(",") if x.strip()]
        target = _clamp(_d(d.get("target_acos"), "30"), Decimal("1"), Decimal("500"))
        maximum = _clamp(max(target, _d(d.get("max_acos"), "45")), Decimal("1"), Decimal("1000"))
        min_bid = max(Decimal("0.01"), _d(d.get("min_bid"), "0.02"))
        max_bid = max(min_bid, _d(d.get("max_bid"), "1000"))
        min_budget = max(Decimal("0.01"), _d(d.get("min_budget"), "1"))
        max_budget = max(min_budget, _d(d.get("max_budget"), "1000000"))
        return cls(
            target_acos=target, max_acos=maximum,
            min_clicks=max(1, _i(d.get("min_clicks"), 8)), min_orders=max(1, _i(d.get("min_orders"), 2)),
            min_spend=max(Decimal("0"), _d(d.get("min_spend"), "10")),
            waste_clicks=max(1, _i(d.get("waste_clicks"), 12)), waste_spend=max(Decimal("0"), _d(d.get("waste_spend"), "20")),
            harvest_orders=max(1, _i(d.get("harvest_orders"), 2)),
            harvest_max_acos=_clamp(_d(d.get("harvest_max_acos"), str(target)), Decimal("1"), Decimal("1000")),
            bid_increase_pct=_clamp(_d(d.get("bid_increase_pct"), "10"), Decimal("0"), Decimal("100")),
            bid_decrease_pct=_clamp(_d(d.get("bid_decrease_pct"), "12"), Decimal("0"), Decimal("100")),
            severe_bid_decrease_pct=_clamp(_d(d.get("severe_bid_decrease_pct"), "20"), Decimal("0"), Decimal("100")),
            budget_increase_pct=_clamp(_d(d.get("budget_increase_pct"), "15"), Decimal("0"), Decimal("100")),
            budget_decrease_pct=_clamp(_d(d.get("budget_decrease_pct"), "12"), Decimal("0"), Decimal("100")),
            placement_increase_points=_clamp(_d(d.get("placement_increase_points"), "10"), Decimal("0"), Decimal("900")),
            placement_decrease_points=_clamp(_d(d.get("placement_decrease_points"), "10"), Decimal("0"), Decimal("900")),
            max_bid_change_pct=_clamp(_d(d.get("max_bid_change_pct"), "20"), Decimal("1"), Decimal("100")),
            max_budget_change_pct=_clamp(_d(d.get("max_budget_change_pct"), "25"), Decimal("1"), Decimal("100")),
            max_placement_change_points=_clamp(_d(d.get("max_placement_change_points"), "25"), Decimal("1"), Decimal("900")),
            attribution_lag_days=max(0, _i(d.get("attribution_lag_days"), 2)),
            min_window_days=max(1, _i(d.get("min_window_days"), 7)), max_data_age_days=max(1, _i(d.get("max_data_age_days"), 7)),
            cooldown_hours=max(0, _i(d.get("cooldown_hours", d.get("decision_cooldown_hours")), 24)),
            learning_min_clicks=max(1, _i(d.get("learning_min_clicks"), 12)),
            stable_min_orders=max(1, _i(d.get("stable_min_orders"), 4)), scale_min_orders=max(1, _i(d.get("scale_min_orders"), 5)),
            min_confidence_to_reduce=_clamp(_d(d.get("min_confidence_to_reduce"), "0.55"), Decimal("0"), Decimal("1")),
            min_confidence_to_scale=_clamp(_d(d.get("min_confidence_to_scale"), "0.70"), Decimal("0"), Decimal("1")),
            max_decisions_per_cycle=max(1, _i(d.get("max_decisions_per_cycle"), 25)),
            min_bid=min_bid, max_bid=max_bid, min_budget=min_budget, max_budget=max_budget,
            allow_bid_changes=_bool(d.get("allow_bid_changes"), True),
            allow_budget_changes=_bool(d.get("allow_budget_changes"), True),
            allow_budget_decreases=_bool(d.get("allow_budget_decreases"), True),
            allow_negatives=_bool(d.get("allow_negatives"), True), allow_harvest=_bool(d.get("allow_harvest"), True),
            allow_placement_changes=_bool(d.get("allow_placement_changes"), True),
            allow_campaign_creation=_bool(d.get("allow_campaign_creation"), False),
            allow_state_changes=_bool(d.get("allow_state_changes"), False),
            allow_official_recommendation_apply=_bool(d.get("allow_official_recommendation_apply"), False),
            recommendation_types=tuple(sorted({str(x).upper() for x in recs if str(x).strip()})),
            auto_write_ad_products=tuple(sorted({str(x).upper() for x in products if str(x).strip()})),
        )


@dataclass
class Decision:
    profile_id: str
    entity_type: str
    entity_id: str
    action_type: str
    priority: int
    rule_id: str
    reason: str
    evidence: dict[str, Any]
    payload: dict[str, Any]
    expected_family: str
    risk: str = "medium"
    plan_key: str = field(default="")

    def __post_init__(self) -> None:
        if not self.plan_key:
            self.plan_key = _stable_key((self.profile_id, self.entity_type, self.entity_id, self.action_type, self.rule_id, self.payload))

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id, "entity_type": self.entity_type, "entity_id": self.entity_id,
            "action_type": self.action_type, "priority": self.priority, "rule_id": self.rule_id,
            "reason": self.reason, "evidence": self.evidence, "payload": self.payload,
            "expected_family": self.expected_family, "risk": self.risk, "plan_key": self.plan_key,
        }


@dataclass
class PlanResult:
    profile: dict[str, Any]
    window: dict[str, Any]
    kpis: dict[str, Any]
    data_quality: dict[str, Any]
    decisions: list[Decision]

    def as_dict(self) -> dict[str, Any]:
        return {"profile": self.profile, "window": self.window, "kpis": self.kpis,
                "data_quality": self.data_quality, "decisions": [x.as_dict() for x in self.decisions]}
