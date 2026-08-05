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


def _lognormal_parameters(mean: float, sd: float) -> tuple[float, float]:
    mean = max(mean, 1e-9)
    cv = _clamp(sd / mean, 0.06, 1.5)
    sigma2 = math.log1p(cv * cv)
    return math.log(mean) - sigma2 / 2.0, math.sqrt(sigma2)


def _prob_sales_below(threshold: float, mean: float, sd: float) -> float:
    if threshold <= 0:
        return 0.0
    if mean <= 0:
        return 1.0
    mu, sigma = _lognormal_parameters(mean, sd)
    return _clamp(normal_cdf((math.log(threshold) - mu) / sigma), 0.0, 1.0)


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

    # Reported orders and sales are incomplete until the attribution window
    # matures. Correct the observed sufficient statistics before combining them
    # with hierarchical priors; this prevents premature cuts on recent clicks.
    final_orders_observed = orders / max(maturity, 0.35)
    final_sales_observed = sales / max(maturity, 0.35)

    prior_clicks = max(0.01, cfg.prior_clicks)
    prior_cvr = _clamp(cfg.prior_cvr, 0.0001, 0.9999)
    alpha = prior_clicks * prior_cvr + final_orders_observed
    beta = prior_clicks * (1.0 - prior_cvr) + max(0.0, clicks - final_orders_observed)
    total = alpha + beta
    cvr_mean = alpha / total
    cvr_var = alpha * beta / (total * total * (total + 1.0))
    cvr_sd = math.sqrt(max(0.0, cvr_var)) * max(0.25, cfg.uncertainty_multiplier)

    prior_aov = max(0.01, _f(account_aov, cfg.default_aov))
    observed_aov = final_sales_observed / final_orders_observed if final_orders_observed > 0 and final_sales_observed > 0 else 0.0
    aov_weight = max(0.01, cfg.prior_aov_orders)
    aov_mean = (prior_aov * aov_weight + observed_aov * final_orders_observed) / (aov_weight + final_orders_observed)
    aov_cv = _clamp(0.55 / math.sqrt(max(1.0, aov_weight + final_orders_observed)), 0.08, 0.55)
    aov_sd = aov_mean * aov_cv * max(0.25, cfg.uncertainty_multiplier)

    expected_orders = clicks * cvr_mean
    model_sales = expected_orders * aov_mean
    observed_weight = _clamp(
        (final_orders_observed + clicks / 20.0) /
        (aov_weight + final_orders_observed + clicks / 20.0),
        0.0,
        0.9,
    )
    expected_sales = max(0.0, observed_weight * final_sales_observed + (1.0 - observed_weight) * model_sales)

    # Beta-binomial predictive variance includes uncertainty in the CVR itself.
    order_var = clicks * cvr_mean * (1.0 - cvr_mean) + clicks * max(0, clicks - 1) * cvr_var
    revenue_var = order_var * aov_mean * aov_mean + expected_orders * aov_sd * aov_sd
    revenue_sd = math.sqrt(max(1e-9, revenue_var))
    # Evidence narrows uncertainty gradually but never removes it completely.
    evidence = clicks + orders * 8.0
    revenue_sd *= max(0.65, 1.0 - min(0.35, evidence / 300.0))

    expected_acos = spend * 100.0 / expected_sales if expected_sales > 1e-9 else None
    mu, sigma = _lognormal_parameters(expected_sales, revenue_sd)
    revenue_low = math.exp(mu - 1.645 * sigma)
    revenue_high = math.exp(mu + 1.645 * sigma)
    acos_low = spend * 100.0 / revenue_high if revenue_high > 1e-9 else None
    acos_high = spend * 100.0 / revenue_low if revenue_low > 1e-9 else None

    target = max(0.01, _f(target_acos, 30.0))
    maximum = max(target, _f(max_acos, 45.0))
    p_over_target = _prob_sales_below(spend * 100.0 / target, expected_sales, revenue_sd)
    p_over_max = _prob_sales_below(spend * 100.0 / maximum, expected_sales, revenue_sd)
    p_under_target = 1.0 - p_over_target
    confidence = _clamp((1.0 - math.exp(-evidence / 28.0)) * maturity, 0.0, 1.0)

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
