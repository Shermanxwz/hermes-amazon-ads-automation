from __future__ import annotations

from datetime import datetime, timedelta
import json
import secrets
from typing import Any

from .budget_guard import (
    _campaign_budget,
    _campaign_rows,
    _exploration,
    _positive_budget_delta,
    _profile_matches,
    _time,
    _values,
)
from .db import UTC, future_iso, now_iso

_INSTALLED = False
_PENDING = {"reserved", "pending", "uncertain"}
_COUNTABLE = _PENDING | {"executed", "verified"}
_OPEN_ACTIVATION = {"blocked", "planned", "reserved", "executed", "pending", "uncertain"}
OVERDELIVERY_SETTING = "amazon_daily_budget_max_spend_multiplier"


def _settings(conn) -> dict[str, Any]:
    wanted = {
        "daily_budget_hard_cap_enabled",
        "max_daily_ad_spend",
        "exploration_budget_pct",
        "budget_guard_exploration_stop_pct",
        "budget_guard_conservative_pct",
        "budget_guard_live_read_max_age_seconds",
        OVERDELIVERY_SETTING,
    }
    placeholders = ",".join("?" for _ in wanted)
    rows = conn.execute(
        f"SELECT key,value FROM settings WHERE key IN ({placeholders})",
        tuple(sorted(wanted)),
    ).fetchall()
    return {str(row["key"]): json.loads(row["value"]) for row in rows}


def _has_more_pages(result: Any) -> bool:
    for value in _values(result, "nextToken", "next_token", "continuationToken", "continuation_token"):
        if value not in (None, "", False, 0, [], {}):
            return True
    return False


def _campaign_state(campaign: dict[str, Any]) -> str | None:
    direct = campaign.get("state")
    if isinstance(direct, str) and direct.strip():
        return direct.strip().upper()
    for value in _values(campaign, "state"):
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    return None


def _fresh_complete_live_exposure(conn, profile_id: str, max_age_seconds: int) -> dict[str, Any] | None:
    cutoff = datetime.now(UTC).timestamp() - max_age_seconds
    rows = conn.execute(
        "SELECT a.*,t.family,t.semantic FROM actions a "
        "LEFT JOIN mcp_tools t ON t.registered_name=a.tool_name "
        "WHERE a.phase='after' AND a.operation='read' AND a.allowed=1 AND a.structured_result=1 "
        "AND a.result_json IS NOT NULL ORDER BY a.id DESC LIMIT 100"
    ).fetchall()
    for row in rows:
        if str(row["family"] or "") != "campaign" or str(row["semantic"] or "") != "read":
            continue
        created = _time(row["created_at"])
        if not created or created.timestamp() < cutoff:
            continue
        try:
            args = json.loads(row["args_json"] or "{}")
            result = json.loads(row["result_json"] or "null")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not _profile_matches(args, profile_id) or _has_more_pages(result):
            continue
        campaigns = _campaign_rows(result)
        if campaigns is None:
            continue
        budgets: dict[str, float] = {}
        states: dict[str, str] = {}
        complete = True
        for campaign in campaigns:
            campaign_id = str(
                next(
                    (
                        value for value in (
                            campaign.get("campaignId"),
                            campaign.get("campaign_id"),
                            campaign.get("id"),
                        )
                        if value not in (None, "")
                    ),
                    "",
                )
            ).strip()
            budget = _campaign_budget(campaign)
            state = _campaign_state(campaign)
            if not campaign_id or budget is None or not state:
                complete = False
                break
            budgets[campaign_id] = max(0.0, float(budget))
            states[campaign_id] = state
        if not complete:
            continue
        # ENABLED campaigns are current spend exposure. Unknown non-paused
        # states are conservatively treated as active. PAUSED/ARCHIVED budgets
        # do not block autonomy until a controller decision reserves activation.
        active_budget = round(sum(
            budget for campaign_id, budget in budgets.items()
            if states.get(campaign_id) not in {"PAUSED", "ARCHIVED"}
        ), 2)
        return {
            "source": "fresh_complete_amazon_campaign_read",
            "action_id": int(row["id"]),
            "observed_at": str(row["created_at"]),
            "campaign_budget_sum": active_budget,
            "all_campaign_budget_sum": round(sum(budgets.values()), 2),
            "campaign_count": len(budgets),
            "fresh": True,
            "_campaign_budgets": budgets,
            "_campaign_states": states,
        }
    return None


