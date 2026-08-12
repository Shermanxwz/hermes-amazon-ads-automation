from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from .strategy_core import (
    ACTIVE_STATES, PLACEMENTS, TRUSTED_SOURCES, UTC, Decision, PlanResult, StrategyPolicy,
    _bool, _clamp, _d, _dt, _i, _parse_decimal, _q, _ratio,
)


class OptimizationEngine:
    """Confidence-aware, ads-only deterministic optimizer."""

    def plan(self, snapshot: dict[str, Any], policy: StrategyPolicy) -> PlanResult:
        profile = snapshot.get("profile") if isinstance(snapshot.get("profile"), dict) else {}
        profile_id = str(profile.get("profile_id") or profile.get("id") or "").strip()
        if not profile_id:
            raise ValueError("snapshot.profile.profile_id is required")
        quality = self._quality(snapshot, policy)
        decisions: list[Decision] = []
        if quality["eligible_for_writes"]:
            rejected = quality["rejected_rows"]
            decisions += self._targets(profile_id, snapshot.get("targets", []), policy, rejected.get("targets", {}))
            decisions += self._search_terms(profile_id, snapshot.get("search_terms", []), policy, rejected.get("search_terms", {}))
            decisions += self._budgets(profile_id, snapshot.get("campaigns", []), snapshot.get("budget_usage", []), policy, rejected.get("campaigns", {}))
            decisions += self._placements(profile_id, snapshot.get("placements", []), policy, rejected.get("placements", {}))
            decisions += self._recommendations(profile_id, snapshot.get("recommendations", []), policy)
        decisions = self._dedupe(decisions)
        candidates = len(decisions)
        decisions = decisions[:policy.max_decisions_per_cycle]
        quality["strategy_summary"] = {
            "mode": "confidence-aware", "objective": "target_acos", "candidate_decisions": candidates,
            "emitted_decisions": len(decisions), "suppressed_by_cycle_limit": candidates - len(decisions),
        }
        return PlanResult(profile, snapshot.get("window") if isinstance(snapshot.get("window"), dict) else {},
                          self._kpis(snapshot), quality, decisions)

    def _quality(self, snapshot: dict[str, Any], p: StrategyPolicy) -> dict[str, Any]:
        window = snapshot.get("window") if isinstance(snapshot.get("window"), dict) else {}
        start, end, declared = str(window.get("start") or ""), str(window.get("end") or ""), _i(window.get("days"))
        source, unsafe, warnings = str(snapshot.get("source") or "unknown").strip().lower(), [], []
        days, age, mature = declared, None, False
        if source not in TRUSTED_SOURCES:
            unsafe.append("untrusted_source")
        if start and end:
            try:
                a, b = datetime.fromisoformat(start).date(), datetime.fromisoformat(end).date()
                if a > b:
                    unsafe.append("window_start_after_end")
                days = max(0, (b - a).days + 1)
                if declared and declared != days:
                    unsafe.append("window_days_mismatch")
                age = (datetime.now(UTC).date() - b).days
                mature = age >= p.attribution_lag_days
            except ValueError:
                unsafe.append("invalid_window_date")
                days = 0
        else:
            unsafe.append("window")
        if days < p.min_window_days:
            unsafe.append("window_too_short")
        if not mature:
            unsafe.append("attribution_not_mature")
        if age is not None and age > p.max_data_age_days:
            unsafe.append("data_too_stale")
        if age is not None and age < 0:
            unsafe.append("window_in_future")
        account = snapshot.get("account") if isinstance(snapshot.get("account"), dict) else {}
        for name in ("impressions", "clicks", "spend", "sales", "orders"):
            if name in account and ((_parse_decimal(account[name]) is None) or _d(account[name]) < 0):
                unsafe.append(f"invalid_account_{name}")
        targets = snapshot.get("targets", [])
        if not isinstance(targets, list):
            unsafe.append("targets")
            targets = []
        else:
            ids = [str(x.get("target_id") or x.get("keyword_id") or x.get("id") or "") for x in targets if isinstance(x, dict)]
            ids = [x for x in ids if x]
            if len(ids) != len(set(ids)):
                unsafe.append("duplicate_target_ids")
        specs = {
            "targets": (targets, ("clicks", "spend", "sales", "orders", "bid")),
            "search_terms": (snapshot.get("search_terms", []), ("clicks", "spend", "sales", "orders")),
            "campaigns": (snapshot.get("campaigns", []), ("spend", "sales", "orders", "budget")),
            "placements": (snapshot.get("placements", []), ("clicks", "spend", "sales", "orders", "adjustment_percent")),
        }
        rejected: dict[str, dict[str, list[str]]] = {}
        for name, (rows, metrics) in specs.items():
            if not isinstance(rows, list):
                unsafe.append(name)
                continue
            bad: dict[str, list[str]] = {}
            for i, row in enumerate(rows):
                reasons: list[str] = []
                if not isinstance(row, dict):
                    reasons.append("row_not_object")
                else:
                    for metric in metrics:
                        if metric in row:
                            value = _parse_decimal(row[metric])
                            if value is None:
                                reasons.append(f"invalid_{metric}")
                            elif value < 0:
                                reasons.append(f"negative_{metric}")
                    if _parse_decimal(row.get("orders")) is not None and _parse_decimal(row.get("clicks")) is not None and _d(row.get("orders")) > _d(row.get("clicks")):
                        reasons.append("orders_exceed_clicks")
                    if _parse_decimal(row.get("clicks")) is not None and _parse_decimal(row.get("impressions")) is not None and _d(row.get("clicks")) > _d(row.get("impressions")):
                        reasons.append("clicks_exceed_impressions")
                if reasons:
                    bad[str(i)] = sorted(set(reasons))
            rejected[name] = bad
            if bad:
                warnings.append(f"{name}_rows_rejected")
        overlap = self._overlap(targets)
        if overlap:
            warnings.append("overlapping_exact_targets")
        return {
            "source": source, "window_days": days, "attribution_mature": mature, "end_age_days": age,
            "missing_or_unsafe": sorted(set(unsafe)), "warnings": sorted(set(warnings)),
            "eligible_for_writes": not unsafe, "rejected_rows": rejected, "overlapping_exact_targets": overlap,
            "row_counts": {k: len(snapshot.get(k, [])) if isinstance(snapshot.get(k), list) else 0
                           for k in ("campaigns", "targets", "search_terms", "placements", "recommendations", "hourly")},
        }

    @staticmethod
    def _kpis(snapshot: dict[str, Any]) -> dict[str, Any]:
        a = snapshot.get("account") if isinstance(snapshot.get("account"), dict) else {}
        spend, sales, orders, clicks, impressions = (_d(a.get(x)) for x in ("spend", "sales", "orders", "clicks", "impressions"))
        acos, roas, ctr, cvr, cpc = (_ratio(spend * 100, sales), _ratio(sales, spend), _ratio(clicks * 100, impressions),
                                     _ratio(orders * 100, clicks), _ratio(spend, clicks))
        return {"spend": _q(spend), "sales": _q(sales), "orders": int(orders), "clicks": int(clicks),
                "impressions": int(impressions), "acos": _q(acos) if acos is not None else None,
                "roas": _q(roas) if roas is not None else None, "ctr": _q(ctr) if ctr is not None else None,
                "cvr": _q(cvr) if cvr is not None else None, "cpc": _q(cpc) if cpc is not None else None}

    def _targets(self, profile: str, rows: Any, p: StrategyPolicy, rejected: dict[str, list[str]]) -> list[Decision]:
        if not isinstance(rows, list) or not p.allow_bid_changes:
            return []
        overlap, out = self._overlap(rows), []
        for i, row in enumerate(rows):
            if str(i) in rejected or not isinstance(row, dict):
                continue
            entity = str(row.get("target_id") or row.get("keyword_id") or row.get("id") or "")
            if str(row.get("state") or row.get("status") or "ENABLED").upper() not in ACTIVE_STATES:
                continue
            bid, clicks, spend, sales, orders = _d(row.get("bid")), _i(row.get("clicks")), _d(row.get("spend")), _d(row.get("sales")), _i(row.get("orders"))
            if not entity or bid <= 0 or clicks < p.min_clicks or spend < p.min_spend:
                continue
            life = self._life(row, p)
            if life["cooldown_active"]:
                continue
            acos, confidence = _ratio(spend * 100, sales), self._confidence(clicks, orders, spend, p)
            evidence = {**self._evidence(row, acos), **life, "confidence": _q(confidence)}
            if orders == 0 and clicks >= p.waste_clicks and spend >= p.waste_spend and confidence >= p.min_confidence_to_reduce:
                pct = min(p.severe_bid_decrease_pct, p.max_bid_change_pct)
                out.append(self._bid(profile, entity, bid, bid * (1 - pct / 100), pct, "ADS-TARGET-WASTE", 95,
                                     "成熟窗口内高点击高花费且无订单，按置信度降低竞价控制浪费", evidence, p))
            elif orders >= p.min_orders and acos is not None and acos > p.max_acos and confidence >= p.min_confidence_to_reduce:
                pct = min(p.max_bid_change_pct, max(p.bid_decrease_pct, (1 - _clamp(p.target_acos / acos, Decimal("0.1"), Decimal("1"))) * 100))
                out.append(self._bid(profile, entity, bid, bid * (1 - pct / 100), pct, "ADS-TARGET-OVER-ACOS", 85,
                                     "目标有成熟订单但 ACOS 超出上限，按目标差距和置信度降低竞价", evidence, p))
            elif orders >= p.min_orders and acos is not None and acos <= p.target_acos * Decimal(".75") and confidence >= p.min_confidence_to_scale and life["state"] in {"stable", "scale"}:
                identity = self._identity(row)
                if identity and len(overlap.get(identity, [])) > 1:
                    continue
                cap = bid * (1 + min(p.bid_increase_pct, p.max_bid_change_pct) / 100)
                target = self._target_cpc(clicks, sales, orders, p)
                after = min(cap, max(bid, target))
                if after > bid:
                    evidence["target_cpc"] = _q(target)
                    out.append(self._bid(profile, entity, bid, after, (after / bid - 1) * 100, "ADS-TARGET-SCALE", 70,
                                         "目标转化稳定且 ACOS 显著优于目标，按目标 CPC 小步扩量", evidence, p))
        return out

    @staticmethod
    def _bid(profile: str, entity: str, before: Decimal, after: Decimal, pct: Decimal, rule: str, priority: int,
             reason: str, evidence: dict[str, Any], p: StrategyPolicy) -> Decision:
        after = _clamp(after, p.min_bid, p.max_bid)
        return Decision(profile, "target", entity, "update_bid", priority, rule, reason, evidence,
                        {"entity_id": entity, "field": "bid", "before": _q(before), "after": _q(after),
                         "change_percent": _q(abs(pct)), "match_fields": {"target_id|targetId|keyword_id|keywordId": entity, "bid": _q(after)},
                         "expected_state": {"bid": _q(after)}, "strategy_context": {"confidence": evidence.get("confidence"),
                         "lifecycle": evidence.get("state"), "cooldown_hours": p.cooldown_hours}}, "target")

    def _search_terms(self, profile: str, rows: Any, p: StrategyPolicy, rejected: dict[str, list[str]]) -> list[Decision]:
        if not isinstance(rows, list):
            return []
        out = []
        for i, row in enumerate(rows):
            if str(i) in rejected or not isinstance(row, dict):
                continue
            term, campaign, group = str(row.get("search_term") or row.get("query") or "").strip(), str(row.get("campaign_id") or ""), str(row.get("ad_group_id") or "")
            clicks, spend, sales, orders = _i(row.get("clicks")), _d(row.get("spend")), _d(row.get("sales")), _i(row.get("orders"))
            if not term or not campaign or not group:
                continue
            life = self._life(row, p)
            if life["cooldown_active"]:
                continue
            acos, confidence = _ratio(spend * 100, sales), self._confidence(clicks, orders, spend, p)
            evidence = {**self._evidence(row, acos), **life, "confidence": _q(confidence)}
            if p.allow_negatives and orders == 0 and clicks >= p.waste_clicks and spend >= p.waste_spend and confidence >= p.min_confidence_to_reduce:
                out.append(Decision(profile, "search_term", term, "add_negative_exact", 92, "ADS-SEARCH-NEGATIVE",
                    "成熟窗口内搜索词持续消耗且无订单，添加否定精准阻止继续浪费", evidence,
                    {"campaign_id": campaign, "ad_group_id": group, "search_term": term, "match_type": "NEGATIVE_EXACT",
                     "match_fields": {"campaign_id|campaignId": campaign, "ad_group_id|adGroupId": group,
                     "search_term|searchTerm|keyword_text|keywordText|negative_keyword_text|negativeKeywordText": term,
                     "match_type|matchType": "NEGATIVE_EXACT"},
                     "expected_state": {"search_term": term, "match_type": "NEGATIVE_EXACT", "state": "ENABLED"}}, "target"))
            if p.allow_harvest and orders >= p.harvest_orders and acos is not None and acos <= p.harvest_max_acos and confidence >= p.min_confidence_to_scale and not _bool(row.get("already_exact"), False):
                target = self._target_cpc(clicks, sales, orders, p)
                bid = _parse_decimal(row.get("suggested_bid")) or (_ratio(spend, Decimal(clicks)) if clicks else None) or target
                bid = _clamp(bid, p.min_bid, p.max_bid)
                out.append(Decision(profile, "search_term", term, "harvest_exact_keyword", 78, "ADS-SEARCH-HARVEST",
                    "搜索词已有成熟订单且 ACOS 达标，先创建并验证精准词，再允许来源否定",
                    {**evidence, "target_cpc": _q(target)},
                    {"campaign_id": campaign, "ad_group_id": group, "source_target_id": str(row.get("target_id") or row.get("keyword_id") or ""),
                     "keyword_text": term, "match_type": "EXACT", "suggested_bid": _q(bid),
                     "migration": {"phase": "create_and_verify", "negative_source_only_after_verified": True, "rollback": "keep_source_traffic"},
                     "match_fields": {"campaign_id|campaignId": campaign, "ad_group_id|adGroupId": group,
                     "keyword_text|keywordText": term, "match_type|matchType": "EXACT"},
                     "expected_state": {"keyword_text": term, "match_type": "EXACT", "state": "ENABLED"}}, "target"))
        return out

    def _budgets(self, profile: str, rows: Any, usage_rows: Any, p: StrategyPolicy, rejected: dict[str, list[str]]) -> list[Decision]:
        if not p.allow_budget_changes or not isinstance(rows, list):
            return []
        usage = {str(x.get("campaign_id") or x.get("campaignId")): x for x in usage_rows if isinstance(x, dict)} if isinstance(usage_rows, list) else {}
        out = []
        for i, row in enumerate(rows):
            if str(i) in rejected or not isinstance(row, dict) or str(row.get("state") or row.get("status") or "ENABLED").upper() not in ACTIVE_STATES:
                continue
            entity, budget = str(row.get("campaign_id") or row.get("id") or ""), _d(row.get("budget"))
            spend, sales, orders, clicks = _d(row.get("spend")), _d(row.get("sales")), _i(row.get("orders")), _i(row.get("clicks"))
            used = _d((usage.get(entity) or {}).get("budget_usage_percent") or (usage.get(entity) or {}).get("budgetUsagePercent") or row.get("budget_usage_percent"))
            life = self._life(row, p)
            if life["cooldown_active"]:
                continue
            acos, confidence = _ratio(spend * 100, sales), self._confidence(clicks, orders, spend, p)
            evidence = {**self._evidence(row, acos), **life, "confidence": _q(confidence), "budget_usage_percent": _q(used)}
            if entity and budget > 0 and used >= 90 and orders >= p.min_orders and acos is not None and acos <= p.target_acos and confidence >= p.min_confidence_to_scale:
                pct = min(p.budget_increase_pct, p.max_budget_change_pct)
                out.append(self._budget(profile, entity, budget, _clamp(budget * (1 + pct / 100), p.min_budget, p.max_budget),
                                        pct, "ADS-BUDGET-PACING-WINNER", 82, "预算接近耗尽且 ACOS 达标，提高预算避免优质流量中断", evidence))
            elif p.allow_budget_decreases and entity and budget > p.min_budget and used >= 70 and orders >= p.min_orders and acos is not None and acos > p.max_acos and confidence >= p.min_confidence_to_reduce:
                pct = min(p.budget_decrease_pct, p.max_budget_change_pct)
                out.append(self._budget(profile, entity, budget, _clamp(budget * (1 - pct / 100), p.min_budget, p.max_budget),
                                        pct, "ADS-BUDGET-CONTAIN-LOSS", 88, "广告活动高消耗且 ACOS 超限，收缩预算以限制继续放大低效流量", evidence))
        return out

    @staticmethod
    def _budget(profile: str, entity: str, before: Decimal, after: Decimal, pct: Decimal, rule: str,
                priority: int, reason: str, evidence: dict[str, Any]) -> Decision:
        return Decision(profile, "campaign", entity, "increase_budget" if after > before else "decrease_budget", priority, rule,
                        reason, evidence, {"entity_id": entity, "field": "budget", "before": _q(before), "after": _q(after),
                        "change_percent": _q(abs(pct)), "match_fields": {"campaign_id|campaignId": entity, "budget": _q(after)},
                        "expected_state": {"budget": _q(after)}}, "campaign")

    def _placements(self, profile: str, rows: Any, p: StrategyPolicy, rejected: dict[str, list[str]]) -> list[Decision]:
        if not p.allow_placement_changes or not isinstance(rows, list):
            return []
        out = []
        for i, row in enumerate(rows):
            if str(i) in rejected or not isinstance(row, dict):
                continue
            entity, placement = str(row.get("campaign_id") or ""), PLACEMENTS.get(str(row.get("placement") or "").upper(), str(row.get("placement") or "").upper())
            clicks, spend, sales, orders, current = _i(row.get("clicks")), _d(row.get("spend")), _d(row.get("sales")), _i(row.get("orders")), _d(row.get("adjustment_percent"))
            life = self._life(row, p)
            if life["cooldown_active"]:
                continue
            acos, confidence = _ratio(spend * 100, sales), self._confidence(clicks, orders, spend, p)
            evidence = {**self._evidence(row, acos), **life, "confidence": _q(confidence), "placement": placement}
            if entity and placement in set(PLACEMENTS.values()) and clicks >= p.min_clicks and orders >= p.min_orders and acos is not None and acos <= p.target_acos * Decimal(".8") and confidence >= p.min_confidence_to_scale:
                points, after = min(p.placement_increase_points, p.max_placement_change_points), min(Decimal("900"), current + min(p.placement_increase_points, p.max_placement_change_points))
                if after > current:
                    rule = "ADS-PLACEMENT-TOS-SCALE" if placement == "PLACEMENT_TOP" else "ADS-PLACEMENT-SCALE"
                    out.append(self._placement(profile, entity, placement, current, after, points, rule, 60,
                                               "广告位转化充分且 ACOS 显著优于目标，小幅提高广告位系数", evidence))
            elif entity and current > 0 and confidence >= p.min_confidence_to_reduce and ((orders >= p.min_orders and acos is not None and acos > p.max_acos) or (orders == 0 and clicks >= p.waste_clicks and spend >= p.waste_spend)):
                points, after = min(p.placement_decrease_points, p.max_placement_change_points), max(Decimal("0"), current - min(p.placement_decrease_points, p.max_placement_change_points))
                if after < current:
                    out.append(self._placement(profile, entity, placement, current, after, points, "ADS-PLACEMENT-REDUCE", 84,
                                               "广告位 ACOS 超限或持续无订单，降低系数而不误伤整个 Campaign", evidence))
        return out

    @staticmethod
    def _placement(profile: str, entity: str, placement: str, before: Decimal, after: Decimal, points: Decimal,
                   rule: str, priority: int, reason: str, evidence: dict[str, Any]) -> Decision:
        return Decision(profile, "campaign", entity, "update_placement", priority, rule, reason, evidence,
                        {"entity_id": entity, "placement": placement, "field": "percentage", "before": _q(before), "after": _q(after),
                         "change_percent": _q(abs(points)), "match_fields": {"campaign_id|campaignId": entity,
                         "placement": placement, "percentage|adjustment_percent|adjustmentPercent": _q(after)},
                         "expected_state": {"placement": placement, "percentage": _q(after)}}, "campaign")

    @staticmethod
    def _recommendations(profile: str, rows: Any, p: StrategyPolicy) -> list[Decision]:
        if not p.allow_official_recommendation_apply or not isinstance(rows, list):
            return []
        out = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            kind = str(row.get("type") or row.get("recommendation_type") or "").upper()
            rec = str(row.get("recommendation_id") or row.get("id") or "")
            entity = str(row.get("entity_id") or row.get("campaign_id") or row.get("target_id") or rec)
            expected = row.get("expected_state") if isinstance(row.get("expected_state"), dict) else {}
            if kind not in p.recommendation_types or not rec or not entity or not expected:
                continue
            out.append(Decision(profile, "recommendation", entity, "apply_amazon_recommendation", 55,
                "ADS-OFFICIAL-RECOMMENDATION", "Amazon Ads 官方推荐仅在显式启用且具备可验证预期状态时进入队列",
                {"recommendation_id": rec, "type": kind, "expires_at": row.get("expires_at") or row.get("expiry_date"), "source": "amazon_ads"},
                {"recommendation_id": rec, "recommendation_type": kind,
                 "provider_payload": row.get("payload") if isinstance(row.get("payload"), dict) else {},
                 "match_fields": {"recommendation_id|recommendationId": rec}, "expected_state": expected}, "recommendation"))
        return out

    @staticmethod
    def _evidence(row: dict[str, Any], acos: Decimal | None) -> dict[str, Any]:
        spend, sales, clicks, orders, impressions = _d(row.get("spend")), _d(row.get("sales")), _i(row.get("clicks")), _i(row.get("orders")), _i(row.get("impressions"))
        return {"impressions": impressions, "clicks": clicks, "spend": _q(spend), "sales": _q(sales), "orders": orders,
                "acos": _q(acos) if acos is not None else None, "cpc": _q(_ratio(spend, Decimal(clicks))) if clicks else None,
                "cvr": _q(_ratio(Decimal(orders) * 100, Decimal(clicks))) if clicks else None}

    @staticmethod
    def _confidence(clicks: int, orders: int, spend: Decimal, p: StrategyPolicy) -> Decimal:
        cs = min(Decimal("1"), Decimal(clicks) / Decimal(max(p.waste_clicks, p.min_clicks, 1)))
        os = min(Decimal("1"), Decimal(orders) / Decimal(max(p.stable_min_orders, 1)))
        denominator = max(p.waste_spend, p.min_spend)
        ss = min(Decimal("1"), spend / denominator) if denominator > 0 else Decimal("1")
        if orders == 0:
            return _clamp(cs * Decimal(".65") + ss * Decimal(".35"), Decimal("0"), Decimal("1"))
        if clicks <= 0:
            return _clamp(os * Decimal(".70") + ss * Decimal(".30"), Decimal("0"), Decimal("1"))
        return _clamp(cs * Decimal(".35") + os * Decimal(".45") + ss * Decimal(".20"), Decimal("0"), Decimal("1"))

    @staticmethod
    def _target_cpc(clicks: int, sales: Decimal, orders: int, p: StrategyPolicy) -> Decimal:
        if clicks <= 0 or sales <= 0 or orders <= 0:
            return p.min_bid
        return max(p.min_bid, p.target_acos / 100 * (Decimal(orders) / clicks) * (sales / orders))

    @staticmethod
    def _life(row: dict[str, Any], p: StrategyPolicy) -> dict[str, Any]:
        explicit, clicks, orders = str(row.get("lifecycle_state") or "").strip().lower(), _i(row.get("clicks")), _i(row.get("orders"))
        state = explicit if explicit in {"explore", "learning", "stable", "scale", "declining", "recovery"} else (
            "scale" if orders >= p.scale_min_orders else "stable" if orders >= p.stable_min_orders else
            "learning" if clicks >= p.learning_min_clicks else "explore")
        changed = _dt(row.get("last_changed_at")) or _dt(row.get("last_bid_change_at")) or _dt(row.get("last_budget_change_at")) or _dt(row.get("last_placement_change_at"))
        until = changed + timedelta(hours=p.cooldown_hours) if changed else None
        return {"state": state, "last_changed_at": changed.isoformat() if changed else None,
                "cooldown_until": until.isoformat() if until else None, "cooldown_active": bool(until and datetime.now(UTC) < until)}

    @staticmethod
    def _identity(row: dict[str, Any]) -> str:
        text = row.get("keyword_text") or row.get("targeting_text") or row.get("expression") or row.get("asin") or ""
        match = str(row.get("match_type") or row.get("matchType") or "").upper()
        return f"{match}:{' '.join(str(text).strip().lower().split())}" if text and match in {"EXACT", "TARGETING_EXPRESSION"} else ""

    def _overlap(self, rows: Any) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = {}
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict):
                    identity = self._identity(row)
                    entity = str(row.get("target_id") or row.get("keyword_id") or row.get("id") or "")
                    if identity and entity:
                        grouped.setdefault(identity, []).append(entity)
        return {k: v for k, v in grouped.items() if len(v) > 1}

    @staticmethod
    def _dedupe(decisions: list[Decision]) -> list[Decision]:
        best: dict[tuple[str, str, str, str, str], Decision] = {}
        for d in decisions:
            payload = d.payload if isinstance(d.payload, dict) else {}
            family = "update_budget" if d.action_type in {"increase_budget", "decrease_budget"} else d.action_type
            key = (d.entity_type, d.entity_id, family, str(payload.get("campaign_id") or ""), str(payload.get("ad_group_id") or ""))
            if key not in best or d.priority > best[key].priority:
                best[key] = d
        return sorted(best.values(), key=lambda x: (-x.priority, x.entity_type, x.entity_id, x.plan_key))
