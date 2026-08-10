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


def _settings(conn) -> dict[str, Any]:
    wanted = {
        "daily_budget_hard_cap_enabled",
        "max_daily_ad_spend",
        "exploration_budget_pct",
        "budget_guard_exploration_stop_pct",
        "budget_guard_conservative_pct",
        "budget_guard_live_read_max_age_seconds",
    }
    rows = conn.execute(
        "SELECT key,value FROM settings WHERE key IN (?,?,?,?,?,?)",
        tuple(sorted(wanted)),
    ).fetchall()
    return {str(row["key"]): json.loads(row["value"]) for row in rows}


def _has_more_pages(result: Any) -> bool:
    for value in _values(result, "nextToken", "next_token", "continuationToken", "continuation_token"):
        if value not in (None, "", False, 0, [], {}):
            return True
    return False


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
            if not campaign_id or budget is None:
                complete = False
                break
            budgets[campaign_id] = max(0.0, float(budget))
        if not complete:
            continue
        return {
            "source": "fresh_complete_amazon_campaign_read",
            "action_id": int(row["id"]),
            "observed_at": str(row["created_at"]),
            "exposure": round(sum(budgets.values()), 2),
            "campaign_count": len(budgets),
            "fresh": True,
        }
    return None


def _fresh_complete_for_store(store, profile_id: str, max_age_seconds: int) -> dict[str, Any] | None:
    with store.connection() as conn:
        return _fresh_complete_live_exposure(conn, profile_id, max_age_seconds)


def _committed_inside_transaction(store, conn, profile_id: str, observed_at: str, current_id: str) -> tuple[float, float]:
    day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    observed = _time(observed_at)
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
        delta = _positive_budget_delta(item)
        if delta <= 0:
            continue
        executed = _time(item.get("executed_at"))
        if status not in _PENDING and observed and executed and executed <= observed:
            continue
        committed += delta
        if _exploration(item):
            exploration += delta
    return round(committed, 2), round(exploration, 2)


def _enforce_atomic_budget(store, conn, row) -> None:
    decision = store._decision_dict(row)
    delta = _positive_budget_delta(decision)
    if delta <= 0:
        return
    settings = _settings(conn)
    if settings.get("daily_budget_hard_cap_enabled") is not True:
        raise ValueError("daily budget exposure hard cap is unavailable or disabled")
    required = {
        "max_daily_ad_spend",
        "exploration_budget_pct",
        "budget_guard_exploration_stop_pct",
        "budget_guard_conservative_pct",
        "budget_guard_live_read_max_age_seconds",
    }
    if not required.issubset(settings):
        raise ValueError("daily budget exposure settings are incomplete")
    cap = float(settings["max_daily_ad_spend"])
    exploration_pct = float(settings["exploration_budget_pct"])
    stop_pct = float(settings["budget_guard_exploration_stop_pct"])
    conservative_pct = float(settings["budget_guard_conservative_pct"])
    max_age = int(settings["budget_guard_live_read_max_age_seconds"])
    if cap <= 0:
        raise ValueError("daily budget exposure hard cap must be positive")

    profile_id = str(decision.get("profile_id") or "").strip()
    if not profile_id:
        raise ValueError("daily budget reservation requires a bound Profile")
    observation = _fresh_complete_live_exposure(conn, profile_id, max_age)
    if not observation:
        raise ValueError("a fresh complete unpaginated Amazon Campaign budget read is required before increasing exposure")

    committed, exploration_committed = _committed_inside_transaction(
        store, conn, profile_id, observation["observed_at"], str(decision["id"])
    )
    projected_after = float(observation["exposure"]) + committed + delta
    if projected_after > cap + 1e-9:
        raise ValueError("planned write would exceed the account daily budget exposure hard cap")

    utilization_after = projected_after / cap * 100.0
    if _exploration(decision):
        exploration_cap = cap * exploration_pct / 100.0
        if exploration_committed + delta > exploration_cap + 1e-9:
            raise ValueError("planned write would exceed the daily exploration budget pool")
        if utilization_after >= stop_pct - 1e-9:
            raise ValueError("new exploration is stopped at the configured budget utilization threshold")
    elif utilization_after >= conservative_pct - 1e-9:
        raise ValueError("positive exposure increases stop at the configured conservative threshold")


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
    # completeness/pagination rule as the authoritative transaction gate.
    from . import budget_guard

    budget_guard._fresh_live_exposure = _fresh_complete_for_store


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_store()
    _install_budget_observation()
    _INSTALLED = True