def _fresh_complete_for_store(store, profile_id: str, max_age_seconds: int) -> dict[str, Any] | None:
    with store.connection() as conn:
        return _fresh_complete_live_exposure(conn, profile_id, max_age_seconds)


def _source_campaign_id(conn, decision: dict[str, Any]) -> str:
    payload = decision.get("payload") if isinstance(decision.get("payload"), dict) else {}
    source_key = str(payload.get("activation_source_plan_key") or "").strip()
    if source_key and decision.get("task_id"):
        row = conn.execute(
            "SELECT entity_id FROM decisions WHERE task_id=? AND plan_key=? LIMIT 1",
            (decision["task_id"], source_key),
        ).fetchone()
        if row and row["entity_id"]:
            return str(row["entity_id"])
    entity_id = str(decision.get("entity_id") or "").strip()
    return "" if entity_id.startswith("{{decision:") else entity_id


def _enable_delta(conn, decision: dict[str, Any], observation: dict[str, Any]) -> float:
    action = str(decision.get("action_type") or "").lower()
    if action not in {"enable", "resume", "update_state", "set_state"}:
        return 0.0
    if str(decision.get("expected_family") or "") != "campaign":
        return 0.0
    payload = decision.get("payload") if isinstance(decision.get("payload"), dict) else {}
    args = payload.get("approved_args") if isinstance(payload.get("approved_args"), dict) else payload
    requested = [str(value).upper() for value in _values(args, "state", "status") if value is not None]
    if requested and "ENABLED" not in requested:
        return 0.0
    campaign_id = _source_campaign_id(conn, decision)
    budgets = observation.get("_campaign_budgets") if isinstance(observation.get("_campaign_budgets"), dict) else {}
    try:
        return max(0.0, float(budgets.get(campaign_id, 0.0)))
    except (TypeError, ValueError):
        return 0.0


def _effective_delta(conn, decision: dict[str, Any], observation: dict[str, Any]) -> float:
    direct = _positive_budget_delta(decision)
    if direct > 0:
        return direct
    return _enable_delta(conn, decision, observation)


def _create_has_open_activation(conn, decision: dict[str, Any]) -> bool:
    if str(decision.get("action_type") or "") != "create_campaign" or not decision.get("task_id"):
        return False
    rows = conn.execute(
        "SELECT payload_json,status FROM decisions WHERE task_id=? AND action_type='enable'",
        (decision["task_id"],),
    ).fetchall()
    source_key = str(decision.get("plan_key") or "")
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if str(payload.get("activation_source_plan_key") or "") == source_key and str(row["status"] or "") in _OPEN_ACTIVATION:
            return True
    return False


def _absorbed_by_observation(conn, decision: dict[str, Any], observation: dict[str, Any]) -> bool:
    observed = _time(observation.get("observed_at"))
    executed = _time(decision.get("executed_at"))
    if not observed or not executed or executed > observed:
        return False
    action = str(decision.get("action_type") or "").lower()
    campaign_id = _source_campaign_id(conn, decision)
    states = observation.get("_campaign_states") if isinstance(observation.get("_campaign_states"), dict) else {}
    if action in {"enable", "resume", "update_state", "set_state"}:
        return states.get(campaign_id) == "ENABLED"
    if action == "create_campaign":
        state = states.get(campaign_id)
        if state == "ENABLED":
            return True
        if state == "PAUSED" and _create_has_open_activation(conn, decision):
            return False
        return state is not None
    # Budget mutations observed after execution are reflected in the complete
    # Campaign budget snapshot regardless of current active/paused state.
    return True


