from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any

_INSTALLED = False
UTC = timezone.utc
_EXPLORE_NAME = re.compile(r"^HERMES-SP-EXP-", re.I)
_ACTIVE_DECISION_STATES = {"planned", "reserved", "executed", "pending", "uncertain", "verified"}


def _walk(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key), item
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)
    elif isinstance(value, str):
        text = value.strip()
        if text.startswith(("{", "[")):
            try:
                yield from _walk(json.loads(text))
            except (TypeError, ValueError, json.JSONDecodeError):
                pass


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _values(value: Any, *names: str) -> list[Any]:
    wanted = {_norm(name) for name in names}
    return [item for key, item in _walk(value) if _norm(key) in wanted]


def _first(value: Any, *names: str) -> Any:
    rows = _values(value, *names)
    return rows[0] if rows else None


def _number(value: Any, *names: str) -> float | None:
    for item in _values(value, *names):
        try:
            result = float(item)
        except (TypeError, ValueError):
            continue
        if result >= 0:
            return result
    return None


def _profile_matches(args: Any, profile_id: str) -> bool:
    return any(str(item).strip() == profile_id for item in _values(args, "profileId", "profile_id", "advertisingProfileId"))


def _campaign_rows(value: Any) -> list[dict[str, Any]] | None:
    if isinstance(value, dict):
        for key in ("campaigns", "campaignsList", "campaignsResponse", "items"):
            candidate = value.get(key)
            if isinstance(candidate, list) and any(
                isinstance(item, dict) and ("campaignId" in item or "campaign_id" in item or "id" in item)
                for item in candidate
            ):
                return [item for item in candidate if isinstance(item, dict)]
        for child in value.values():
            found = _campaign_rows(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        if value and any(isinstance(item, dict) and ("campaignId" in item or "campaign_id" in item) for item in value):
            return [item for item in value if isinstance(item, dict)]
        for child in value:
            found = _campaign_rows(child)
            if found is not None:
                return found
    elif isinstance(value, str):
        text = value.strip()
        if text.startswith(("{", "[")):
            try:
                return _campaign_rows(json.loads(text))
            except (TypeError, ValueError, json.JSONDecodeError):
                return None
    return None


def _campaign_budget(row: dict[str, Any]) -> float | None:
    direct = _number(row, "dailyBudget", "budgetAmount")
    if direct is not None:
        return direct
    budgets = row.get("budgets")
    if isinstance(budgets, list) and budgets:
        direct = _number(budgets[0], "value", "amount", "budget")
        if direct is not None:
            return direct
    value = row.get("budget")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and float(value) >= 0:
        return float(value)
    return None


def _fresh_live_exposure(store: Any, profile_id: str, max_age_seconds: int) -> dict[str, Any] | None:
    cutoff = datetime.now(UTC).timestamp() - max_age_seconds
    with store.connection() as conn:
        rows = conn.execute(
            "SELECT a.*,t.family FROM actions a LEFT JOIN mcp_tools t ON t.registered_name=a.tool_name "
            "WHERE a.phase='after' AND a.operation='read' AND a.allowed=1 AND a.structured_result=1 "
            "AND a.result_json IS NOT NULL AND t.family='campaign' ORDER BY a.id DESC LIMIT 50"
        ).fetchall()
    for row in rows:
        try:
            created = datetime.fromisoformat(str(row["created_at"] or ""))
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            if created.timestamp() < cutoff:
                continue
            args = json.loads(row["args_json"] or "{}")
            if not _profile_matches(args, profile_id):
                continue
            result = json.loads(row["result_json"] or "null")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        campaigns = _campaign_rows(result)
        if campaigns is None:
            continue
        budgets: dict[str, float] = {}
        for campaign in campaigns:
            campaign_id = str(_first(campaign, "campaignId", "campaign_id", "id") or "").strip()
            budget = _campaign_budget(campaign)
            if campaign_id and budget is not None:
                budgets[campaign_id] = max(0.0, budget)
        if budgets or not campaigns:
            return {
                "source": "fresh_amazon_campaign_read",
                "action_id": int(row["id"]),
                "observed_at": row["created_at"],
                "campaign_count": len(budgets),
                "campaign_budgets": budgets,
                "exposure": round(sum(budgets.values()), 2),
                "fresh": True,
            }
    return None


def _snapshot_exposure(store: Any, profile_id: str) -> dict[str, Any] | None:
    with store.connection() as conn:
        cycle = conn.execute(
            "SELECT c.id,c.created_at FROM cycles c JOIN metric_rows m ON m.cycle_id=c.id "
            "WHERE c.profile_id=? AND m.level='campaigns' ORDER BY c.created_at DESC LIMIT 1",
            (profile_id,),
        ).fetchone()
        if not cycle:
            return None
        rows = conn.execute(
            "SELECT entity_id,row_json FROM metric_rows WHERE cycle_id=? AND profile_id=? AND level='campaigns'",
            (cycle["id"], profile_id),
        ).fetchall()
    budgets: dict[str, float] = {}
    for row in rows:
        try:
            item = json.loads(row["row_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        campaign_id = str(row["entity_id"] or _first(item, "campaignId", "campaign_id", "id") or "").strip()
        budget = _campaign_budget(item)
        if campaign_id and budget is not None:
            budgets[campaign_id] = max(0.0, budget)
    return {
        "source": "latest_normalized_snapshot",
        "cycle_id": cycle["id"],
        "observed_at": cycle["created_at"],
        "campaign_count": len(budgets),
        "campaign_budgets": budgets,
        "exposure": round(sum(budgets.values()), 2),
        "fresh": False,
    }


def _payload(decision: dict[str, Any]) -> dict[str, Any]:
    return decision.get("payload") if isinstance(decision.get("payload"), dict) else {}


def _approved_args(decision: dict[str, Any]) -> dict[str, Any]:
    body = _payload(decision)
    args = body.get("approved_args")
    return args if isinstance(args, dict) else body


def _exploration(decision: dict[str, Any]) -> bool:
    body = _payload(decision)
    if body.get("exploration") is True or str(body.get("intent") or "").lower() == "exploration":
        return True
    name = str(_first(_approved_args(decision), "name", "campaignName") or "")
    return bool(_EXPLORE_NAME.match(name))


def _positive_budget_delta(decision: dict[str, Any], observed: dict[str, float] | None = None) -> float:
    action = str(decision.get("action_type") or "").lower()
    body = _payload(decision)
    args = _approved_args(decision)
    if action == "create_campaign":
        return max(0.0, _number(args, "budget", "dailyBudget", "budgetAmount") or 0.0)
    if action in {"increase_budget", "update_budget", "set_budget"} or str(body.get("field") or "").lower() == "budget":
        try:
            before = float(body.get("before"))
            after = float(body.get("after"))
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, after - before)
    if action in {"enable", "resume", "update_state"} and str(decision.get("expected_family") or "") == "campaign":
        state = str(_first(args, "state", "status") or body.get("after") or "").upper()
        if state != "ENABLED":
            return 0.0
        campaign_id = str(decision.get("entity_id") or "")
        if observed and campaign_id in observed:
            return max(0.0, float(observed[campaign_id]))
    return 0.0


def _committed_today(store: Any, profile_id: str, after_id: int | None = None) -> tuple[float, float]:
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    with store.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM decisions WHERE profile_id=? AND created_at>=? ORDER BY created_at,id",
            (profile_id, today),
        ).fetchall()
    total = 0.0
    exploration = 0.0
    for row in rows:
        item = store._decision_dict(row)
        if item.get("status") not in _ACTIVE_DECISION_STATES:
            continue
        delta = _positive_budget_delta(item)
        if delta <= 0:
            continue
        total += delta
        if _exploration(item):
            exploration += delta
    return round(total, 2), round(exploration, 2)


def budget_status(store: Any, profile_id: str | None = None, *, require_fresh: bool = False) -> dict[str, Any]:
    settings = store.get_settings()
    cap = float(settings.get("max_daily_ad_spend", 100.0))
    exploration_pct = float(settings.get("exploration_budget_pct", 20.0))
    soft_pct = float(settings.get("budget_guard_exploration_stop_pct", 80.0))
    conservative_pct = float(settings.get("budget_guard_conservative_pct", 90.0))
    max_age = int(settings.get("budget_guard_live_read_max_age_seconds", 900))
    if not profile_id:
        enabled = [row for row in store.list_profiles() if row and row.get("enabled")]
        profile_id = str(enabled[0].get("profile_id") or "") if len(enabled) == 1 else ""
    if not profile_id:
        return {
            "enabled": True,
            "profile_bound": False,
            "hard_cap": cap,
            "exploration_pct": exploration_pct,
            "fresh": False,
            "increase_allowed": False,
            "reason": "one enabled Profile is required to compute the daily budget envelope",
        }
    live = _fresh_live_exposure(store, profile_id, max_age)
    observation = live or _snapshot_exposure(store, profile_id)
    committed, exploration_committed = _committed_today(store, profile_id)
    base = float((observation or {}).get("exposure") or 0.0)
    projected = round(base + committed, 2)
    ratio = projected / cap * 100 if cap > 0 else 100.0
    exploration_cap = round(cap * exploration_pct / 100, 2)
    result = {
        "enabled": True,
        "profile_bound": True,
        "profile_id": profile_id,
        "hard_cap": round(cap, 2),
        "observed_exposure": round(base, 2),
        "committed_positive_delta_today": committed,
        "projected_exposure": projected,
        "remaining": round(max(0.0, cap - projected), 2),
        "utilization_pct": round(ratio, 2),
        "exploration_pct": exploration_pct,
        "exploration_cap": exploration_cap,
        "exploration_committed_today": exploration_committed,
        "exploration_remaining": round(max(0.0, exploration_cap - exploration_committed), 2),
        "exploration_stop_pct": soft_pct,
        "conservative_pct": conservative_pct,
        "fresh": bool(live),
        "observation": {key: value for key, value in (observation or {}).items() if key != "campaign_budgets"},
        "increase_allowed": bool(live) and projected < cap and ratio < conservative_pct,
        "exploration_allowed": bool(live) and projected < cap and ratio < soft_pct and exploration_committed < exploration_cap,
    }
    if require_fresh and not live:
        result["reason"] = "a fresh structured Amazon Campaign read is required before increasing exposure"
    elif projected >= cap:
        result["reason"] = "daily budget exposure hard cap reached"
    elif ratio >= conservative_pct:
        result["reason"] = "daily budget exposure entered conservative mode"
    elif ratio >= soft_pct:
        result["reason"] = "daily budget exposure stopped new exploration"
    else:
        result["reason"] = "within daily budget envelope"
    return result


def _plan_decisions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
    profile_id = str(profile.get("profile_id") or profile.get("id") or "")
    rows: list[dict[str, Any]] = []
    for index, action in enumerate(payload.get("actions") if isinstance(payload.get("actions"), list) else []):
        if not isinstance(action, dict):
            continue
        args = action.get("arguments") if isinstance(action.get("arguments"), dict) else {}
        rows.append({
            "profile_id": profile_id,
            "entity_id": str(action.get("entity_id") or ""),
            "expected_family": str(action.get("expected_family") or ""),
            "action_type": str(action.get("action_type") or ""),
            "payload": {
                "approved_args": args,
                "before": action.get("before"),
                "after": action.get("after"),
                "field": action.get("field"),
                "exploration": action.get("exploration") is True,
                "intent": action.get("intent"),
                "plan_index": index,
            },
        })
    return rows


def _configure_settings() -> None:
    from . import db

    db.DEFAULT_SETTINGS.update({
        "max_daily_ad_spend": 100.0,
        "exploration_budget_pct": 20.0,
        "budget_guard_exploration_stop_pct": 80.0,
        "budget_guard_conservative_pct": 90.0,
        "budget_guard_live_read_max_age_seconds": 900,
        "daily_budget_hard_cap_enabled": True,
    })
    db.BOOLEAN_SETTINGS.add("daily_budget_hard_cap_enabled")
    db.NUMERIC_SETTING_RANGES.update({
        "max_daily_ad_spend": (1.0, 1000000000.0),
        "exploration_budget_pct": (0.0, 100.0),
        "budget_guard_exploration_stop_pct": (50.0, 99.0),
        "budget_guard_conservative_pct": (50.0, 100.0),
    })
    db.INTEGER_SETTING_RANGES["budget_guard_live_read_max_age_seconds"] = (30, 86400)
    db.STRATEGY_SETTING_KEYS.update({"max_daily_ad_spend", "exploration_budget_pct"})
    db.SAFETY_LOCKED_SETTINGS["daily_budget_hard_cap_enabled"] = True


def _install_store() -> None:
    from .db import Store

    original_dashboard = Store.dashboard

    def dashboard(self):
        result = original_dashboard(self)
        profiles = [row for row in result.get("profiles", []) if row and row.get("enabled")]
        profile_id = str(profiles[0].get("profile_id") or "") if len(profiles) == 1 else None
        status = budget_status(self, profile_id)
        status.pop("profile_id", None)
        result["budget_guard"] = status
        return result

    Store.dashboard = dashboard


def _install_service() -> None:
    from .service import ControlService

    original_guard = ControlService._guardrail_check
    original_context = ControlService.context
    original_managed_plan = ControlService.create_managed_plan

    def guard(self, decision, tool, settings):
        allowed, reason = original_guard(self, decision, tool, settings)
        if not allowed:
            return allowed, reason
        delta = _positive_budget_delta(decision)
        if delta <= 0:
            return True, reason
        profile_id = str(decision.get("profile_id") or "")
        state = budget_status(self.store, profile_id, require_fresh=True)
        if not state.get("fresh"):
            return False, str(state.get("reason"))
        if _exploration(decision) and not state.get("exploration_allowed"):
            return False, str(state.get("reason"))
        if not state.get("increase_allowed"):
            return False, str(state.get("reason"))
        if float(state.get("projected_exposure") or 0) + delta > float(state.get("hard_cap") or 0) + 1e-9:
            return False, "planned write would exceed the account daily budget exposure hard cap"
        return True, reason + "; daily budget envelope verified"

    def context(self, session_id):
        result = original_context(self, session_id)
        task = result.get("task") if isinstance(result.get("task"), dict) else {}
        profile_id = ""
        if task:
            decisions = self.store.list_decisions(task_id=str(task.get("id") or ""), limit=1)
            profile_id = str(decisions[0].get("profile_id") or "") if decisions else ""
        if not profile_id:
            enabled = [row for row in self.store.list_profiles() if row and row.get("enabled")]
            profile_id = str(enabled[0].get("profile_id") or "") if len(enabled) == 1 else ""
        state = budget_status(self.store, profile_id or None)
        state.pop("profile_id", None)
        result["budget_guard"] = state
        result["instructions"] += (
            " Daily budget is a hard controller boundary. Historical performance evidence controls action size, not permission to explore. "
            "Before any exposure-increasing or exploratory managed plan, obtain a fresh Amazon Campaign read. "
            "Use small HERMES-SP-EXP-* PAUSED experiments inside the exploration pool; never exceed the hard cap."
        )
        return result

    def create_managed_plan(self, payload, actor="hermes-main"):
        profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
        profile_id = str(profile.get("profile_id") or profile.get("id") or "").strip()
        decisions = _plan_decisions(payload)
        positive = sum(_positive_budget_delta(item) for item in decisions)
        exploratory = sum(_positive_budget_delta(item) for item in decisions if _exploration(item))
        if positive > 0:
            state = budget_status(self.store, profile_id, require_fresh=True)
            if not state.get("fresh"):
                raise ValueError(str(state.get("reason")))
            if float(state.get("projected_exposure") or 0) + positive > float(state.get("hard_cap") or 0) + 1e-9:
                raise ValueError("managed plan would exceed the account daily budget exposure hard cap")
            if exploratory > 0:
                if not state.get("exploration_allowed"):
                    raise ValueError(str(state.get("reason")))
                if exploratory > float(state.get("exploration_remaining") or 0) + 1e-9:
                    raise ValueError("managed plan would exceed the daily exploration budget pool")
            elif not state.get("increase_allowed"):
                raise ValueError(str(state.get("reason")))
        return original_managed_plan(self, payload, actor)

    ControlService._guardrail_check = guard
    ControlService.context = context
    ControlService.create_managed_plan = create_managed_plan


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _configure_settings()
    _install_store()
    _install_service()
    _INSTALLED = True
