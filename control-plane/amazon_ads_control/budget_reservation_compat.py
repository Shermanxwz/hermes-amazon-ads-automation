from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import threading
from typing import Any, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - Linux is the production target
    fcntl = None

_INSTALLED = False
_PROCESS_LOCK = threading.RLock()


def _row_decision(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["payload"] = json.loads(item.pop("payload_json") or "{}")
    return item


def _activation_source(conn, decision: dict[str, Any]) -> dict[str, Any] | None:
    payload = decision.get("payload") if isinstance(decision.get("payload"), dict) else {}
    source_key = str(payload.get("activation_source_plan_key") or "").strip()
    task_id = str(decision.get("task_id") or "").strip()
    if not source_key or not task_id:
        return None
    row = conn.execute(
        "SELECT * FROM decisions WHERE task_id=? AND plan_key=? LIMIT 1",
        (task_id, source_key),
    ).fetchone()
    return _row_decision(row) if row else None


def _settings_or_raise(br, conn) -> tuple[dict[str, Any], float, float, float, float, int, float]:
    settings = br._settings(conn)
    if settings.get("daily_budget_hard_cap_enabled") is not True:
        raise ValueError("daily budget exposure hard cap is unavailable or disabled")
    required = {
        "max_daily_ad_spend",
        "exploration_budget_pct",
        "budget_guard_exploration_stop_pct",
        "budget_guard_conservative_pct",
        "budget_guard_live_read_max_age_seconds",
        br.OVERDELIVERY_SETTING,
    }
    if not required.issubset(settings):
        raise ValueError("daily budget exposure settings are incomplete")
    cap = float(settings["max_daily_ad_spend"])
    exploration_pct = float(settings["exploration_budget_pct"])
    stop_pct = float(settings["budget_guard_exploration_stop_pct"])
    conservative_pct = float(settings["budget_guard_conservative_pct"])
    max_age = int(settings["budget_guard_live_read_max_age_seconds"])
    multiplier = float(settings[br.OVERDELIVERY_SETTING])
    if cap <= 0 or multiplier < 1:
        raise ValueError("daily budget exposure configuration is invalid")
    return settings, cap, exploration_pct, stop_pct, conservative_pct, max_age, multiplier


def _enforce_staged_enable(br, store, conn, decision: dict[str, Any], source: dict[str, Any]) -> None:
    _, cap, exploration_pct, stop_pct, conservative_pct, max_age, multiplier = _settings_or_raise(br, conn)
    profile_id = str(decision.get("profile_id") or "").strip()
    if not profile_id:
        raise ValueError("daily budget reservation requires a bound Profile")
    observation = br._fresh_complete_live_exposure(conn, profile_id, max_age)
    if not observation:
        raise ValueError("a fresh complete unpaginated Amazon Campaign budget read is required before increasing exposure")

    committed, exploration_committed = br._committed_inside_transaction(
        store, conn, profile_id, observation, str(decision["id"])
    )
    projected_after = (float(observation["campaign_budget_sum"]) + committed) * multiplier
    if projected_after > cap + 1e-9:
        raise ValueError("planned write would exceed the owner daily maximum-spend exposure hard cap")

    utilization_after = projected_after / cap * 100.0
    if br._exploration(source):
        exploration_cap = cap * exploration_pct / 100.0
        exploration_after = exploration_committed * multiplier
        if exploration_after > exploration_cap + 1e-9:
            raise ValueError("planned write would exceed the daily exploration maximum-spend pool")
        if utilization_after >= stop_pct - 1e-9:
            raise ValueError("new exploration is stopped at the configured budget utilization threshold")
    elif utilization_after >= conservative_pct - 1e-9:
        raise ValueError("positive exposure increases stop at the configured conservative threshold")


@contextmanager
def _reservation_lock(store) -> Iterator[None]:
    with _PROCESS_LOCK:
        if fcntl is None:
            yield
            return
        lock_path = Path(str(store.path) + ".budget-reservation.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from . import budget_reservation as br
    from .db import Store

    original_reserve = Store.reserve_decision
    original_enable_delta = br._enable_delta
    original_absorbed = br._absorbed_by_observation
    original_enforce = br._enforce_atomic_budget

    def enable_delta(conn, decision: dict[str, Any], observation: dict[str, Any]) -> float:
        source = _activation_source(conn, decision)
        if (
            source
            and str(source.get("action_type") or "").lower() == "create_campaign"
            and str(source.get("status") or "") in br._COUNTABLE
            and br._positive_budget_delta(source) > 0
        ):
            # The PAUSED create already reserves this future exposure until the
            # staged activation completes. Do not reserve the same budget twice.
            return 0.0
        return original_enable_delta(conn, decision, observation)

    def absorbed_by_observation(conn, decision: dict[str, Any], observation: dict[str, Any]) -> bool:
        if original_absorbed(conn, decision, observation):
            return True
        observed = br._time(observation.get("observed_at"))
        executed = br._time(decision.get("executed_at"))
        if not observed or not executed or executed > observed:
            return False
        if str(decision.get("action_type") or "").lower() != "create_campaign":
            return False
        # A complete post-write Campaign read absorbs a settled standalone
        # create even when a synthetic test row lacks the resolved Amazon ID.
        # Staged PAUSED creates remain committed until their activation closes.
        return not br._create_has_open_activation(conn, decision)

    def enforce_atomic_budget(store, conn, row) -> None:
        decision = store._decision_dict(row)
        source = _activation_source(conn, decision)
        if (
            br._requests_enable(decision)
            and source
            and str(source.get("action_type") or "").lower() == "create_campaign"
            and str(source.get("status") or "") in br._COUNTABLE
        ):
            _enforce_staged_enable(br, store, conn, decision, source)
            return
        original_enforce(store, conn, row)

    def reserve_decision(self, decision_id: str, task_id: str, session_id: str, *args, **kwargs):
        # All compliant reservation callers serialize through this lock. The
        # budget precheck therefore observes every reservation committed by the
        # prior caller before the sealed approval/CAS/cooldown owner runs.
        with _reservation_lock(self):
            self.reconcile_expired_reservations()
            with self.connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    row = conn.execute(
                        "SELECT * FROM decisions WHERE id=? AND task_id=?",
                        (decision_id, task_id),
                    ).fetchone()
                    if row and row["status"] == "planned":
                        enforce_atomic_budget(self, conn, row)
                    conn.rollback()
                except Exception:
                    conn.rollback()
                    raise
            return original_reserve(self, decision_id, task_id, session_id, *args, **kwargs)

    # budget_reservation installs last. Prevent its legacy copied reservation
    # state machine from replacing the already-hardened sealed owner.
    br._install_store = lambda: None
    br._enable_delta = enable_delta
    br._absorbed_by_observation = absorbed_by_observation
    br._enforce_atomic_budget = enforce_atomic_budget
    Store.reserve_decision = reserve_decision
    _INSTALLED = True