def _committed_inside_transaction(
    store, conn, profile_id: str, observation: dict[str, Any], current_id: str,
) -> tuple[float, float]:
    day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT * FROM decisions WHERE profile_id=? AND id<>? AND created_at>=? "
        "AND status IN ('reserved','pending','uncertain','executed','verified') ORDER BY created_at,id",
        (profile_id, current_id, day_start),
    ).fetchall()
    committed = 0.0
    exploration = 0.0
    for row in rows:
        item = store._decision_dict(row)
        status = str(item.get("status") or "")
        if status not in _COUNTABLE:
            continue
        delta = _effective_delta(conn, item, observation)
        if delta <= 0:
            continue
        if status not in _PENDING and _absorbed_by_observation(conn, item, observation):
            continue
        committed += delta
        if _exploration(item):
            exploration += delta
    return round(committed, 2), round(exploration, 2)


def _raw_committed(store, profile_id: str, observation: dict[str, Any]) -> tuple[float, float]:
    with store.connection() as conn:
        return _committed_inside_transaction(store, conn, profile_id, observation, "")


def _sanitize_observation(observation: dict[str, Any] | None) -> dict[str, Any]:
    return {
        key: value for key, value in (observation or {}).items()
        if not str(key).startswith("_")
    }


def _owner_budget_status(store, profile_id: str | None = None, *, require_fresh: bool = False) -> dict[str, Any]:
    from . import budget_guard

    settings = store.get_settings()
    cap = float(settings.get("max_daily_ad_spend", 100.0))
    exploration_pct = float(settings.get("exploration_budget_pct", 20.0))
    stop_pct = float(settings.get("budget_guard_exploration_stop_pct", 80.0))
    conservative_pct = float(settings.get("budget_guard_conservative_pct", 90.0))
    max_age = int(settings.get("budget_guard_live_read_max_age_seconds", 900))
    multiplier = float(settings.get(OVERDELIVERY_SETTING, 2.0))
    if not profile_id:
        enabled = [row for row in store.list_profiles() if row and row.get("enabled")]
        profile_id = str(enabled[0].get("profile_id") or "") if len(enabled) == 1 else ""
    if not profile_id:
        return {
            "enabled": True, "profile_bound": False, "hard_cap": round(cap, 2),
            "exploration_pct": exploration_pct, "fresh": False,
            "increase_allowed": False, "exploration_allowed": False,
            "amazon_overdelivery_multiplier": multiplier,
            "reason": "one enabled Profile is required to compute the daily budget envelope",
        }

    live = _fresh_complete_for_store(store, profile_id, max_age)
    fallback = budget_guard._snapshot_exposure(store, profile_id)
    observation = live or fallback or {}
    raw_base = float(observation.get("campaign_budget_sum", observation.get("exposure", 0.0)) or 0.0)
    committed, exploration_committed = _raw_committed(store, profile_id, observation)
    observed_exposure = round(raw_base * multiplier, 2)
    committed_exposure = round(committed * multiplier, 2)
    projected = round(observed_exposure + committed_exposure, 2)
    exploration_committed_exposure = round(exploration_committed * multiplier, 2)
    ratio = projected / cap * 100.0 if cap > 0 else 100.0
    exploration_cap = round(cap * exploration_pct / 100.0, 2)
    result = {
        "enabled": True,
        "profile_bound": True,
        "profile_id": profile_id,
        "hard_cap": round(cap, 2),
        "amazon_overdelivery_multiplier": multiplier,
        "observed_campaign_budget_sum": round(raw_base, 2),
        "observed_exposure": observed_exposure,
        "committed_campaign_budget_delta_today": round(committed, 2),
        "committed_positive_delta_today": committed_exposure,
        "projected_exposure": projected,
        "remaining": round(max(0.0, cap - projected), 2),
        "utilization_pct": round(ratio, 2),
        "exploration_pct": exploration_pct,
        "exploration_cap": exploration_cap,
        "exploration_committed_today": exploration_committed_exposure,
        "exploration_remaining": round(max(0.0, exploration_cap - exploration_committed_exposure), 2),
        "exploration_stop_pct": stop_pct,
        "conservative_pct": conservative_pct,
        "fresh": bool(live),
        "observation_available": bool(observation),
        "observation": _sanitize_observation(observation),
        "increase_allowed": bool(live) and projected < cap and ratio < conservative_pct,
        "exploration_allowed": bool(live) and projected < cap and ratio < stop_pct and exploration_committed_exposure < exploration_cap,
    }
    if require_fresh and not live:
        result["reason"] = "a fresh complete unpaginated Amazon Campaign budget read is required before increasing exposure"
    elif not observation:
        result["reason"] = "no Campaign budget observation is available"
    elif projected >= cap:
        result["reason"] = "daily maximum-spend exposure hard cap reached"
    elif ratio >= conservative_pct:
        result["reason"] = "daily maximum-spend exposure entered conservative mode"
    elif ratio >= stop_pct:
        result["reason"] = "daily maximum-spend exposure stopped new exploration"
    else:
        result["reason"] = "within daily maximum-spend exposure envelope"
    return result


