from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import math
from typing import Any, Iterable


def _f(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


@dataclass(frozen=True)
class DelayModel:
    """Cumulative advertising-attribution completion by click age in days."""

    cumulative: tuple[float, ...] = (0.58, 0.76, 0.87, 0.93, 0.97, 0.99, 1.0)
    floor: float = 0.35

    @classmethod
    def from_value(cls, value: Any) -> "DelayModel":
        if isinstance(value, dict):
            value = value.get("cumulative") or value.get("curve")
        if not isinstance(value, (list, tuple)) or not value:
            return cls()
        cleaned: list[float] = []
        last = 0.0
        for item in value:
            point = _clamp(_f(item), last, 1.0)
            cleaned.append(point)
            last = point
        if cleaned[-1] < 1.0:
            cleaned.append(1.0)
        return cls(tuple(cleaned))

    def maturity(self, age_days: float | int | None) -> float:
        if age_days is None:
            return 1.0
        age = max(0.0, _f(age_days))
        lower = int(math.floor(age))
        upper = int(math.ceil(age))
        if lower >= len(self.cumulative) - 1:
            return 1.0
        left = self.cumulative[lower]
        right = self.cumulative[min(upper, len(self.cumulative) - 1)]
        fraction = age - lower
        return _clamp(left + (right - left) * fraction, self.floor, 1.0)


@dataclass(frozen=True)
class PosteriorConfig:
    prior_clicks: float = 24.0
    prior_cvr: float = 0.08
    prior_aov_orders: float = 3.0
    default_aov: float = 30.0
    uncertainty_multiplier: float = 1.0


@dataclass(frozen=True)
class AcosPosterior:
    clicks: int
    observed_orders: float
    observed_sales: float
    spend: float
    maturity: float
    cvr_mean: float
    cvr_sd: float
    aov_mean: float
    aov_sd: float
    expected_final_orders: float
    expected_final_sales: float
    expected_acos: float | None
    acos_low: float | None
    acos_high: float | None
    p_acos_over_target: float
    p_acos_over_max: float
    p_acos_under_target: float
    confidence: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "clicks": self.clicks,
            "observed_orders": round(self.observed_orders, 4),
            "observed_sales": round(self.observed_sales, 4),
            "spend": round(self.spend, 4),
            "maturity": round(self.maturity, 4),
            "cvr_mean": round(self.cvr_mean, 6),
            "cvr_sd": round(self.cvr_sd, 6),
            "aov_mean": round(self.aov_mean, 4),
            "aov_sd": round(self.aov_sd, 4),
            "expected_final_orders": round(self.expected_final_orders, 4),
            "expected_final_sales": round(self.expected_final_sales, 4),
            "expected_acos": round(self.expected_acos, 4) if self.expected_acos is not None else None,
            "acos_interval": [
                round(self.acos_low, 4) if self.acos_low is not None else None,
                round(self.acos_high, 4) if self.acos_high is not None else None,
            ],
            "p_acos_over_target": round(self.p_acos_over_target, 6),
            "p_acos_over_max": round(self.p_acos_over_max, 6),
            "p_acos_under_target": round(self.p_acos_under_target, 6),
            "confidence": round(self.confidence, 6),
        }


def _prob_sales_below(threshold: float, mean: float, sd: float) -> float:
    if threshold <= 0:
        return 0.0
    if mean <= 0:
        return 1.0
    if sd <= 1e-9:
        return 1.0 if mean < threshold else 0.0
    return _clamp(normal_cdf((threshold - mean) / sd), 0.0, 1.0)


