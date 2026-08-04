from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from typing import Any, Iterable

UTC = timezone.utc
TRUSTED_SOURCES = {"amazon-ads-mcp", "amazon-ads-api", "amazon-ads-report", "amazon-marketing-stream"}


def _d(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    try:
        parsed = Decimal(str(value))
        return parsed if parsed.is_finite() else Decimal(default)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


def _q(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    return numerator / denominator if denominator > 0 else None


def _stable_key(parts: Iterable[Any]) -> str:
    encoded = "|".join(str(part) for part in parts).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]


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
    max_bid_change_pct: Decimal = Decimal("20")
    max_budget_change_pct: Decimal = Decimal("25")
    attribution_lag_days: int = 2
    min_window_days: int = 7
    max_data_age_days: int = 7
    allow_bid_changes: bool = True
    allow_budget_changes: bool = True
    allow_negatives: bool = True
    allow_harvest: bool = True
    allow_placement_changes: bool = True
    allow_campaign_creation: bool = False
    allow_official_recommendation_apply: bool = False
    recommendation_types: tuple[str, ...] = ("BID", "BUDGET", "KEYWORD", "TARGET")

    @classmethod
    def from_mapping(cls, value: dict[str, Any] | None) -> "StrategyPolicy":
        data = value or {}
        recommendation_types = data.get("recommendation_types", cls.recommendation_types)
        if isinstance(recommendation_types, str):
            recommendation_types = [part.strip() for part in recommendation_types.split(",") if part.strip()]
        return cls(
            target_acos=_d(data.get("target_acos"), "30"),
            max_acos=_d(data.get("max_acos"), "45"),
            min_clicks=int(data.get("min_clicks", 8)),
            min_orders=int(data.get("min_orders", 2)),
            min_spend=_d(data.get("min_spend"), "10"),
            waste_clicks=int(data.get("waste_clicks", 12)),
            waste_spend=_d(data.get("waste_spend"), "20"),
            harvest_orders=int(data.get("harvest_orders", 2)),
            harvest_max_acos=_d(data.get("harvest_max_acos"), str(data.get("target_acos", 30))),
            bid_increase_pct=_d(data.get("bid_increase_pct"), "10"),
            bid_decrease_pct=_d(data.get("bid_decrease_pct"), "12"),
            severe_bid_decrease_pct=_d(data.get("severe_bid_decrease_pct"), "20"),
            budget_increase_pct=_d(data.get("budget_increase_pct"), "15"),
            max_bid_change_pct=_d(data.get("max_bid_change_pct"), "20"),
            max_budget_change_pct=_d(data.get("max_budget_change_pct"), "25"),
            attribution_lag_days=int(data.get("attribution_lag_days", 2)),
            min_window_days=int(data.get("min_window_days", 7)),
            max_data_age_days=int(data.get("max_data_age_days", 7)),
            allow_bid_changes=bool(data.get("allow_bid_changes", True)),
            allow_budget_changes=bool(data.get("allow_budget_changes", True)),
            allow_negatives=bool(data.get("allow_negatives", True)),
            allow_harvest=bool(data.get("allow_harvest", True)),
            allow_placement_changes=bool(data.get("allow_placement_changes", True)),
            allow_campaign_creation=bool(data.get("allow_campaign_creation", False)),
            allow_official_recommendation_apply=bool(data.get("allow_official_recommendation_apply", False)),
            recommendation_types=tuple(str(item).upper() for item in recommendation_types),
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
            "profile_id": self.profile_id,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "action_type": self.action_type,
            "priority": self.priority,
            "rule_id": self.rule_id,
            "reason": self.reason,
            "evidence": self.evidence,
            "payload": self.payload,
            "expected_family": self.expected_family,
            "risk": self.risk,
            "plan_key": self.plan_key,
        }


@dataclass
class PlanResult:
    profile: dict[str, Any]
    window: dict[str, Any]
    kpis: dict[str, Any]
    data_quality: dict[str, Any]
    decisions: list[Decision]

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "window": self.window,
            "kpis": self.kpis,
            "data_quality": self.data_quality,
            "decisions": [decision.as_dict() for decision in self.decisions],
        }