def _enforce_atomic_budget(store, conn, row) -> None:
    decision = store._decision_dict(row)
    settings = _settings(conn)
    if settings.get("daily_budget_hard_cap_enabled") is not True:
        # Risk-reducing and exposure-neutral writes do not need this setting;
        # identify possible positive exposure only after we have an observation.
        raw_direct = _positive_budget_delta(decision)
        if raw_direct > 0 or str(decision.get("action_type") or "").lower() in {"enable", "resume"}:
            raise ValueError("daily budget exposure hard cap is unavailable or disabled")
        return
    required = {
        "max_daily_ad_spend",
        "exploration_budget_pct",
        "budget_guard_exploration_stop_pct",
        "budget_guard_conservative_pct",
        "budget_guard_live_read_max_age_seconds",
        OVERDELIVERY_SETTING,
    }
    if not required.issubset(settings):
        if _positive_budget_delta(decision) > 0:
            raise ValueError("daily budget exposure settings are incomplete")
        return
    cap = float(settings["max_daily_ad_spend"])
    exploration_pct = float(settings["exploration_budget_pct"])
    stop_pct = float(settings["budget_guard_exploration_stop_pct"])
    conservative_pct = float(settings["budget_guard_conservative_pct"])
    max_age = int(settings["budget_guard_live_read_max_age_seconds"])
    multiplier = float(settings[OVERDELIVERY_SETTING])
    if cap <= 0 or multiplier < 1:
        raise ValueError("daily budget exposure configuration is invalid")

    profile_id = str(decision.get("profile_id") or "").strip()
    if not profile_id:
        if _positive_budget_delta(decision) > 0:
            raise ValueError("daily budget reservation requires a bound Profile")
        return
    observation = _fresh_complete_live_exposure(conn, profile_id, max_age)
    direct = _positive_budget_delta(decision)
    maybe_enable = str(decision.get("action_type") or "").lower() in {"enable", "resume", "update_state", "set_state"}
    if not observation:
        if direct > 0 or maybe_enable:
            raise ValueError("a fresh complete unpaginated Amazon Campaign budget read is required before increasing exposure")
        return
    delta = _effective_delta(conn, decision, observation)
    if delta <= 0:
        return

    committed, exploration_committed = _committed_inside_transaction(
        store, conn, profile_id, observation, str(decision["id"])
    )
    projected_after = (float(observation["campaign_budget_sum"]) + committed + delta) * multiplier
    if projected_after > cap + 1e-9:
        raise ValueError("planned write would exceed the owner daily maximum-spend exposure hard cap")

    utilization_after = projected_after / cap * 100.0
    if _exploration(decision):
        exploration_cap = cap * exploration_pct / 100.0
        exploration_after = (exploration_committed + delta) * multiplier
        if exploration_after > exploration_cap + 1e-9:
            raise ValueError("planned write would exceed the daily exploration maximum-spend pool")
        if utilization_after >= stop_pct - 1e-9:
            raise ValueError("new exploration is stopped at the configured budget utilization threshold")
    elif utilization_after >= conservative_pct - 1e-9:
        raise ValueError("positive exposure increases stop at the configured conservative threshold")