def estimate_acos_posterior(
    row: dict[str, Any],
    *,
    target_acos: float | Decimal,
    max_acos: float | Decimal,
    delay_model: DelayModel | None = None,
    age_days: float | int | None = None,
    config: PosteriorConfig | None = None,
    account_aov: float | None = None,
) -> AcosPosterior:
    cfg = config or PosteriorConfig()
    delay = delay_model or DelayModel()
    maturity = delay.maturity(age_days)
    clicks = max(0, int(_f(row.get("clicks"))))
    orders = max(0.0, _f(row.get("orders")))
    sales = max(0.0, _f(row.get("sales")))
    spend = max(0.0, _f(row.get("spend")))

    prior_clicks = max(0.01, cfg.prior_clicks)
    prior_cvr = _clamp(cfg.prior_cvr, 0.0001, 0.9999)
    alpha0 = prior_clicks * prior_cvr
    beta0 = prior_clicks * (1.0 - prior_cvr)
    alpha = alpha0 + orders
    beta = beta0 + max(0.0, clicks - orders)
    total = alpha + beta
    cvr_mean = alpha / total
    cvr_var = alpha * beta / (total * total * (total + 1.0))
    cvr_sd = math.sqrt(max(0.0, cvr_var)) * max(0.25, cfg.uncertainty_multiplier)

    observed_aov = sales / orders if orders > 0 and sales > 0 else 0.0
    prior_aov = max(0.01, _f(account_aov, cfg.default_aov))
    weight = max(0.01, cfg.prior_aov_orders)
    aov_mean = (prior_aov * weight + observed_aov * orders) / (weight + orders)
    # A bounded empirical uncertainty proxy. It intentionally remains wider for
    # zero/small-order entities and narrows as attributed orders accumulate.
    aov_cv = max(0.08, min(0.75, 0.60 / math.sqrt(max(1.0, weight + orders))))
    aov_sd = aov_mean * aov_cv * max(0.25, cfg.uncertainty_multiplier)

    expected_orders = clicks * cvr_mean
    expected_sales_raw = expected_orders * aov_mean
    # Observed conversions are incomplete inside the attribution window. Blend
    # the model expectation with maturity-corrected observed sales rather than
    # blindly multiplying the most recent report.
    maturity_corrected_sales = sales / maturity if sales > 0 else 0.0
    observed_weight = _clamp((orders + clicks / 20.0) / 12.0, 0.0, 0.8)
    expected_sales = observed_weight * maturity_corrected_sales + (1.0 - observed_weight) * expected_sales_raw
    expected_sales = max(0.0, expected_sales)

    revenue_var = (clicks * aov_mean) ** 2 * cvr_var + (expected_orders * aov_sd) ** 2
    revenue_sd = math.sqrt(max(1e-9, revenue_var)) / max(maturity, 0.35)
    expected_acos = spend * 100.0 / expected_sales if expected_sales > 1e-9 else None

    revenue_low = max(0.0, expected_sales - 1.645 * revenue_sd)
    revenue_high = max(0.0, expected_sales + 1.645 * revenue_sd)
    acos_low = spend * 100.0 / revenue_high if revenue_high > 1e-9 else None
    acos_high = spend * 100.0 / revenue_low if revenue_low > 1e-9 else None

    target = max(0.01, _f(target_acos, 30.0))
    maximum = max(target, _f(max_acos, 45.0))
    p_over_target = _prob_sales_below(spend * 100.0 / target, expected_sales, revenue_sd)
    p_over_max = _prob_sales_below(spend * 100.0 / maximum, expected_sales, revenue_sd)
    p_under_target = 1.0 - p_over_target
    effective_samples = clicks + orders * 8.0
    confidence = _clamp((1.0 - math.exp(-effective_samples / 28.0)) * maturity, 0.0, 1.0)

    return AcosPosterior(
        clicks=clicks,
        observed_orders=orders,
        observed_sales=sales,
        spend=spend,
        maturity=maturity,
        cvr_mean=cvr_mean,
        cvr_sd=cvr_sd,
        aov_mean=aov_mean,
        aov_sd=aov_sd,
        expected_final_orders=expected_orders,
        expected_final_sales=expected_sales,
        expected_acos=expected_acos,
        acos_low=acos_low,
        acos_high=acos_high,
        p_acos_over_target=p_over_target,
        p_acos_over_max=p_over_max,
        p_acos_under_target=p_under_target,
        confidence=confidence,
    )


def account_aov(snapshot: dict[str, Any], fallback: float = 30.0) -> float:
    account = snapshot.get("account") if isinstance(snapshot.get("account"), dict) else {}
    sales = max(0.0, _f(account.get("sales")))
    orders = max(0.0, _f(account.get("orders")))
    if orders > 0 and sales > 0:
        return sales / orders
    candidates: list[float] = []
    for level in ("targets", "campaigns", "search_terms", "placements"):
        rows = snapshot.get(level)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_orders = max(0.0, _f(row.get("orders")))
            row_sales = max(0.0, _f(row.get("sales")))
            if row_orders > 0 and row_sales > 0:
                candidates.append(row_sales / row_orders)
    if not candidates:
        return fallback
    candidates.sort()
    return candidates[len(candidates) // 2]


def cumulative_delay_from_rows(rows: Iterable[dict[str, Any]]) -> DelayModel:
    buckets: dict[int, list[float]] = {}
    for row in rows:
        try:
            day = max(0, int(row.get("age_days") or row.get("day") or 0))
            cumulative = _f(row.get("cumulative_fraction") or row.get("maturity"))
        except (TypeError, ValueError):
            continue
        if 0 < cumulative <= 1:
            buckets.setdefault(day, []).append(cumulative)
    if not buckets:
        return DelayModel()
    maximum = max(buckets)
    curve: list[float] = []
    last = 0.0
    for day in range(maximum + 1):
        values = buckets.get(day)
        point = sum(values) / len(values) if values else last
        point = _clamp(point, last, 1.0)
        curve.append(point)
        last = point
    return DelayModel.from_value(curve)