class OptimizationEngine:
    """Deterministic sponsored-ads optimizer.

    Hermes/LLMs collect and explain data; this engine decides from explicit inputs and rules.
    """

    def plan(self, snapshot: dict[str, Any], policy: StrategyPolicy) -> PlanResult:
        profile = snapshot.get("profile") if isinstance(snapshot.get("profile"), dict) else {}
        profile_id = str(profile.get("profile_id") or profile.get("id") or "").strip()
        if not profile_id:
            raise ValueError("snapshot.profile.profile_id is required")
        window = snapshot.get("window") if isinstance(snapshot.get("window"), dict) else {}
        quality = self._quality(snapshot, policy)
        kpis = self._kpis(snapshot)
        decisions: list[Decision] = []
        if quality["eligible_for_writes"]:
            decisions.extend(self._target_decisions(profile_id, snapshot.get("targets", []), policy))
            decisions.extend(self._search_term_decisions(profile_id, snapshot.get("search_terms", []), policy))
            decisions.extend(self._budget_decisions(profile_id, snapshot.get("campaigns", []), snapshot.get("budget_usage", []), policy))
            decisions.extend(self._placement_decisions(profile_id, snapshot.get("placements", []), policy))
            decisions.extend(self._recommendation_decisions(profile_id, snapshot.get("recommendations", []), policy))
        decisions = self._dedupe(decisions)
        return PlanResult(profile=profile, window=window, kpis=kpis, data_quality=quality, decisions=decisions)

    def _quality(self, snapshot: dict[str, Any], policy: StrategyPolicy) -> dict[str, Any]:
        window = snapshot.get("window") if isinstance(snapshot.get("window"), dict) else {}
        start = str(window.get("start") or "")
        end = str(window.get("end") or "")
        try:
            declared_days = int(window.get("days") or 0)
        except (TypeError, ValueError):
            declared_days = 0
        days = declared_days
        source = str(snapshot.get("source") or "unknown").strip().lower()
        missing: list[str] = []
        end_age_days = None
        mature = False
        if source not in TRUSTED_SOURCES:
            missing.append("untrusted_source")
        if start and end:
            try:
                start_date = datetime.fromisoformat(start).date()
                end_date = datetime.fromisoformat(end).date()
                if start_date > end_date:
                    missing.append("window_start_after_end")
                derived_days = max(0, (end_date - start_date).days + 1)
                if declared_days and declared_days != derived_days:
                    missing.append("window_days_mismatch")
                days = derived_days
                end_age_days = (datetime.now(UTC).date() - end_date).days
                mature = end_age_days >= policy.attribution_lag_days
            except ValueError:
                missing.append("invalid_window_date")
                days = 0
        if not start or not end:
            missing.append("window")
        if days < policy.min_window_days:
            missing.append("window_too_short")
        if not mature:
            missing.append("attribution_not_mature")
        if end_age_days is not None and end_age_days > policy.max_data_age_days:
            missing.append("data_too_stale")
        targets = snapshot.get("targets", [])
        if not isinstance(targets, list):
            missing.append("targets")
        else:
            target_ids = [
                str(row.get("target_id") or row.get("keyword_id") or row.get("id") or "")
                for row in targets if isinstance(row, dict)
            ]
            nonempty = [item for item in target_ids if item]
            if len(nonempty) != len(set(nonempty)):
                missing.append("duplicate_target_ids")
        account = snapshot.get("account") if isinstance(snapshot.get("account"), dict) else {}
        for field in ("impressions", "clicks", "spend", "sales", "orders"):
            raw = account.get(field)
            if raw is not None and (_d(raw) < 0 or str(raw).strip().lower() in {"nan", "inf", "infinity", "-inf", "-infinity"}):
                missing.append(f"invalid_account_{field}")
        eligible = not missing
        return {
            "source": source,
            "window_days": days,
            "attribution_mature": mature,
            "end_age_days": end_age_days,
            "missing_or_unsafe": sorted(set(missing)),
            "eligible_for_writes": eligible,
            "row_counts": {
                key: len(snapshot.get(key, [])) if isinstance(snapshot.get(key), list) else 0
                for key in ("campaigns", "targets", "search_terms", "placements", "recommendations", "hourly")
            },
        }

    def _kpis(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        account = snapshot.get("account") if isinstance(snapshot.get("account"), dict) else {}
        spend = _d(account.get("spend"))
        sales = _d(account.get("sales"))
        orders = _d(account.get("orders"))
        clicks = _d(account.get("clicks"))
        impressions = _d(account.get("impressions"))
        acos = _ratio(spend * 100, sales)
        roas = _ratio(sales, spend)
        ctr = _ratio(clicks * 100, impressions)
        cvr = _ratio(orders * 100, clicks)
        cpc = _ratio(spend, clicks)
        return {
            "spend": _q(spend), "sales": _q(sales), "orders": int(orders),
            "clicks": int(clicks), "impressions": int(impressions),
            "acos": _q(acos) if acos is not None else None,
            "roas": _q(roas) if roas is not None else None,
            "ctr": _q(ctr) if ctr is not None else None,
            "cvr": _q(cvr) if cvr is not None else None,
            "cpc": _q(cpc) if cpc is not None else None,
        }

    def _target_decisions(self, profile_id: str, rows: Any, policy: StrategyPolicy) -> list[Decision]:
        if not isinstance(rows, list) or not policy.allow_bid_changes:
            return []
        out: list[Decision] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            entity_id = str(row.get("target_id") or row.get("keyword_id") or row.get("id") or "")
            state = str(row.get("state") or row.get("status") or "ENABLED").upper()
            if state not in {"ENABLED", "ACTIVE"}:
                continue
            bid = _d(row.get("bid"))
            clicks = int(_d(row.get("clicks")))
            spend = _d(row.get("spend"))
            sales = _d(row.get("sales"))
            orders = int(_d(row.get("orders")))
            if not entity_id or bid <= 0 or clicks < policy.min_clicks or spend < policy.min_spend:
                continue
            acos = _ratio(spend * 100, sales)
            evidence = self._evidence(row, acos)
            if orders == 0 and clicks >= policy.waste_clicks and spend >= policy.waste_spend:
                pct = min(policy.severe_bid_decrease_pct, policy.max_bid_change_pct)
                after = bid * (Decimal("1") - pct / 100)
                out.append(self._bid_decision(profile_id, entity_id, bid, after, "ADS-TARGET-WASTE", 95,
                                              "成熟窗口内高点击高花费且无订单，降低竞价控制浪费", evidence, pct))
            elif orders >= policy.min_orders and acos is not None and acos > policy.max_acos:
                severity = min(policy.max_bid_change_pct, max(policy.bid_decrease_pct, (acos - policy.target_acos) / max(Decimal("1"), policy.target_acos) * 10))
                after = bid * (Decimal("1") - severity / 100)
                out.append(self._bid_decision(profile_id, entity_id, bid, after, "ADS-TARGET-OVER-ACOS", 85,
                                              "目标有订单但 ACOS 超出上限，按受控幅度降低竞价", evidence, severity))
            elif orders >= policy.min_orders and acos is not None and acos <= policy.target_acos * Decimal("0.75"):
                pct = min(policy.bid_increase_pct, policy.max_bid_change_pct)
                after = bid * (Decimal("1") + pct / 100)
                out.append(self._bid_decision(profile_id, entity_id, bid, after, "ADS-TARGET-SCALE", 70,
                                              "目标转化稳定且 ACOS 明显优于目标，受控提高竞价争取流量", evidence, pct))
        return out

    def _bid_decision(self, profile_id: str, entity_id: str, before: Decimal, after: Decimal,
                      rule_id: str, priority: int, reason: str, evidence: dict[str, Any], pct: Decimal) -> Decision:
        return Decision(
            profile_id=profile_id, entity_type="target", entity_id=entity_id,
            action_type="update_bid", priority=priority, rule_id=rule_id, reason=reason,
            evidence=evidence,
            payload={
                "entity_id": entity_id, "field": "bid", "before": _q(before), "after": _q(max(Decimal("0.02"), after)),
                "change_percent": _q(pct),
                "match_fields": {"target_id|targetId|keyword_id|keywordId": entity_id, "bid": _q(max(Decimal("0.02"), after))},
                "expected_state": {"bid": _q(max(Decimal("0.02"), after))},
            },
            expected_family="target", risk="medium",
        )

    def _search_term_decisions(self, profile_id: str, rows: Any, policy: StrategyPolicy) -> list[Decision]:
        if not isinstance(rows, list):
            return []
        out: list[Decision] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            term = str(row.get("search_term") or row.get("query") or "").strip()
            source_target = str(row.get("target_id") or row.get("keyword_id") or "")
            campaign_id = str(row.get("campaign_id") or "")
            ad_group_id = str(row.get("ad_group_id") or "")
            clicks = int(_d(row.get("clicks")))
            spend = _d(row.get("spend"))
            sales = _d(row.get("sales"))
            orders = int(_d(row.get("orders")))
            if not term or not campaign_id or not ad_group_id:
                continue
            acos = _ratio(spend * 100, sales)
            evidence = self._evidence(row, acos)
            if policy.allow_negatives and orders == 0 and clicks >= policy.waste_clicks and spend >= policy.waste_spend:
                out.append(Decision(
                    profile_id=profile_id, entity_type="search_term", entity_id=term,
                    action_type="add_negative_exact", priority=92, rule_id="ADS-SEARCH-NEGATIVE",
                    reason="成熟窗口内搜索词持续消耗且无订单，添加否定精准阻止继续浪费",
                    evidence=evidence,
                    payload={
                        "campaign_id": campaign_id, "ad_group_id": ad_group_id, "search_term": term,
                        "match_type": "NEGATIVE_EXACT",
                        "match_fields": {
                            "campaign_id|campaignId": campaign_id, "ad_group_id|adGroupId": ad_group_id,
                            "search_term|searchTerm|keyword_text|keywordText|negative_keyword_text|negativeKeywordText": term,
                            "match_type|matchType": "NEGATIVE_EXACT",
                        },
                        "expected_state": {"search_term": term, "match_type": "NEGATIVE_EXACT", "state": "ENABLED"},
                    }, expected_family="target", risk="medium",
                ))
            if (policy.allow_harvest and orders >= policy.harvest_orders and acos is not None
                    and acos <= policy.harvest_max_acos and not bool(row.get("already_exact"))):
                out.append(Decision(
                    profile_id=profile_id, entity_type="search_term", entity_id=term,
                    action_type="harvest_exact_keyword", priority=78, rule_id="ADS-SEARCH-HARVEST",
                    reason="搜索词已有稳定订单且 ACOS 达标，将其收割为独立精准关键词以获得可控竞价",
                    evidence=evidence,
                    payload={
                        "campaign_id": campaign_id, "ad_group_id": ad_group_id, "source_target_id": source_target,
                        "keyword_text": term, "match_type": "EXACT", "suggested_bid": row.get("suggested_bid") or row.get("cpc"),
                        "match_fields": {
                            "campaign_id|campaignId": campaign_id, "ad_group_id|adGroupId": ad_group_id,
                            "keyword_text|keywordText": term, "match_type|matchType": "EXACT",
                        },
                        "expected_state": {"keyword_text": term, "match_type": "EXACT", "state": "ENABLED"},
                    }, expected_family="target", risk="medium",
                ))
        return out

    def _budget_decisions(self, profile_id: str, campaigns: Any, usage_rows: Any, policy: StrategyPolicy) -> list[Decision]:
        if not policy.allow_budget_changes or not isinstance(campaigns, list):
            return []
        usage_by_id = {}
        if isinstance(usage_rows, list):
            usage_by_id = {str(row.get("campaign_id") or row.get("campaignId")): row for row in usage_rows if isinstance(row, dict)}
        out: list[Decision] = []
        for row in campaigns:
            if not isinstance(row, dict):
                continue
            campaign_id = str(row.get("campaign_id") or row.get("id") or "")
            state = str(row.get("state") or row.get("status") or "ENABLED").upper()
            if state not in {"ENABLED", "ACTIVE"}:
                continue
            budget = _d(row.get("budget"))
            spend = _d(row.get("spend"))
            sales = _d(row.get("sales"))
            orders = int(_d(row.get("orders")))
            usage = usage_by_id.get(campaign_id, {})
            usage_pct = _d(usage.get("budget_usage_percent") or usage.get("budgetUsagePercent") or row.get("budget_usage_percent"))
            acos = _ratio(spend * 100, sales)
            if campaign_id and budget > 0 and usage_pct >= 90 and orders >= policy.min_orders and acos is not None and acos <= policy.target_acos:
                pct = min(policy.budget_increase_pct, policy.max_budget_change_pct)
                after = budget * (Decimal("1") + pct / 100)
                out.append(Decision(
                    profile_id=profile_id, entity_type="campaign", entity_id=campaign_id,
                    action_type="increase_budget", priority=82, rule_id="ADS-BUDGET-PACING-WINNER",
                    reason="广告活动预算接近耗尽且 ACOS 达标，增加预算减少高质量流量损失",
                    evidence={**self._evidence(row, acos), "budget_usage_percent": _q(usage_pct)},
                    payload={
                        "entity_id": campaign_id, "field": "budget", "before": _q(budget), "after": _q(after),
                        "change_percent": _q(pct),
                        "match_fields": {"campaign_id|campaignId": campaign_id, "budget": _q(after)},
                        "expected_state": {"budget": _q(after)},
                    }, expected_family="campaign", risk="medium",
                ))
        return out

    def _placement_decisions(self, profile_id: str, rows: Any, policy: StrategyPolicy) -> list[Decision]:
        if not policy.allow_placement_changes or not isinstance(rows, list):
            return []
        out: list[Decision] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            campaign_id = str(row.get("campaign_id") or "")
            placement = str(row.get("placement") or "").upper()
            clicks = int(_d(row.get("clicks")))
            spend = _d(row.get("spend"))
            sales = _d(row.get("sales"))
            orders = int(_d(row.get("orders")))
            current = _d(row.get("adjustment_percent"))
            acos = _ratio(spend * 100, sales)
            if (campaign_id and placement in {"PLACEMENT_TOP", "TOP_OF_SEARCH"} and clicks >= policy.min_clicks
                    and orders >= policy.min_orders and acos is not None and acos <= policy.target_acos * Decimal("0.8")):
                after = min(Decimal("900"), current + Decimal("10"))
                if after > current:
                    out.append(Decision(
                        profile_id=profile_id, entity_type="campaign", entity_id=campaign_id,
                        action_type="update_placement", priority=60, rule_id="ADS-PLACEMENT-TOS-SCALE",
                        reason="首页顶部广告位转化充分且 ACOS 显著优于目标，小幅提高广告位系数",
                        evidence={**self._evidence(row, acos), "placement": placement},
                        payload={
                            "entity_id": campaign_id, "placement": "PLACEMENT_TOP", "field": "percentage",
                            "before": _q(current), "after": _q(after), "change_percent": 10,
                            "match_fields": {
                                "campaign_id|campaignId": campaign_id, "placement": "PLACEMENT_TOP",
                                "percentage|adjustment_percent|adjustmentPercent": _q(after),
                            },
                            "expected_state": {"placement": "PLACEMENT_TOP", "percentage": _q(after)},
                        }, expected_family="campaign", risk="medium",
                    ))
        return out

    def _recommendation_decisions(self, profile_id: str, rows: Any, policy: StrategyPolicy) -> list[Decision]:
        # Official recommendations are valuable evidence, but they are not blindly
        # executable. Operators must explicitly opt in after validating the tool
        # contract and expected state for their account.
        if not policy.allow_official_recommendation_apply or not isinstance(rows, list):
            return []
        out: list[Decision] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            rec_type = str(row.get("type") or row.get("recommendation_type") or "").upper()
            if rec_type not in policy.recommendation_types:
                continue
            rec_id = str(row.get("recommendation_id") or row.get("id") or "")
            entity_id = str(row.get("entity_id") or row.get("campaign_id") or row.get("target_id") or rec_id)
            expires = row.get("expires_at") or row.get("expiry_date")
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            expected_state = row.get("expected_state") if isinstance(row.get("expected_state"), dict) else {}
            if not rec_id or not entity_id or not expected_state:
                continue
            out.append(Decision(
                profile_id=profile_id, entity_type="recommendation", entity_id=entity_id,
                action_type="apply_amazon_recommendation", priority=55, rule_id="ADS-OFFICIAL-RECOMMENDATION",
                reason="Amazon Ads 官方推荐进入可审计决策队列；执行前仍受本地目标和风险边界约束",
                evidence={"recommendation_id": rec_id, "type": rec_type, "expires_at": expires, "source": "amazon_ads"},
                payload={
                    "recommendation_id": rec_id, "recommendation_type": rec_type, "provider_payload": payload,
                    "match_fields": {"recommendation_id|recommendationId": rec_id},
                    "expected_state": expected_state,
                },
                expected_family="recommendation", risk="medium",
            ))
        return out

    @staticmethod
    def _evidence(row: dict[str, Any], acos: Decimal | None) -> dict[str, Any]:
        spend = _d(row.get("spend")); sales = _d(row.get("sales")); clicks = int(_d(row.get("clicks")))
        orders = int(_d(row.get("orders"))); impressions = int(_d(row.get("impressions")))
        return {
            "impressions": impressions, "clicks": clicks, "spend": _q(spend), "sales": _q(sales), "orders": orders,
            "acos": _q(acos) if acos is not None else None,
            "cpc": _q(_ratio(spend, Decimal(clicks))) if clicks else None,
            "cvr": _q(_ratio(Decimal(orders) * 100, Decimal(clicks))) if clicks else None,
        }

    @staticmethod
    def _dedupe(decisions: list[Decision]) -> list[Decision]:
        # Higher-priority action wins per entity/action family. Exact plan keys remain deterministic.
        best: dict[tuple[str, str, str, str, str], Decision] = {}
        for decision in decisions:
            payload = decision.payload if isinstance(decision.payload, dict) else {}
            key = (
                decision.entity_type, decision.entity_id, decision.action_type,
                str(payload.get("campaign_id") or ""), str(payload.get("ad_group_id") or ""),
            )
            if key not in best or decision.priority > best[key].priority:
                best[key] = decision
        return sorted(best.values(), key=lambda item: (-item.priority, item.entity_type, item.entity_id))