def _configure_overdelivery() -> None:
    from . import db

    # Amazon sponsored ads may spend up to 100% above average daily budget on
    # a high-traffic day. Keep the worst-case 2x multiplier locked until a
    # future live account-policy attestation safely proves a smaller bound.
    db.DEFAULT_SETTINGS[OVERDELIVERY_SETTING] = 2.0
    db.NUMERIC_SETTING_RANGES[OVERDELIVERY_SETTING] = (1.0, 2.0)
    db.SAFETY_LOCKED_SETTINGS[OVERDELIVERY_SETTING] = 2.0


def _install_store() -> None:
    from .db import Store

    def reserve_decision(
        self, decision_id: str, task_id: str, session_id: str, ttl_seconds: int,
        cooldown_seconds: int = 86400, *, max_actions_per_task: int = 50,
        max_actions_per_day: int = 250, max_campaign_creates_per_day: int = 2,
    ) -> dict[str, Any]:
        self.reconcile_expired_reservations()
        now = now_iso()
        token = secrets.token_urlsafe(24)
        expires = future_iso(ttl_seconds)
        cooldown_cutoff = (
            datetime.now(UTC) - timedelta(seconds=max(0, cooldown_seconds))
        ).isoformat(timespec="seconds")
        day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM decisions WHERE id=? AND task_id=?", (decision_id, task_id)
                ).fetchone()
                if not row:
                    raise KeyError("decision not found for task")
                if row["status"] != "planned":
                    raise ValueError(f"decision is not reservable from status {row['status']}")
                task_count = conn.execute(
                    "SELECT COUNT(*) FROM decisions WHERE task_id=? AND reserved_at IS NOT NULL", (task_id,)
                ).fetchone()[0]
                if task_count >= max_actions_per_task:
                    raise ValueError("task write limit reached")
                daily_count = conn.execute(
                    "SELECT COUNT(*) FROM decisions WHERE reserved_at>=?", (day_start,)
                ).fetchone()[0]
                if daily_count >= max_actions_per_day:
                    raise ValueError("daily write limit reached")
                if row["action_type"] == "create_campaign":
                    create_count = conn.execute(
                        "SELECT COUNT(*) FROM decisions WHERE action_type='create_campaign' AND reserved_at>=?",
                        (day_start,),
                    ).fetchone()[0]
                    if create_count >= max_campaign_creates_per_day:
                        raise ValueError("daily campaign creation limit reached")
                duplicate = conn.execute(
                    "SELECT id,status FROM decisions WHERE id<>? AND plan_key=? AND created_at>=? "
                    "AND status IN ('reserved','executed','pending','uncertain','verified','failed','mismatch') "
                    "ORDER BY created_at DESC LIMIT 1",
                    (decision_id, row["plan_key"], cooldown_cutoff),
                ).fetchone()
                if duplicate:
                    raise ValueError(f"equivalent decision is inside cooldown ({duplicate['status']})")

                # Authoritative financial gate. BEGIN IMMEDIATE serializes
                # concurrent reservations, so the second writer observes the
                # first writer's reserved exposure before computing room.
                _enforce_atomic_budget(self, conn, row)

                updated = conn.execute(
                    "UPDATE decisions SET status='reserved',reserved_by=?,reservation_token=?,"
                    "reservation_expires_at=?,reserved_at=? WHERE id=? AND status='planned'",
                    (session_id, token, expires, now, decision_id),
                ).rowcount
                if updated != 1:
                    raise ValueError("decision reservation race lost")
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        decision = self.get_decision(decision_id) or {}
        decision["reservation_token"] = token
        return decision

    Store.reserve_decision = reserve_decision


def _install_budget_observation() -> None:
    # budget_status() and the fast service guard must use the same strict
    # completeness/pagination rule and the same Amazon overdelivery model as
    # the authoritative transaction gate.
    from . import budget_guard

    budget_guard._fresh_live_exposure = _fresh_complete_for_store
    budget_guard.budget_status = _owner_budget_status


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _configure_overdelivery()
    _install_store()
    _install_budget_observation()
    _INSTALLED = True
