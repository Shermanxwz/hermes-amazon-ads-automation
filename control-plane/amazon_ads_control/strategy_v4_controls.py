from __future__ import annotations

from typing import Any

from .strategy_core import Decision, _d
from .strategy_v4_policy import StrategyPolicy
from .sealed_envelope import envelope_hash
from .strategy_v4_support import posterior


def _marker(profile: str, policy: StrategyPolicy, purpose: str, state: str) -> dict[str, Any]:
    return {"version": 1, "validated": True, "scope": "sealed-sp", "profile_id": profile,
        "ad_product": "SPONSORED_PRODUCTS", "observed_in_ads": True, "purpose": purpose,
        "desired_state": state, "envelope_hash": envelope_hash(profile, policy)}

def lifecycle(snapshot: dict[str, Any], policy: StrategyPolicy, ctx, age: Any) -> list[Decision]:
    rows = snapshot.get("targets")
    if not isinstance(rows, list):
        return []
    profile = str(snapshot.get("profile", {}).get("profile_id") or "")
    delay, cfg, aov = ctx
    out: list[Decision] = []
    for row in rows:
        if not isinstance(row, dict) or str(row.get("ad_product") or "SPONSORED_PRODUCTS").upper() != "SPONSORED_PRODUCTS":
            continue
        entity = str(row.get("target_id") or row.get("keyword_id") or row.get("id") or "")
        state = str(row.get("state") or row.get("status") or "ENABLED").upper()
        if not entity:
            continue
        post = posterior(row, snapshot, policy, delay, cfg, aov, age)
        desired = None
        if state == "ENABLED" and post.p_acos_over_max >= 0.985 and post.confidence >= 0.72 and post.spend >= float(policy.waste_spend) * 2:
            desired, rule, priority, purpose = "PAUSED", "ADS-LIFECYCLE-QUARANTINE", 99, "risk_quarantine"
        elif state == "PAUSED" and bool(row.get("recovery_ready")) and post.p_acos_under_target >= 0.92 and post.confidence >= 0.65:
            desired, rule, priority, purpose = "ENABLED", "ADS-LIFECYCLE-RECOVERY", 74, "verified_recovery"
        if not desired:
            continue
        marker = _marker(profile, policy, purpose, desired)
        if desired == "ENABLED":
            marker["verified_create"] = True
        out.append(Decision(profile, "target", entity, "enable" if desired == "ENABLED" else "pause", priority, rule,
            "后验 ACOS 与生命周期状态满足可逆隔离或恢复条件", {"posterior_acos": post.as_dict(), "lifecycle": purpose},
            {"entity_id": entity, "field": "state", "before": state, "after": desired, "ad_product": "SPONSORED_PRODUCTS",
             "standing_authorization": marker, "match_fields": {"target_id|targetId|keyword_id|keywordId": entity, "state": desired},
             "expected_state": {"state": desired}}, "target", risk="high"))
    return out


def global_budget(engine, snapshot: dict[str, Any], policy: StrategyPolicy, ctx, age: Any, decisions: list[Decision]) -> list[Decision]:
    rows, usage_rows = snapshot.get("campaigns"), snapshot.get("budget_usage")
    if not isinstance(rows, list) or len(rows) < 2:
        return decisions
    usage = {str(x.get("campaign_id") or x.get("campaignId") or ""): x for x in usage_rows or [] if isinstance(x, dict)}
    delay, cfg, aov = ctx
    scored = []
    for row in rows:
        if not isinstance(row, dict) or str(row.get("state") or "ENABLED").upper() != "ENABLED" or _d(row.get("budget")) <= 0:
            continue
        post = posterior(row, snapshot, policy, delay, cfg, aov, age)
        scored.append((float(policy.target_acos) / max(0.01, post.expected_acos or 1e9), row, post))
    if len(scored) < 2:
        return decisions
    scored.sort(key=lambda x: x[0])
    loser, winner = scored[0], scored[-1]
    wid, lid = str(winner[1].get("campaign_id") or winner[1].get("id") or ""), str(loser[1].get("campaign_id") or loser[1].get("id") or "")
    wb, lb = _d(winner[1].get("budget")), _d(loser[1].get("budget"))
    wu = _d((usage.get(wid) or {}).get("budget_usage_percent") or winner[1].get("budget_usage_percent"))
    lu = _d((usage.get(lid) or {}).get("budget_usage_percent") or loser[1].get("budget_usage_percent"))
    pct = min(policy.budget_increase_pct, policy.budget_decrease_pct, policy.max_budget_change_pct)
    transfer = min(wb * pct / 100, lb * pct / 100)
    if not (wid and lid and transfer > 0 and wu >= 80 and lu >= 55 and winner[2].p_acos_under_target >= float(policy.posterior_scale_probability) and loser[2].p_acos_over_max >= float(policy.posterior_reduce_probability)):
        return decisions
    kept = [d for d in decisions if d.action_type not in {"increase_budget", "decrease_budget"}]
    profile = str(snapshot.get("profile", {}).get("profile_id") or "")
    kept.append(engine._budget(profile, wid, wb, wb + transfer, transfer / wb * 100, "ADS-GLOBAL-BUDGET-ALLOCATE-WINNER", 91,
        "将预算从高风险 Campaign 等额转移到达标且接近耗尽的 Campaign", {"posterior_acos": winner[2].as_dict(), "paired_campaign_id": lid}))
    kept.append(engine._budget(profile, lid, lb, lb - transfer, transfer / lb * 100, "ADS-GLOBAL-BUDGET-ALLOCATE-LOSER", 92,
        "全局预算重分配释放低效预算，不增加账户总暴露", {"posterior_acos": loser[2].as_dict(), "paired_campaign_id": wid}))
    return kept
