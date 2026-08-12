from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, time as day_time, timedelta, timezone
import json
from pathlib import Path
import re
import threading
from typing import Any, Iterator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    import fcntl
except ImportError:  # pragma: no cover - Linux is the production target
    fcntl = None

from .budget_guard import (
    _approved_args,
    _campaign_budget,
    _campaign_rows,
    _exploration,
    _first,
    _payload,
    _positive_budget_delta,
    _profile_matches,
    _time,
    _values,
)

UTC = timezone.utc
_INSTALLED = False
_PROCESS_LOCK = threading.RLock()

_PENDING = {"reserved", "pending", "uncertain"}
_COUNTABLE = _PENDING | {"executed", "verified"}
_OPEN_ACTIVATION = {"blocked", "planned", "reserved", "executed", "pending", "uncertain"}

OVERDELIVERY_SETTING = "amazon_daily_budget_max_spend_multiplier"
SPEND_TIMEZONE_SETTING = "daily_spend_timezone"
SPEND_EVIDENCE_MAX_AGE_SETTING = "daily_spend_evidence_max_age_seconds"
RESERVATION_HOLD_SETTING = "daily_spend_reservation_hold_seconds"
NON_BUDGET_RESERVE_PCT_SETTING = "daily_spend_non_budget_reserve_pct"
PLATFORM_BUFFER_PCT_SETTING = "daily_spend_platform_buffer_pct"


