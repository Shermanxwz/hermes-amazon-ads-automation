from __future__ import annotations

from decimal import Decimal
from typing import Any

from .probabilistic_acos import DelayModel, PosteriorConfig, account_aov, estimate_acos_posterior
from .strategy_core import Decision, _clamp, _d, _i, _q
from .strategy_v4_policy import StrategyPolicy

REDUCE = {"ADS-TARGET-WASTE", "ADS-TARGET-OVER-ACOS", "ADS-BUDGET-CONTAIN-LOSS", "ADS-PLACEMENT-REDUCE", "ADS-SEARCH-NEGATIVE"}
SCALE = {"ADS-TARGET-SCALE", "ADS-BUDGET-PACING-WINNER", "ADS-PLACEMENT-TOS-SCALE", "ADS-PLACEMENT-SCALE", "ADS-SEARCH-HARVEST"}


def context(snapshot: dict[str, Any], policy: StrategyPolicy):
    delay = DelayModel(tuple(float(x) for x in policy.delay_curve))
    cfg = PosteriorConfig(
        prior_clicks=float(policy.posterior_prior_clicks),
        prior_cvr=float(policy.posterior_prior_cvr_pct / 100),
        prior_aov_orders=float(policy.posterior_prior_aov_orders),
        default_aov=float(policy.posterior_default_aov),
    )
    return delay, cfg, account_aov(snapshot, float(policy.posterior_default_aov))


def row_index(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for level in ("targets", "campaigns", "search_terms", "placements"):
        rows = snapshot.get(level)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            for key in ("target_id", "keyword_id", "campaign_id", "id", "search_term", "query"):
                if row.get(key) not in (None, ""):
                    result[f"{level}:{row[key]}"] = row
    return result


def decision_row(decision: Decision, index: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    levels = {"target": ("targets",), "campaign": ("campaigns", "placements"), "search_term": ("search_terms",)}
    for level in levels.get(decision.entity_type, ("targets", "campaigns", "search_terms", "placements")):
        row = index.get(f"{level}:{decision.entity_id}")
        if row:
            return row
    payload = decision.payload if isinstance(decision.payload, dict) else {}
    campaign = str(payload.get("campaign_id") or "")
    return index.get(f"campaigns:{campaign}") or index.get(f"placements:{campaign}")


def posterior(row: dict[str, Any], snapshot: dict[str, Any], policy: StrategyPolicy, delay, cfg, aov, age):
    return estimate_acos_posterior(
        row, target_acos=policy.target_acos, max_acos=policy.max_acos,
        delay_model=delay, age_days=row.get("attribution_age_days", age), config=cfg, account_aov=aov,
    )


def gate(decisions: list[Decision], snapshot: dict[str, Any], policy: StrategyPolicy, age: Any):
    delay, cfg, aov = context(snapshot, policy)
    index = row_index(snapshot)
    kept: list[Decision] = []
    suppressed: list[dict[str, Any]] = []
    for item in decisions:
        row = decision_row(item, index)
        if not row:
            kept.append(item)
            continue
        post = posterior(row, snapshot, policy, delay, cfg, aov, age)
        item.evidence = {**item.evidence, "posterior_acos": post.as_dict(), "decision_os": "v4"}
        ok = True
        if item.rule_id in REDUCE:
            ok = post.p_acos_over_max >= float(policy.posterior_reduce_probability) and post.confidence >= float(policy.posterior_min_confidence)
        elif item.rule_id in SCALE:
            ok = post.p_acos_under_target >= float(policy.posterior_scale_probability) and post.confidence >= float(policy.posterior_min_confidence)
        if ok:
            kept.append(item)
        else:
            suppressed.append({"plan_key": item.plan_key, "rule_id": item.rule_id, "entity_id": item.entity_id})
    return kept, suppressed, (delay, cfg, aov)


def hourly(engine, snapshot: dict[str, Any], policy: StrategyPolicy, ctx) -> list[Decision]:
    rows, targets = snapshot.get("hourly"), snapshot.get("targets")
    if not isinstance(rows, list) or not isinstance(targets, list):
        return []
    campaigns: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        cid, hour = str(row.get("campaign_id") or ""), _i(row.get("hour"), -1)
        if not cid or hour not in range(24):
            continue
        item = campaigns.setdefault(cid, {"spend": Decimal("0"), "sales": Decimal("0"), "orders": 0, "clicks": 0, "hour": hour, "budget": _d(row.get("budget"))})
        for key in ("spend", "sales"):
            item[key] += _d(row.get(key))
        for key in ("orders", "clicks"):
            item[key] += _i(row.get(key))
        item["hour"] = max(item["hour"], hour)
        item["budget"] = max(item["budget"], _d(row.get("budget")))
    delay, cfg, aov = ctx
    out: list[Decision] = []
    for row in targets:
        if not isinstance(row, dict) or str(row.get("state") or "ENABLED").upper() != "ENABLED":
            continue
        cid, entity, bid = str(row.get("campaign_id") or ""), str(row.get("target_id") or row.get("keyword_id") or row.get("id") or ""), _d(row.get("bid"))
        pace = campaigns.get(cid)
        if not pace or not entity or bid <= 0 or pace["budget"] <= 0:
            continue
        ratio = (pace["spend"] / pace["budget"]) / max(Decimal("0.02"), Decimal(max(1, pace["hour"] + 1)) / 24)
        post = posterior(pace, snapshot, policy, delay, cfg, aov, 0)
        pct = min(policy.hourly_max_bid_change_pct, policy.max_bid_change_pct)
        if ratio >= policy.hourly_overpace_ratio and post.p_acos_over_max >= float(policy.posterior_reduce_probability):
            after, rule, priority = _clamp(bid * (1 - pct / 100), policy.min_bid, policy.max_bid), "ADS-INTRADAY-PACE-DOWN", 89
        elif ratio <= policy.hourly_underpace_ratio and post.p_acos_under_target >= float(policy.posterior_scale_probability):
            after, rule, priority = _clamp(bid * (1 + pct / 100), policy.min_bid, policy.max_bid), "ADS-INTRADAY-PACE-UP", 66
        else:
            continue
        out.append(engine._bid(str(snapshot.get("profile", {}).get("profile_id") or ""), entity, bid, after, pct, rule, priority,
            "小时级预算节奏与后验 ACOS 联合控制，使用可逆小幅竞价调整", {"pace_ratio": _q(ratio), "hour": pace["hour"], "posterior_acos": post.as_dict(), "state": "stable", "confidence": post.confidence}, policy))
    return out
