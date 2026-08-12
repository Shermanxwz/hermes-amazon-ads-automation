from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any

_INSTALLED = False
UTC = timezone.utc
_EXPLORE_NAME = re.compile(r"^HERMES-SP-EXP-", re.I)


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


def _time(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _profile_matches(args: Any, profile_id: str) -> bool:
    return any(
        str(item).strip() == profile_id
        for item in _values(args, "profileId", "profile_id", "advertisingProfileId")
    )


def _campaign_rows(value: Any) -> list[dict[str, Any]] | None:
    if isinstance(value, dict):
        for key in ("campaigns", "campaignsList", "campaignsResponse", "items"):
            candidate = value.get(key)
            if isinstance(candidate, list):
                if not candidate:
                    return []
                if any(
                    isinstance(item, dict)
                    and ("campaignId" in item or "campaign_id" in item or "id" in item)
                    for item in candidate
                ):
                    return [item for item in candidate if isinstance(item, dict)]
        for child in value.values():
            found = _campaign_rows(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        if not value:
            return []
        if any(
            isinstance(item, dict) and ("campaignId" in item or "campaign_id" in item or "id" in item)
            for item in value
        ):
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
    value = row.get("budget")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and float(value) >= 0:
        return float(value)
    budgets = row.get("budgets")
    if isinstance(budgets, list) and budgets:
        nested = _number(budgets[0], "value", "amount", "budget")
        if nested is not None:
            return nested
    return None


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


def _positive_budget_delta(decision: dict[str, Any]) -> float:
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
    return 0.0


def budget_status(store: Any, profile_id: str | None = None, *, require_fresh: bool = False) -> dict[str, Any]:
    """Bootstrap fallback.

    budget_reservation installs the sealed same-day-spend implementation after
    this extension. Keeping a fail-closed fallback makes import/order failures
    safe rather than silently reverting to a weaker budget policy.
    """
    settings = store.get_settings()
    cap = float(settings.get("max_daily_ad_spend", 100.0))
    exploration_pct = float(settings.get("exploration_budget_pct", 20.0))
    return {
        "enabled": True,
        "profile_bound": bool(profile_id),
        "hard_cap": round(cap, 2),
        "spent_today": None,
        "pending_reserve": 0.0,
        "remaining": 0.0,
        "utilization_pct": 100.0,
        "exploration_pct": exploration_pct,
        "exploration_cap": round(cap * exploration_pct / 100.0, 2),
        "exploration_remaining": 0.0,
        "fresh": False,
        "increase_allowed": False,
        "exploration_allowed": False,
        "reason": "same-day spend guard is not installed",
    }


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
        state = budget_status(self, profile_id)
        state.pop("profile_id", None)
        result["budget_guard"] = state
        return result

    Store.dashboard = dashboard


def _install_service_context() -> None:
    from .service import ControlService

    original_context = ControlService.context

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
            " The owner daily maximum ad spend is the single commercial budget boundary. "
            "Before any spend-increasing execution, the controller requires fresh same-day spend evidence; "
            "Campaign budget reads are additionally required only when a monetary activation/reservation needs them. "
            "Weak historical performance may justify a small reversible HERMES-SP-EXP-* experiment inside the "
            "exploration share, but stale spend evidence or a reached cap must fail closed."
        )
        return result

    ControlService.context = context


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _configure_settings()
    _install_store()
    _install_service_context()
    _INSTALLED = True