def _settings(conn) -> dict[str, Any]:
    wanted = {
        "daily_budget_hard_cap_enabled",
        "max_daily_ad_spend",
        "exploration_budget_pct",
        "budget_guard_exploration_stop_pct",
        "budget_guard_conservative_pct",
        "budget_guard_live_read_max_age_seconds",
        OVERDELIVERY_SETTING,
        SPEND_TIMEZONE_SETTING,
        SPEND_EVIDENCE_MAX_AGE_SETTING,
        RESERVATION_HOLD_SETTING,
        NON_BUDGET_RESERVE_PCT_SETTING,
        PLATFORM_BUFFER_PCT_SETTING,
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
    """Fresh complete Campaign state used only for monetary activation reserves.

    The owner spend ceiling is not derived from the sum of Campaign budgets.
    This observation is retained for CAS-like enable/create accounting and for
    identifying exploration Campaigns in Marketing Stream data.
    """
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
        names: dict[str, str] = {}
        complete = True
        for campaign in campaigns:
            campaign_id = str(
                next(
                    (
                        value
                        for value in (
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
            if not campaign_id or budget is None or not state or campaign_id in budgets:
                complete = False
                break
            budgets[campaign_id] = max(0.0, float(budget))
            states[campaign_id] = state
            names[campaign_id] = str(_first(campaign, "name", "campaignName") or "")
        if not complete:
            continue
        return {
            "source": "fresh_complete_amazon_campaign_read",
            "action_id": int(row["id"]),
            "observed_at": str(row["created_at"]),
            "campaign_budget_sum": round(
                sum(
                    budget
                    for campaign_id, budget in budgets.items()
                    if states.get(campaign_id) not in {"PAUSED", "ARCHIVED"}
                ),
                2,
            ),
            "all_campaign_budget_sum": round(sum(budgets.values()), 2),
            "campaign_count": len(budgets),
            "fresh": True,
            "_campaign_budgets": budgets,
            "_campaign_states": states,
            "_campaign_names": names,
        }
    return None


def _fresh_complete_for_store(store, profile_id: str, max_age_seconds: int) -> dict[str, Any] | None:
    with store.connection() as conn:
        return _fresh_complete_live_exposure(conn, profile_id, max_age_seconds)


def _source_campaign_id(conn, decision: dict[str, Any]) -> str:
    payload = _payload(decision)
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


def _requested_states(decision: dict[str, Any]) -> set[str]:
    args = _approved_args(decision)
    return {
        str(value).strip().upper()
        for value in _values(args, "state", "status")
        if value is not None and str(value).strip()
    }


def _requests_enable(decision: dict[str, Any]) -> bool:
    action = str(decision.get("action_type") or "").lower()
    if action in {"enable", "resume"}:
        return True
    if action in {"update_state", "set_state"}:
        return "ENABLED" in _requested_states(decision)
    body = _payload(decision)
    if str(body.get("field") or "").lower() == "state":
        return str(body.get("after") or "").upper() == "ENABLED"
    return False


def _requests_campaign_enable(decision: dict[str, Any]) -> bool:
    if not _requests_enable(decision):
        return False
    family = str(decision.get("expected_family") or decision.get("entity_type") or "").lower()
    return family == "campaign"


def _source_create(conn, decision: dict[str, Any]) -> dict[str, Any] | None:
    payload = _payload(decision)
    source_key = str(payload.get("activation_source_plan_key") or "").strip()
    task_id = str(decision.get("task_id") or "").strip()
    if not source_key or not task_id:
        return None
    row = conn.execute(
        "SELECT * FROM decisions WHERE task_id=? AND plan_key=? LIMIT 1",
        (task_id, source_key),
    ).fetchone()
    if not row:
        return None
    item = dict(row)
    item["payload"] = json.loads(item.pop("payload_json") or "{}")
    return item


def _enable_delta(conn, decision: dict[str, Any], observation: dict[str, Any] | None) -> float:
    if not _requests_campaign_enable(decision):
        return 0.0
    source = _source_create(conn, decision)
    if (
        source
        and str(source.get("action_type") or "").lower() == "create_campaign"
        and str(source.get("status") or "") in _COUNTABLE
        and _positive_budget_delta(source) > 0
    ):
        return 0.0
    budgets = (
        observation.get("_campaign_budgets")
        if isinstance(observation, dict) and isinstance(observation.get("_campaign_budgets"), dict)
        else {}
    )
    campaign_id = _source_campaign_id(conn, decision)
    if not campaign_id or campaign_id not in budgets:
        raise ValueError("Campaign to enable is missing from the fresh complete budget observation")
    try:
        return max(0.0, float(budgets[campaign_id]))
    except (TypeError, ValueError) as exc:
        raise ValueError("Campaign to enable has no usable budget in the fresh observation") from exc


def _is_spend_increasing(decision: dict[str, Any]) -> bool:
    action = str(decision.get("action_type") or "").lower()
    body = _payload(decision)
    field = str(body.get("field") or "").lower()
    if action in {"pause", "disable", "decrease_budget"}:
        return False
    if "negative" in action:
        return False
    if _requests_enable(decision):
        return True
    if action.startswith("create_") or "harvest" in action:
        return True
    if _positive_budget_delta(decision) > 0:
        return True
    if action in {"increase_bid", "update_bid", "set_bid"} or field == "bid":
        try:
            return float(body.get("after")) > float(body.get("before"))
        except (TypeError, ValueError):
            return action == "increase_bid"
    if action in {"increase_placement", "update_placement", "set_placement"} or field in {
        "placement",
        "percentage",
        "adjustment_percent",
    }:
        try:
            return float(body.get("after")) > float(body.get("before"))
        except (TypeError, ValueError):
            return action == "increase_placement"
    if action in {"update_state", "set_state"} or field == "state":
        return str(body.get("after") or "").upper() == "ENABLED"
    return False


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
        if (
            str(payload.get("activation_source_plan_key") or "") == source_key
            and str(row["status"] or "") in _OPEN_ACTIVATION
        ):
            return True
    return False


def _parse_dt(value: Any) -> datetime | None:
    return _time(value)


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"invalid daily spend timezone: {name}") from exc


def _day_window(timezone_name: str, now: datetime | None = None) -> tuple[str, datetime, datetime]:
    zone = _zone(timezone_name)
    current = (now or datetime.now(UTC)).astimezone(zone)
    start_local = datetime.combine(current.date(), day_time.min, tzinfo=zone)
    end_local = start_local + timedelta(days=1)
    return (
        current.date().isoformat(),
        start_local.astimezone(UTC),
        end_local.astimezone(UTC),
    )


def _signed_number(value: Any, *names: str) -> float | None:
    for item in _values(value, *names):
        try:
            return float(item)
        except (TypeError, ValueError):
            continue
    return None


def _payload_date(payload: dict[str, Any], event_time: Any, received_at: Any, timezone_name: str) -> str | None:
    direct = _first(payload, "date", "eventDate", "metricDate")
    if isinstance(direct, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", direct.strip()):
        return direct.strip()
    for candidate in (
        _first(payload, "timeWindowStart", "eventTime", "event_time", "timestamp"),
        event_time,
        received_at,
    ):
        parsed = _parse_dt(candidate)
        if parsed:
            return parsed.astimezone(_zone(timezone_name)).date().isoformat()
    return None


def _is_sp_traffic(dataset_id: str, payload: dict[str, Any]) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", dataset_id.lower())
    if "traffic" not in normalized:
        return False
    ad_product = str(_first(payload, "adProduct", "ad_product") or "").upper()
    if ad_product:
        return ad_product == "SPONSORED_PRODUCTS"
    return normalized.startswith("sp") or "sponsoredproduct" in normalized


def _stream_spend(
    conn,
    profile_id: str,
    timezone_name: str,
    evidence_max_age_seconds: int,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    today, start_utc, end_utc = _day_window(timezone_name, now)
    lower = (start_utc - timedelta(hours=12)).isoformat(timespec="seconds")
    upper = (end_utc + timedelta(hours=12)).isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT profile_id,dataset_id,event_time,payload_json,received_at FROM stream_events "
        "WHERE profile_id=? AND received_at>=? AND received_at<? ORDER BY received_at",
        (profile_id, lower, upper),
    ).fetchall()
    total = 0.0
    count = 0
    latest: datetime | None = None
    by_campaign: dict[str, float] = {}
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or not _is_sp_traffic(str(row["dataset_id"] or ""), payload):
            continue
        if _payload_date(payload, row["event_time"], row["received_at"], timezone_name) != today:
            continue
        amount = _signed_number(payload, "cost", "spend")
        if amount is None:
            continue
        total += amount
        count += 1
        received = _parse_dt(row["received_at"])
        if received and (latest is None or received > latest):
            latest = received
        campaign_id = str(_first(payload, "campaignId", "campaign_id") or "").strip()
        if campaign_id:
            by_campaign[campaign_id] = by_campaign.get(campaign_id, 0.0) + amount
    if count == 0 or latest is None:
        return None
    current = now or datetime.now(UTC)
    age = max(0.0, (current - latest.astimezone(UTC)).total_seconds())
    return {
        "source": "marketing_stream_hourly_sp_traffic",
        "date": today,
        "spend": round(max(0.0, total), 2),
        "event_count": count,
        "as_of": latest.astimezone(UTC).isoformat(timespec="seconds"),
        "fresh": age <= evidence_max_age_seconds,
        "age_seconds": round(age, 1),
        "_campaign_spend": {key: round(max(0.0, value), 2) for key, value in by_campaign.items()},
    }


def _report_spend(
    conn,
    profile_id: str,
    timezone_name: str,
    evidence_max_age_seconds: int,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    today, _, _ = _day_window(timezone_name, now)
    row = conn.execute(
        "SELECT c.id,c.kpi_json,c.created_at FROM cycles c "
        "JOIN snapshot_lineage s ON s.cycle_id=c.id "
        "WHERE c.profile_id=? AND c.window_start=? AND c.window_end=? "
        "ORDER BY c.created_at DESC LIMIT 1",
        (profile_id, today, today),
    ).fetchone()
    if not row:
        return None
    try:
        kpis = json.loads(row["kpi_json"] or "{}")
        spend = float(kpis.get("spend"))
    except (TypeError, ValueError, json.JSONDecodeError, AttributeError):
        return None
    created = _parse_dt(row["created_at"])
    if created is None or spend < 0:
        return None
    current = now or datetime.now(UTC)
    age = max(0.0, (current - created.astimezone(UTC)).total_seconds())
    return {
        "source": "same_day_lineaged_report",
        "date": today,
        "spend": round(spend, 2),
        "cycle_id": str(row["id"]),
        "as_of": created.astimezone(UTC).isoformat(timespec="seconds"),
        "fresh": age <= evidence_max_age_seconds,
        "age_seconds": round(age, 1),
        "_campaign_spend": {},
    }


def _spend_evidence(
    conn,
    profile_id: str,
    timezone_name: str,
    evidence_max_age_seconds: int,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    candidates = [
        item
        for item in (
            _stream_spend(conn, profile_id, timezone_name, evidence_max_age_seconds, now=now),
            _report_spend(conn, profile_id, timezone_name, evidence_max_age_seconds, now=now),
        )
        if item is not None
    ]
    if not candidates:
        return None
    fresh = [item for item in candidates if item.get("fresh")]
    pool = fresh or candidates
    selected = max(pool, key=lambda item: (float(item.get("spend") or 0), str(item.get("as_of") or "")))
    return dict(selected)


def _monetary_delta(
    conn,
    decision: dict[str, Any],
    observation: dict[str, Any] | None,
) -> float:
    direct = _positive_budget_delta(decision)
    if direct > 0:
        return direct
    return _enable_delta(conn, decision, observation)


def _decision_reserve_amount(
    conn,
    decision: dict[str, Any],
    observation: dict[str, Any] | None,
    cap: float,
    multiplier: float,
    non_budget_pct: float,
    platform_buffer_pct: float,
) -> float:
    if not _is_spend_increasing(decision):
        return 0.0
    monetary = _monetary_delta(conn, decision, observation)
    if monetary > 0:
        extra_possible = max(0.0, monetary * max(0.0, multiplier - 1.0))
        bounded_buffer = min(extra_possible, cap * max(0.0, platform_buffer_pct) / 100.0)
        return round(monetary + bounded_buffer, 2)
    return round(max(0.01, cap * max(0.0, non_budget_pct) / 100.0), 2)


def _reservation_is_current(
    decision: dict[str, Any],
    day_start_utc: datetime,
    hold_seconds: int,
    *,
    now: datetime | None = None,
) -> bool:
    reserved = _parse_dt(decision.get("reserved_at") or decision.get("created_at"))
    if reserved is None or reserved < day_start_utc:
        return False
    status = str(decision.get("status") or "")
    if status in _PENDING:
        return True
    if status not in {"executed", "verified"}:
        return False
    if str(decision.get("action_type") or "") == "create_campaign" and decision.get("_open_activation"):
        return True
    executed = _parse_dt(decision.get("executed_at") or decision.get("verified_at") or reserved)
    current = now or datetime.now(UTC)
    return bool(executed and (current - executed.astimezone(UTC)).total_seconds() <= hold_seconds)


def _pending_reserve(
    store,
    conn,
    profile_id: str,
    settings: dict[str, Any],
    observation: dict[str, Any] | None,
    *,
    current_id: str = "",
    now: datetime | None = None,
) -> tuple[float, float]:
    cap = float(settings["max_daily_ad_spend"])
    multiplier = float(settings[OVERDELIVERY_SETTING])
    non_budget_pct = float(settings[NON_BUDGET_RESERVE_PCT_SETTING])
    platform_buffer_pct = float(settings[PLATFORM_BUFFER_PCT_SETTING])
    hold_seconds = int(settings[RESERVATION_HOLD_SETTING])
    _, day_start, _ = _day_window(str(settings[SPEND_TIMEZONE_SETTING]), now)
    rows = conn.execute(
        "SELECT * FROM decisions WHERE profile_id=? AND id<>? "
        "AND status IN ('reserved','pending','uncertain','executed','verified') "
        "ORDER BY created_at,id",
        (profile_id, current_id),
    ).fetchall()
    total = 0.0
    exploration = 0.0
    for row in rows:
        item = store._decision_dict(row)
        item["_open_activation"] = _create_has_open_activation(conn, item)
        if not _reservation_is_current(item, day_start, hold_seconds, now=now):
            continue
        reserve = _decision_reserve_amount(
            conn, item, observation, cap, multiplier, non_budget_pct, platform_buffer_pct
        )
        total += reserve
        if _exploration(item):
            exploration += reserve
    return round(total, 2), round(exploration, 2)


def _exploration_spend(evidence: dict[str, Any] | None, observation: dict[str, Any] | None) -> float:
    if not evidence or not observation:
        return 0.0
    by_campaign = evidence.get("_campaign_spend")
    names = observation.get("_campaign_names")
    if not isinstance(by_campaign, dict) or not isinstance(names, dict):
        return 0.0
    total = 0.0
    for campaign_id, amount in by_campaign.items():
        if re.match(r"^HERMES-SP-EXP-", str(names.get(campaign_id) or ""), re.I):
            total += float(amount or 0.0)
    return round(max(0.0, total), 2)


def _sanitize(value: dict[str, Any] | None) -> dict[str, Any]:
    return {key: item for key, item in (value or {}).items() if not str(key).startswith("_")}


def _blocked_budget_status(
    cap: float,
    exploration_pct: float,
    timezone_name: str,
    reason: str,
    *,
    spent_today: float | None = None,
    source: str | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    exploration_cap = round(cap * exploration_pct / 100.0, 2)
    return {
        "enabled": True,
        "profile_bound": True,
        "hard_cap": round(cap, 2),
        "spent_today": spent_today,
        "pending_reserve": 0.0,
        "protected_spend": round(cap, 2),
        "remaining": 0.0,
        "utilization_pct": 100.0,
        "exploration_pct": exploration_pct,
        "exploration_cap": exploration_cap,
        "exploration_spend_today": 0.0,
        "exploration_pending_reserve": 0.0,
        "exploration_remaining": 0.0,
        "spend_timezone": timezone_name,
        "spend_source": source,
        "spend_as_of": as_of,
        "fresh": False,
        "increase_allowed": False,
        "exploration_allowed": False,
        "reason": reason,
        "projected_exposure": round(cap, 2),
        "observed_exposure": spent_today,
        "committed_positive_delta_today": 0.0,
    }


def _owner_budget_status(store, profile_id: str | None = None, *, require_fresh: bool = False) -> dict[str, Any]:
    settings = store.get_settings()
    cap = float(settings.get("max_daily_ad_spend", 100.0))
    exploration_pct = float(settings.get("exploration_budget_pct", 20.0))
    stop_pct = float(settings.get("budget_guard_exploration_stop_pct", 80.0))
    conservative_pct = float(settings.get("budget_guard_conservative_pct", 90.0))
    campaign_max_age = int(settings.get("budget_guard_live_read_max_age_seconds", 900))
    evidence_max_age = int(settings.get(SPEND_EVIDENCE_MAX_AGE_SETTING, 7200))
    timezone_name = str(settings.get(SPEND_TIMEZONE_SETTING, "America/Los_Angeles"))
    if not profile_id:
        enabled = [row for row in store.list_profiles() if row and row.get("enabled")]
        profile_id = str(enabled[0].get("profile_id") or "") if len(enabled) == 1 else ""
    if not profile_id:
        result = _blocked_budget_status(
            cap, exploration_pct, timezone_name,
            "one enabled Profile is required to compute the daily spend ceiling",
        )
        result["profile_bound"] = False
        return result

    with store.connection() as conn:
        evidence = _spend_evidence(conn, profile_id, timezone_name, evidence_max_age)
        campaign_observation = _fresh_complete_live_exposure(conn, profile_id, campaign_max_age)
        if evidence is None:
            return _blocked_budget_status(
                cap, exploration_pct, timezone_name,
                "no same-day Sponsored Products spend evidence is available",
            )
        if require_fresh and not evidence.get("fresh"):
            return _blocked_budget_status(
                cap, exploration_pct, timezone_name,
                "same-day Sponsored Products spend evidence is stale",
                spent_today=float(evidence.get("spend") or 0.0),
                source=str(evidence.get("source") or ""),
                as_of=str(evidence.get("as_of") or ""),
            )
        try:
            pending, exploration_pending = _pending_reserve(
                store, conn, profile_id, settings, campaign_observation
            )
        except ValueError as exc:
            return _blocked_budget_status(
                cap, exploration_pct, timezone_name, str(exc),
                spent_today=float(evidence.get("spend") or 0.0),
                source=str(evidence.get("source") or ""),
                as_of=str(evidence.get("as_of") or ""),
            )

    spent = round(max(0.0, float(evidence.get("spend") or 0.0)), 2)
    protected = round(spent + pending, 2)
    ratio = protected / cap * 100.0 if cap > 0 else 100.0
    exploration_cap = round(cap * exploration_pct / 100.0, 2)
    exploration_spent = _exploration_spend(evidence, campaign_observation)
    exploration_used = round(exploration_spent + exploration_pending, 2)
    fresh = bool(evidence.get("fresh"))
    result = {
        "enabled": True,
        "profile_bound": True,
        "profile_id": profile_id,
        "hard_cap": round(cap, 2),
        "spent_today": spent,
        "pending_reserve": pending,
        "protected_spend": protected,
        "remaining": round(max(0.0, cap - protected), 2),
        "utilization_pct": round(ratio, 2),
        "exploration_pct": exploration_pct,
        "exploration_cap": exploration_cap,
        "exploration_spend_today": exploration_spent,
        "exploration_pending_reserve": exploration_pending,
        "exploration_remaining": round(max(0.0, exploration_cap - exploration_used), 2),
        "exploration_stop_pct": stop_pct,
        "conservative_pct": conservative_pct,
        "spend_timezone": timezone_name,
        "spend_source": str(evidence.get("source") or ""),
        "spend_as_of": str(evidence.get("as_of") or ""),
        "spend_evidence": _sanitize(evidence),
        "campaign_budget_evidence_fresh": bool(campaign_observation),
        "fresh": fresh,
        "increase_allowed": fresh and protected < cap and ratio < conservative_pct,
        "exploration_allowed": (
            fresh
            and protected < cap
            and ratio < stop_pct
            and exploration_used < exploration_cap
        ),
        "projected_exposure": protected,
        "observed_exposure": spent,
        "committed_positive_delta_today": pending,
        "exploration_committed_today": exploration_used,
    }
    if not fresh:
        result["reason"] = "same-day Sponsored Products spend evidence is stale"
    elif protected >= cap:
        result["reason"] = "owner daily maximum ad spend ceiling reached"
    elif ratio >= conservative_pct:
        result["reason"] = "daily spend entered conservative mode"
    elif ratio >= stop_pct:
        result["reason"] = "daily spend stopped new exploration"
    else:
        result["reason"] = "within owner daily maximum ad spend"
    return result


def _enforce_atomic_budget(store, conn, row) -> None:
    decision = store._decision_dict(row)
    if not _is_spend_increasing(decision):
        return
    settings = _settings(conn)
    required = {
        "daily_budget_hard_cap_enabled",
        "max_daily_ad_spend",
        "exploration_budget_pct",
        "budget_guard_exploration_stop_pct",
        "budget_guard_conservative_pct",
        "budget_guard_live_read_max_age_seconds",
        OVERDELIVERY_SETTING,
        SPEND_TIMEZONE_SETTING,
        SPEND_EVIDENCE_MAX_AGE_SETTING,
        RESERVATION_HOLD_SETTING,
        NON_BUDGET_RESERVE_PCT_SETTING,
        PLATFORM_BUFFER_PCT_SETTING,
    }
    if settings.get("daily_budget_hard_cap_enabled") is not True or not required.issubset(settings):
        raise ValueError("daily spend ceiling settings are unavailable or incomplete")
    cap = float(settings["max_daily_ad_spend"])
    if cap <= 0:
        raise ValueError("daily spend ceiling configuration is invalid")
    profile_id = str(decision.get("profile_id") or "").strip()
    if not profile_id:
        raise ValueError("daily spend reservation requires a bound Profile")

    evidence = _spend_evidence(
        conn,
        profile_id,
        str(settings[SPEND_TIMEZONE_SETTING]),
        int(settings[SPEND_EVIDENCE_MAX_AGE_SETTING]),
    )
    if not evidence or not evidence.get("fresh"):
        raise ValueError("fresh same-day Sponsored Products spend evidence is required before increasing spend")

    campaign_observation = _fresh_complete_live_exposure(
        conn, profile_id, int(settings["budget_guard_live_read_max_age_seconds"])
    )
    monetary = _positive_budget_delta(decision)
    if monetary <= 0 and _requests_campaign_enable(decision):
        monetary = _enable_delta(conn, decision, campaign_observation)
    if monetary > 0:
        if not campaign_observation:
            raise ValueError(
                "a fresh complete unpaginated Amazon Campaign budget read is required "
                "before changing the monetary spend envelope"
            )
        nominal_after = float(campaign_observation.get("campaign_budget_sum") or 0.0) + monetary
        if nominal_after > cap + 1e-9:
            raise ValueError(
                "planned write would make active/future Campaign daily budgets exceed "
                "the owner daily maximum ad spend ceiling"
            )
    elif (
        _requests_campaign_enable(decision)
        and _source_create(conn, decision) is None
        and not campaign_observation
    ):
        raise ValueError(
            "a fresh complete unpaginated Amazon Campaign budget read is required before enabling spend"
        )

    pending, exploration_pending = _pending_reserve(
        store, conn, profile_id, settings, campaign_observation, current_id=str(decision["id"])
    )
    reserve = _decision_reserve_amount(
        conn,
        decision,
        campaign_observation,
        cap,
        float(settings[OVERDELIVERY_SETTING]),
        float(settings[NON_BUDGET_RESERVE_PCT_SETTING]),
        float(settings[PLATFORM_BUFFER_PCT_SETTING]),
    )
    spent = max(0.0, float(evidence.get("spend") or 0.0))
    protected_after = spent + pending + reserve
    if protected_after > cap + 1e-9:
        raise ValueError("planned write would exceed the owner daily maximum ad spend ceiling")

    utilization_after = protected_after / cap * 100.0
    if _exploration(decision):
        exploration_cap = cap * float(settings["exploration_budget_pct"]) / 100.0
        campaign_exploration_spend = _exploration_spend(evidence, campaign_observation)
        if campaign_exploration_spend + exploration_pending + reserve > exploration_cap + 1e-9:
            raise ValueError("planned write would exceed the daily exploration share")
        if utilization_after >= float(settings["budget_guard_exploration_stop_pct"]) - 1e-9:
            raise ValueError("new exploration is stopped at the configured daily-spend threshold")
    elif utilization_after >= float(settings["budget_guard_conservative_pct"]) - 1e-9:
        raise ValueError("spend-increasing actions stop at the configured conservative threshold")


@contextmanager
def _reservation_lock(store) -> Iterator[None]:
    with _PROCESS_LOCK:
        if fcntl is None:
            yield
            return
        lock_path = Path(str(store.path) + ".spend-reservation.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _configure() -> None:
    from . import db

    db.DEFAULT_SETTINGS.update({
        OVERDELIVERY_SETTING: 2.0,
        SPEND_TIMEZONE_SETTING: "America/Los_Angeles",
        SPEND_EVIDENCE_MAX_AGE_SETTING: 7200,
        RESERVATION_HOLD_SETTING: 7200,
        NON_BUDGET_RESERVE_PCT_SETTING: 1.0,
        PLATFORM_BUFFER_PCT_SETTING: 5.0,
    })
    db.SAFETY_LOCKED_SETTINGS.update({
        OVERDELIVERY_SETTING: 2.0,
        SPEND_TIMEZONE_SETTING: "America/Los_Angeles",
    })
    db.NUMERIC_SETTING_RANGES.update({
        OVERDELIVERY_SETTING: (1.0, 2.0),
        NON_BUDGET_RESERVE_PCT_SETTING: (0.1, 10.0),
        PLATFORM_BUFFER_PCT_SETTING: (0.0, 20.0),
    })
    db.INTEGER_SETTING_RANGES.update({
        SPEND_EVIDENCE_MAX_AGE_SETTING: (300, 21600),
        RESERVATION_HOLD_SETTING: (300, 21600),
    })


def _install_budget_observation() -> None:
    from . import budget_guard

    budget_guard.budget_status = _owner_budget_status


def _install_service_guard() -> None:
    from .service import ControlService

    original_guard = ControlService._guardrail_check

    def guard(self, decision, tool, settings):
        allowed, reason = original_guard(self, decision, tool, settings)
        if not allowed or not _is_spend_increasing(decision):
            return allowed, reason
        profile_id = str(decision.get("profile_id") or "")
        state = _owner_budget_status(self.store, profile_id or None, require_fresh=True)
        if not state.get("fresh"):
            return False, str(state.get("reason") or "fresh same-day spend evidence is required")
        if _exploration(decision):
            if not state.get("exploration_allowed"):
                return False, str(state.get("reason") or "daily exploration share is unavailable")
        elif not state.get("increase_allowed"):
            return False, str(state.get("reason") or "daily spend ceiling blocks increases")
        return True, reason + "; owner daily spend ceiling verified"

    ControlService._guardrail_check = guard


def _install_store() -> None:
    from .db import Store

    original_reserve = Store.reserve_decision

    def reserve_decision(self, decision_id: str, task_id: str, session_id: str, *args, **kwargs):
        with _reservation_lock(self):
            with self.connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    row = conn.execute(
                        "SELECT * FROM decisions WHERE id=? AND task_id=?",
                        (decision_id, task_id),
                    ).fetchone()
                    if row and row["status"] == "planned":
                        _enforce_atomic_budget(self, conn, row)
                    conn.rollback()
                except Exception:
                    conn.rollback()
                    raise
            return original_reserve(self, decision_id, task_id, session_id, *args, **kwargs)

    Store.reserve_decision = reserve_decision


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _configure()
    _install_budget_observation()
    _install_service_guard()
    _install_store()
    _INSTALLED = True
