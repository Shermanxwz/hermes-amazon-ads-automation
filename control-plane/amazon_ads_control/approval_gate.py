from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import secrets
from typing import Any
from urllib.parse import parse_qs, urlparse

_INSTALLED = False
UTC = timezone.utc

PERMANENTLY_BLOCKED_FAMILIES = {"billing", "account_admin"}
PERMANENTLY_BLOCKED_WORDS = {
    "billing", "invoice", "payment", "permission", "role", "invitation",
    "account_link", "delete_account", "delete_profile",
}
APPROVAL_ACTION_WORDS = {
    "create_campaign", "create_ad_group", "create_ad", "create_portfolio",
    "launch", "expand", "archive", "pause", "disable", "composite", "workflow",
}
RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _expires(minutes: int) -> str:
    return (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat(timespec="seconds")


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _decision_plan(decision: dict[str, Any]) -> dict[str, Any]:
    payload = decision.get("payload") if isinstance(decision.get("payload"), dict) else {}
    return {
        "decision_id": str(decision.get("id") or ""),
        "plan_key": str(decision.get("plan_key") or ""),
        "action_type": str(decision.get("action_type") or ""),
        "entity_type": str(decision.get("entity_type") or ""),
        "entity_id": str(decision.get("entity_id") or ""),
        "expected_family": str(decision.get("expected_family") or ""),
        "risk": str(decision.get("risk") or "critical"),
        "tool_name": str(payload.get("tool_name") or ""),
        "arguments_hash": str(payload.get("approved_args_hash") or ""),
        "expected_state": payload.get("expected_state") if isinstance(payload.get("expected_state"), dict) else {},
        "maximum_daily_budget": payload.get("maximum_daily_budget"),
    }


def _approval_plan(task: dict[str, Any], decisions: list[dict[str, Any]]) -> dict[str, Any]:
    rows = sorted((_decision_plan(item) for item in decisions), key=lambda row: row["decision_id"])
    return {
        "version": 1,
        "task_id": str(task.get("id") or ""),
        "cycle_id": str(task.get("cycle_id") or ""),
        "profile_id": str(decisions[0].get("profile_id") if decisions else ""),
        "title": str(task.get("title") or ""),
        "actions": rows,
    }


def _requires_approval(decision: dict[str, Any], tool: dict[str, Any] | None = None) -> bool:
    payload = decision.get("payload") if isinstance(decision.get("payload"), dict) else {}
    if payload.get("approval_required") is True:
        return True
    risk = str((tool or {}).get("risk") or decision.get("risk") or "critical").lower()
    if risk in {"high", "critical"}:
        return True
    action = str(decision.get("action_type") or "").lower()
    native = str((tool or {}).get("native_name") or "").lower()
    return any(word in action or word in native for word in APPROVAL_ACTION_WORDS)


def _permanent_block(tool: dict[str, Any] | None) -> str | None:
    if not tool:
        return "Amazon Ads tool is absent from the synchronized live catalog"
    family = str(tool.get("family") or "")
    semantic = str(tool.get("semantic") or "unknown")
    native = str(tool.get("native_name") or "").lower().replace("-", "_")
    if semantic == "unknown":
        return "unknown Amazon Ads MCP semantics require catalog review and cannot be approved"
    if tool.get("drifted"):
        return "unacknowledged Amazon Ads MCP schema drift cannot be approved"
    if family in PERMANENTLY_BLOCKED_FAMILIES:
        return "billing and account-administration operations remain permanently blocked"
    if "delete" in native:
        return "irreversible delete operations remain permanently blocked"
    if any(word in native for word in PERMANENTLY_BLOCKED_WORDS):
        return "account, billing or permission mutations remain permanently blocked"
    return None


def _risk_max(left: str, right: str) -> str:
    return left if RISK_ORDER.get(left, 3) >= RISK_ORDER.get(right, 3) else right


def _approval_dict(row: Any) -> dict[str, Any]:
    item = dict(row)
    for source, target in (
        ("plan_json", "plan"),
        ("decision_ids_json", "decision_ids"),
        ("tool_names_json", "tool_names"),
    ):
        item[target] = json.loads(item.pop(source) or ("[]" if target != "plan" else "{}"))
    return item


def _configure_settings() -> None:
    from . import db as db_module

    # High-risk operations are no longer globally immutable-denied. They require
    # a payload-bound human approval. Account/billing, unknown, drifted and
    # irreversible delete operations remain immutable-denied.
    db_module.SAFETY_LOCKED_SETTINGS.pop("block_high_risk_writes", None)
    db_module.SAFETY_LOCKED_SETTINGS.pop("block_deletes", None)
    db_module.SAFETY_LOCKED_SETTINGS.update({
        "require_payload_bound_approval": True,
        "block_account_admin": True,
        "catalog_drift_blocks_writes": True,
        "require_planned_writes": True,
        "require_independent_verification": True,
        "max_write_batch_size": 1,
    })
    db_module.DEFAULT_SETTINGS.update({
        "block_high_risk_writes": False,
        "block_deletes": False,
        "require_payload_bound_approval": True,
        "approval_ttl_minutes": 30,
        "approval_max_actions": 50,
        "allow_approved_campaign_creation": True,
        "allow_approved_composite_workflows": True,
        "require_declared_worker_model": True,
        "require_different_verifier_model": True,
        "executor_models": ["MiniMax-M3"],
        "verifier_models": ["gpt-5.6-sol"],
        "fallback_forces_observe": True,
    })
    db_module.BOOLEAN_SETTINGS.update({
        "require_payload_bound_approval",
        "allow_approved_campaign_creation",
        "allow_approved_composite_workflows",
        "require_declared_worker_model",
        "require_different_verifier_model",
        "fallback_forces_observe",
    })
    db_module.INTEGER_SETTING_RANGES.update({
        "approval_ttl_minutes": (1, 1440),
        "approval_max_actions": (1, 500),
    })


def _install_store() -> None:
    from .db import Store

    original_init = Store.__init__
    original_dashboard = Store.dashboard
    original_reserve = Store.reserve_decision
    original_record_verification = Store.record_verification
    original_bind_worker = Store.bind_worker

    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        with self.connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS approval_requests (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    profile_id TEXT,
                    status TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    decision_ids_json TEXT NOT NULL,
                    tool_names_json TEXT NOT NULL,
                    maximum_daily_budget REAL,
                    requested_by TEXT NOT NULL,
                    requested_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    approved_by TEXT,
                    approved_at TEXT,
                    rejected_by TEXT,
                    rejected_at TEXT,
                    cancelled_by TEXT,
                    cancelled_at TEXT,
                    token_hash TEXT,
                    completed_at TEXT,
                    FOREIGN KEY(task_id) REFERENCES tasks(id)
                );
                CREATE INDEX IF NOT EXISTS idx_approval_status_time
                    ON approval_requests(status, requested_at DESC);
                CREATE INDEX IF NOT EXISTS idx_approval_task
                    ON approval_requests(task_id, requested_at DESC);
                CREATE TABLE IF NOT EXISTS approval_decisions (
                    approval_id TEXT NOT NULL,
                    decision_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reserved_at TEXT,
                    completed_at TEXT,
                    PRIMARY KEY(approval_id, decision_id),
                    FOREIGN KEY(approval_id) REFERENCES approval_requests(id) ON DELETE CASCADE,
                    FOREIGN KEY(decision_id) REFERENCES decisions(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS approval_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    approval_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(approval_id) REFERENCES approval_requests(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_approval_events
                    ON approval_events(approval_id, created_at);
                CREATE TABLE IF NOT EXISTS hermes_sessions (
                    session_id TEXT PRIMARY KEY,
                    model TEXT,
                    provider TEXT,
                    surface TEXT,
                    state TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    ended_at TEXT
                );
                """
            )

    def approval_event(self, approval_id: str, event: str, actor: str, data: dict[str, Any] | None = None) -> int:
        with self.connection() as conn:
            cursor = conn.execute(
                "INSERT INTO approval_events(approval_id,event,actor,data_json,created_at) VALUES(?,?,?,?,?)",
                (approval_id, event[:80], actor[:120], _canonical(data or {}), _now()),
            )
            return int(cursor.lastrowid)

    def reconcile_expired_approvals(self) -> list[str]:
        now = _now()
        ids: list[str] = []
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                rows = conn.execute(
                    "SELECT id,task_id FROM approval_requests WHERE status='pending' AND expires_at<?",
                    (now,),
                ).fetchall()
                ids = [str(row["id"]) for row in rows]
                if ids:
                    placeholders = ",".join("?" for _ in ids)
                    conn.execute(
                        f"UPDATE approval_requests SET status='expired' WHERE id IN ({placeholders})",
                        ids,
                    )
                    task_ids = [str(row["task_id"]) for row in rows]
                    if task_ids:
                        tp = ",".join("?" for _ in task_ids)
                        conn.execute(
                            f"UPDATE tasks SET status='blocked',write_allowed=0 WHERE id IN ({tp}) AND status='awaiting_approval'",
                            task_ids,
                        )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        for approval_id in ids:
            self.approval_event(approval_id, "expired", "controller")
        return ids

    def create_approval_request(
        self,
        task_id: str,
        actor: str,
        summary: str = "",
        ttl_minutes: int | None = None,
    ) -> dict[str, Any]:
        self.reconcile_expired_approvals()
        task = self.get_task(task_id)
        if not task:
            raise KeyError("task not found")
        decisions = self.list_decisions(task_id=task_id, limit=500)
        if not decisions:
            raise ValueError("approval requires at least one planned decision")
        if any(item.get("status") != "planned" for item in decisions):
            raise ValueError("approval can only cover wholly planned decisions")
        settings = self.get_settings()
        if len(decisions) > int(settings.get("approval_max_actions", 50)):
            raise ValueError("approval plan exceeds the configured action limit")
        plan = _approval_plan(task, decisions)
        plan_hash = _digest(plan)
        risk = "low"
        tools: list[str] = []
        maximum_daily_budget = 0.0
        for decision in decisions:
            risk = _risk_max(risk, str(decision.get("risk") or "critical").lower())
            payload = decision.get("payload") if isinstance(decision.get("payload"), dict) else {}
            tool_name = str(payload.get("tool_name") or "")
            if tool_name:
                tools.append(tool_name)
            value = payload.get("maximum_daily_budget")
            if isinstance(value, (int, float)):
                maximum_daily_budget += max(0.0, float(value))
        minutes = int(ttl_minutes or settings.get("approval_ttl_minutes", 30))
        approval_id = secrets.token_hex(10)
        now = _now()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "UPDATE approval_requests SET status='cancelled',cancelled_by='superseded',cancelled_at=? "
                    "WHERE task_id=? AND status IN ('pending','approved')",
                    (now, task_id),
                )
                conn.execute(
                    "INSERT INTO approval_requests("
                    "id,task_id,profile_id,status,risk,summary,plan_json,payload_hash,"
                    "decision_ids_json,tool_names_json,maximum_daily_budget,requested_by,requested_at,expires_at"
                    ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        approval_id,
                        task_id,
                        str(plan.get("profile_id") or "") or None,
                        "pending",
                        risk,
                        (summary or f"Approve {len(decisions)} exact Amazon Ads actions")[:1000],
                        _canonical(plan),
                        plan_hash,
                        _canonical([item["id"] for item in decisions]),
                        _canonical(sorted(set(tools))),
                        maximum_daily_budget or None,
                        actor[:120],
                        now,
                        _expires(minutes),
                    ),
                )
                for decision in decisions:
                    conn.execute(
                        "INSERT INTO approval_decisions(approval_id,decision_id,status) VALUES(?,?,'pending')",
                        (approval_id, decision["id"]),
                    )
                conn.execute(
                    "UPDATE tasks SET status='awaiting_approval',write_allowed=0 WHERE id=?",
                    (task_id,),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        self.approval_event(approval_id, "requested", actor, {"payload_hash": plan_hash})
        self.event(
            "warning",
            "approval.requested",
            actor,
            task_id,
            "High-risk Amazon Ads plan is waiting for explicit operator approval",
            {"approval_id": approval_id, "payload_hash": plan_hash, "actions": len(decisions)},
        )
        return self.get_approval(approval_id) or {}

    def get_approval(self, approval_id: str) -> dict[str, Any] | None:
        self.reconcile_expired_approvals()
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM approval_requests WHERE id=?", (approval_id,)).fetchone()
            if not row:
                return None
            item = _approval_dict(row)
            events = conn.execute(
                "SELECT event,actor,data_json,created_at FROM approval_events WHERE approval_id=? ORDER BY id",
                (approval_id,),
            ).fetchall()
            item["events"] = [
                {
                    "event": event["event"],
                    "actor": event["actor"],
                    "data": json.loads(event["data_json"] or "{}"),
                    "created_at": event["created_at"],
                }
                for event in events
            ]
            return item

    def list_approvals(self, limit: int = 100, status: str | None = None) -> list[dict[str, Any]]:
        self.reconcile_expired_approvals()
        sql = "SELECT * FROM approval_requests"
        params: list[Any] = []
        if status:
            sql += " WHERE status=?"
            params.append(status)
        sql += " ORDER BY requested_at DESC LIMIT ?"
        params.append(min(500, max(1, int(limit))))
        with self.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_approval_dict(row) for row in rows]

    def approve_approval(
        self,
        approval_id: str,
        actor: str,
        payload_hash: str,
        confirmation: str,
    ) -> dict[str, Any]:
        self.reconcile_expired_approvals()
        now = _now()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute("SELECT * FROM approval_requests WHERE id=?", (approval_id,)).fetchone()
                if not row:
                    raise KeyError("approval not found")
                if row["status"] != "pending":
                    raise ValueError(f"approval is not pending ({row['status']})")
                if _parse_time(row["expires_at"]) <= datetime.now(UTC):
                    raise ValueError("approval expired")
                if not hmac.compare_digest(str(row["payload_hash"]), str(payload_hash)):
                    raise ValueError("approval payload hash mismatch")
                expected = f"APPROVE {approval_id} {str(row['payload_hash'])[:12]}"
                if not hmac.compare_digest(confirmation.strip(), expected):
                    raise ValueError(f"confirmation must exactly equal: {expected}")
                task = self.get_task(str(row["task_id"]))
                decisions = self.list_decisions(task_id=str(row["task_id"]), limit=500)
                current_hash = _digest(_approval_plan(task or {}, decisions))
                if not hmac.compare_digest(current_hash, str(row["payload_hash"])):
                    raise ValueError("task plan changed after approval request")
                token = secrets.token_urlsafe(32)
                token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
                conn.execute(
                    "UPDATE approval_requests SET status='approved',approved_by=?,approved_at=?,token_hash=? WHERE id=?",
                    (actor[:120], now, token_hash, approval_id),
                )
                conn.execute(
                    "UPDATE tasks SET status='planned',write_allowed=1 WHERE id=? AND status='awaiting_approval'",
                    (row["task_id"],),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        self.approval_event(approval_id, "approved", actor, {"payload_hash": payload_hash})
        self.event(
            "warning",
            "approval.approved",
            actor,
            str(row["task_id"]),
            "Operator approved an exact payload-bound Amazon Ads plan",
            {"approval_id": approval_id, "payload_hash": payload_hash},
        )
        return self.get_approval(approval_id) or {}

    def reject_approval(self, approval_id: str, actor: str, reason: str = "") -> dict[str, Any]:
        now = _now()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute("SELECT * FROM approval_requests WHERE id=?", (approval_id,)).fetchone()
                if not row:
                    raise KeyError("approval not found")
                if row["status"] not in {"pending", "approved"}:
                    raise ValueError(f"approval cannot be rejected from {row['status']}")
                conn.execute(
                    "UPDATE approval_requests SET status='rejected',rejected_by=?,rejected_at=? WHERE id=?",
                    (actor[:120], now, approval_id),
                )
                conn.execute(
                    "UPDATE tasks SET status='rejected',write_allowed=0 WHERE id=?",
                    (row["task_id"],),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        self.approval_event(approval_id, "rejected", actor, {"reason": reason[:1000]})
        return self.get_approval(approval_id) or {}

    def approval_for_decision(self, decision_id: str, task_id: str) -> dict[str, Any] | None:
        self.reconcile_expired_approvals()
        with self.connection() as conn:
            row = conn.execute(
                "SELECT ar.*,ad.status AS decision_approval_status "
                "FROM approval_requests ar JOIN approval_decisions ad ON ad.approval_id=ar.id "
                "WHERE ar.task_id=? AND ad.decision_id=? AND ar.status='approved' "
                "AND ad.status='pending' ORDER BY ar.approved_at DESC LIMIT 1",
                (task_id, decision_id),
            ).fetchone()
        if not row:
            return None
        approval = _approval_dict(row)
        task = self.get_task(task_id)
        decisions = self.list_decisions(task_id=task_id, limit=500)
        if _digest(_approval_plan(task or {}, decisions)) != approval["payload_hash"]:
            return None
        return approval

    def mark_approval_reserved(self, approval_id: str, decision_id: str) -> None:
        with self.connection() as conn:
            cursor = conn.execute(
                "UPDATE approval_decisions SET status='reserved',reserved_at=? "
                "WHERE approval_id=? AND decision_id=? AND status='pending'",
                (_now(), approval_id, decision_id),
            )
        if cursor.rowcount != 1:
            raise ValueError("approval decision was already consumed or is unavailable")
        self.approval_event(approval_id, "decision_reserved", "executor", {"decision_id": decision_id})

    def complete_approval_decision(self, decision_id: str) -> None:
        now = _now()
        approval_id = ""
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT approval_id FROM approval_decisions WHERE decision_id=? AND status='reserved' "
                    "ORDER BY reserved_at DESC LIMIT 1",
                    (decision_id,),
                ).fetchone()
                if not row:
                    conn.rollback()
                    return
                approval_id = str(row["approval_id"])
                conn.execute(
                    "UPDATE approval_decisions SET status='completed',completed_at=? "
                    "WHERE approval_id=? AND decision_id=?",
                    (now, approval_id, decision_id),
                )
                pending = int(conn.execute(
                    "SELECT COUNT(*) FROM approval_decisions WHERE approval_id=? AND status<>'completed'",
                    (approval_id,),
                ).fetchone()[0])
                if pending == 0:
                    conn.execute(
                        "UPDATE approval_requests SET status='completed',completed_at=? WHERE id=?",
                        (now, approval_id),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        self.approval_event(approval_id, "decision_completed", "verifier", {"decision_id": decision_id})

    def record_hermes_session(
        self,
        session_id: str,
        state: str,
        model: str | None = None,
        provider: str | None = None,
        surface: str | None = None,
    ) -> None:
        if not session_id:
            return
        now = _now()
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO hermes_sessions(session_id,model,provider,surface,state,started_at,last_seen_at,ended_at) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET "
                "model=COALESCE(excluded.model,hermes_sessions.model),"
                "provider=COALESCE(excluded.provider,hermes_sessions.provider),"
                "surface=COALESCE(excluded.surface,hermes_sessions.surface),"
                "state=excluded.state,last_seen_at=excluded.last_seen_at,"
                "ended_at=CASE WHEN excluded.state='ended' THEN excluded.ended_at ELSE hermes_sessions.ended_at END",
                (
                    session_id,
                    model,
                    provider,
                    surface,
                    state,
                    now,
                    now,
                    now if state == "ended" else None,
                ),
            )

    def dashboard(self):
        result = original_dashboard(self)
        approvals = self.list_approvals(20)
        result["approvals"] = {
            "recent": approvals,
            "pending": sum(1 for item in approvals if item.get("status") == "pending"),
            "approved": sum(1 for item in approvals if item.get("status") == "approved"),
        }
        return result

    def reserve_decision(self, decision_id, task_id, session_id, *args, **kwargs):
        decision = self.get_decision(decision_id)
        tool_name = str((decision or {}).get("payload", {}).get("tool_name") or "")
        tool = self.get_tool(tool_name) if tool_name else None
        approval = None
        if decision and _requires_approval(decision, tool):
            approval = self.approval_for_decision(decision_id, task_id)
            if not approval:
                raise ValueError("exact operator approval is required for this high-risk decision")
        result = original_reserve(self, decision_id, task_id, session_id, *args, **kwargs)
        if approval:
            try:
                self.mark_approval_reserved(approval["id"], decision_id)
            except Exception:
                # The Amazon mutation has not started yet. Put the decision back into a
                # safe blocked state rather than leaving an executable reservation.
                with self.connection() as conn:
                    conn.execute(
                        "UPDATE decisions SET status='blocked',failure='approval reservation failed',"
                        "reservation_token=NULL,reservation_expires_at=NULL WHERE id=? AND status='reserved'",
                        (decision_id,),
                    )
                raise
        return result

    def record_verification(self, **kwargs):
        result = original_record_verification(self, **kwargs)
        if kwargs.get("status") in {"verified", "mismatch"} and kwargs.get("decision_id"):
            self.complete_approval_decision(str(kwargs["decision_id"]))
        return result

    def bind_worker(self, *args, **kwargs):
        role = str(kwargs.get("role") or (args[5] if len(args) > 5 else "executor"))
        model = kwargs.get("model") if "model" in kwargs else (args[6] if len(args) > 6 else None)
        task_id = str(kwargs.get("task_id") or (args[0] if args else ""))
        settings = self.get_settings()
        declared = str(model or "").strip()
        if settings.get("require_declared_worker_model", True) and not declared:
            raise ValueError("worker model must be declared by Hermes delegation")
        allowed = settings.get("executor_models" if role == "executor" else "verifier_models", [])
        if isinstance(allowed, list) and allowed and declared not in {str(item) for item in allowed}:
            raise ValueError(f"{role} model {declared!r} is outside the configured role allowlist")
        if role == "verifier" and settings.get("require_different_verifier_model", True):
            with self.connection() as conn:
                row = conn.execute(
                    "SELECT w.model FROM tasks t LEFT JOIN workers w ON w.session_id=t.worker_session_id "
                    "WHERE t.id=?",
                    (task_id,),
                ).fetchone()
            executor_model = str(row["model"] or "") if row else ""
            if executor_model and declared == executor_model:
                raise ValueError("verifier must use a different model from the executor")
        return original_bind_worker(self, *args, **kwargs)

    Store.__init__ = init
    Store.approval_event = approval_event
    Store.reconcile_expired_approvals = reconcile_expired_approvals
    Store.create_approval_request = create_approval_request
    Store.get_approval = get_approval
    Store.list_approvals = list_approvals
    Store.approve_approval = approve_approval
    Store.reject_approval = reject_approval
    Store.approval_for_decision = approval_for_decision
    Store.mark_approval_reserved = mark_approval_reserved
    Store.complete_approval_decision = complete_approval_decision
    Store.record_hermes_session = record_hermes_session
    Store.dashboard = dashboard
    Store.reserve_decision = reserve_decision
    Store.record_verification = record_verification
    Store.bind_worker = bind_worker


def _install_service() -> None:
    from .schema_validation import validate_instance
    from .service import ControlService, _family_matches

    original_context = ControlService.context
    original_guardrail = ControlService._guardrail_check
    original_match = ControlService._match_decision

    def context(self, session_id):
        result = original_context(self, session_id)
        result["approvals"] = {
            "pending": self.store.list_approvals(20, "pending"),
            "approved": self.store.list_approvals(20, "approved"),
        }
        return result

    def _match_decision(self, task_id: str, tool: dict[str, Any], args: dict[str, Any]):
        decision, reason = original_match(self, task_id, tool, args)
        if decision:
            return decision, reason
        matches = []
        for item in self.store.list_decisions(task_id=task_id, status="planned", limit=500):
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            if not _family_matches(item, str(tool.get("family") or "")):
                continue
            if str(payload.get("tool_name") or "") != str(tool.get("registered_name") or ""):
                continue
            expected_hash = str(payload.get("approved_args_hash") or "")
            if expected_hash and hmac.compare_digest(expected_hash, _digest(args)):
                matches.append(item)
        if len(matches) == 1:
            return matches[0], "matched exact approved structural-operation arguments"
        if len(matches) > 1:
            return None, "write ambiguously matches multiple exact approved decisions"
        return None, reason

    def _guardrail_check(self, decision, tool, settings):
        permanent = _permanent_block(tool)
        if permanent:
            return False, permanent
        needs_approval = _requires_approval(decision, tool)
        approval = self.store.approval_for_decision(str(decision.get("id")), str(decision.get("task_id"))) if needs_approval else None
        if needs_approval and not approval:
            return False, "operator approval is required for this exact high-risk plan"
        if not needs_approval:
            return original_guardrail(self, decision, tool, settings)
        adjusted_tool = dict(tool)
        adjusted_tool["risk"] = "medium"
        adjusted_settings = dict(settings)
        adjusted_settings["block_high_risk_writes"] = False
        adjusted_settings["block_deletes"] = False
        adjusted_settings["allow_campaign_creation"] = bool(settings.get("allow_approved_campaign_creation", True))
        allowed, reason = original_guardrail(self, decision, adjusted_tool, adjusted_settings)
        if not allowed:
            return allowed, reason
        payload = decision.get("payload") if isinstance(decision.get("payload"), dict) else {}
        expected_hash = str(payload.get("approved_args_hash") or "")
        if expected_hash and not approval:
            return False, "payload-bound approval is missing"
        return True, f"within deterministic guardrails and operator approval {approval['id']}"

    def create_managed_plan(self, payload: dict[str, Any], actor: str = "hermes-main"):
        profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
        profile_id = str(profile.get("profile_id") or profile.get("id") or "").strip()
        if not profile_id:
            raise ValueError("profile.profile_id is required")
        title = str(payload.get("title") or "Amazon Ads managed structural plan").strip()
        raw_actions = payload.get("actions")
        if not isinstance(raw_actions, list) or not raw_actions:
            raise ValueError("actions must be a non-empty array")
        settings = self.store.get_settings()
        if len(raw_actions) > int(settings.get("approval_max_actions", 50)):
            raise ValueError("managed plan exceeds approval_max_actions")
        decisions = []
        for index, raw in enumerate(raw_actions):
            if not isinstance(raw, dict):
                raise ValueError(f"actions[{index}] must be an object")
            tool_name = str(raw.get("tool_name") or "").strip()
            tool = self.store.get_tool(tool_name)
            permanent = _permanent_block(tool)
            if permanent:
                raise ValueError(f"actions[{index}]: {permanent}")
            if not tool or not tool.get("enabled") or tool.get("semantic") != "write":
                raise ValueError(f"actions[{index}] requires an enabled live catalog write tool")
            arguments = raw.get("arguments") if isinstance(raw.get("arguments"), dict) else None
            if arguments is None:
                raise ValueError(f"actions[{index}].arguments must be an object")
            schema = tool.get("schema") if isinstance(tool.get("schema"), dict) else {}
            errors = validate_instance(arguments, schema)
            if errors:
                raise ValueError(f"actions[{index}] violates live MCP schema: {'; '.join(errors[:5])}")
            expected_state = raw.get("expected_state") if isinstance(raw.get("expected_state"), dict) else {}
            if not expected_state:
                raise ValueError(f"actions[{index}].expected_state is required for independent verification")
            action_type = str(raw.get("action_type") or "").strip()
            if not action_type:
                raise ValueError(f"actions[{index}].action_type is required")
            entity_type = str(raw.get("entity_type") or tool.get("family") or "entity").strip()
            entity_id = str(raw.get("entity_id") or raw.get("logical_id") or f"planned:{_digest(arguments)[:20]}")
            risk = _risk_max(str(tool.get("risk") or "critical"), str(raw.get("risk") or "medium"))
            decisions.append({
                "entity_type": entity_type,
                "entity_id": entity_id,
                "action_type": action_type,
                "priority": int(raw.get("priority") or 100 - index),
                "rule_id": "approval_gated_managed_plan",
                "reason": str(raw.get("reason") or "Main proposed an exact operator-approved managed operation")[:2000],
                "evidence": raw.get("evidence") if isinstance(raw.get("evidence"), dict) else {},
                "payload": {
                    "tool_name": tool_name,
                    "approved_args": arguments,
                    "approved_args_hash": _digest(arguments),
                    "expected_state": expected_state,
                    "approval_required": True,
                    "maximum_daily_budget": raw.get("maximum_daily_budget"),
                    "depends_on": raw.get("depends_on") if isinstance(raw.get("depends_on"), list) else [],
                },
                "expected_family": str(tool.get("family") or ""),
                "risk": risk,
                "plan_key": str(raw.get("plan_key") or f"managed:{profile_id}:{tool_name}:{_digest(arguments)}"),
            })
        snapshot = {
            "source": "amazon-ads-mcp-live-managed-plan",
            "profile": {
                "profile_id": profile_id,
                "name": profile.get("name"),
                "marketplace": profile.get("marketplace"),
                "country_code": profile.get("country_code"),
                "currency": profile.get("currency"),
            },
            "window": {"start": None, "end": None, "grain": "structural-plan"},
            "account": {"impressions": 0, "clicks": 0, "spend": 0, "sales": 0, "orders": 0},
        }
        cycle = self.store.create_cycle(
            profile=snapshot["profile"],
            source=snapshot["source"],
            window=snapshot["window"],
            data_quality={"eligible_for_writes": False, "structural_plan": True, "requires_operator_approval": True},
            kpis={},
            snapshot=snapshot,
            decisions=decisions,
            created_by=actor,
        )
        cycle_decisions = self.store.list_decisions(cycle_id=cycle["id"], limit=500)
        task = self.store.create_task(
            title=title,
            kind="managed-structural-plan",
            created_by=actor,
            parent_session_id=payload.get("parent_session_id"),
            write_allowed=False,
            payload={
                "objective": str(payload.get("objective") or title)[:8000],
                "decision_ids": [item["id"] for item in cycle_decisions],
                "approval_required": True,
            },
            cycle_id=cycle["id"],
            decision_ids=[item["id"] for item in cycle_decisions],
        )
        approval = self.store.create_approval_request(
            task["id"],
            actor,
            str(payload.get("approval_summary") or title),
            int(payload.get("approval_ttl_minutes") or settings.get("approval_ttl_minutes", 30)),
        )
        return {"cycle": self.store.get_cycle(cycle["id"]), "task": self.store.get_task(task["id"]), "approval": approval}

    def request_approval(self, payload: dict[str, Any]):
        task_id = str(payload.get("task_id") or "").strip()
        if not task_id:
            raise ValueError("task_id is required")
        return self.store.create_approval_request(
            task_id,
            str(payload.get("actor") or "hermes-main"),
            str(payload.get("summary") or ""),
            int(payload.get("ttl_minutes") or self.store.get_settings().get("approval_ttl_minutes", 30)),
        )

    ControlService.context = context
    ControlService._match_decision = _match_decision
    ControlService._guardrail_check = _guardrail_check
    ControlService.create_managed_plan = create_managed_plan
    ControlService.request_approval = request_approval


def _install_api() -> None:
    from .api import Handler
    from .security import constant_token_match

    original_get = Handler.do_GET
    original_post = Handler.do_POST

    def _require_operator(self) -> bool:
        token = self.headers.get("X-Operator-Token", "")
        expected = getattr(self.app.settings, "operator_token", "")
        if not expected or not constant_token_match(token, expected):
            self._respond(401, {"error": "invalid_operator_token"})
            return False
        return True

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/approvals":
            return original_get(self)
        if not self._require_browser():
            return
        query = parse_qs(parsed.query)
        status = query.get("status", [None])[0]
        self._respond(200, {"approvals": self.app.store.list_approvals(self._limit(query, 100), status)})

    def do_POST(self):
        path = urlparse(self.path).path
        agent_routes = {
            "/api/agent/managed-plans": ("create_managed_plan", 201),
            "/api/agent/approvals/request": ("request_approval", 201),
        }
        if path in agent_routes:
            try:
                data = self._body()
            except ValueError as exc:
                self._respond(400, {"error": str(exc)})
                return
            if not self._require_agent():
                return
            method, status = agent_routes[path]
            try:
                result = getattr(self.app.service, method)(data, str(data.get("actor") or "hermes-main")) if method == "create_managed_plan" else getattr(self.app.service, method)(data)
                self._respond(status, result)
            except (ValueError, KeyError) as exc:
                self._respond(400, {"error": str(exc)})
            except Exception as exc:
                self.app.store.event("error", "api.approval_agent_error", "controller", None, str(exc), {"path": path})
                self._respond(500, {"error": "internal_error"})
            return
        if path.startswith("/api/approvals/") and path.endswith(("/approve", "/reject")):
            try:
                data = self._body()
            except ValueError as exc:
                self._respond(400, {"error": str(exc)})
                return
            if not self._require_browser(mutate=True):
                return
            parts = [part for part in path.split("/") if part]
            approval_id = parts[2]
            try:
                if path.endswith("/approve"):
                    result = self.app.store.approve_approval(
                        approval_id,
                        "dashboard-operator",
                        str(data.get("payload_hash") or ""),
                        str(data.get("confirmation") or ""),
                    )
                else:
                    result = self.app.store.reject_approval(
                        approval_id,
                        "dashboard-operator",
                        str(data.get("reason") or ""),
                    )
                self._respond(200, result)
            except (ValueError, KeyError) as exc:
                self._respond(400, {"error": str(exc)})
            return
        if path.startswith("/api/operator/approvals/") and path.endswith(("/approve", "/reject")):
            try:
                data = self._body()
            except ValueError as exc:
                self._respond(400, {"error": str(exc)})
                return
            if not _require_operator(self):
                return
            parts = [part for part in path.split("/") if part]
            approval_id = parts[3]
            actor = str(data.get("actor") or "hermes-user-command")
            try:
                if path.endswith("/approve"):
                    result = self.app.store.approve_approval(
                        approval_id,
                        actor,
                        str(data.get("payload_hash") or ""),
                        str(data.get("confirmation") or ""),
                    )
                else:
                    result = self.app.store.reject_approval(
                        approval_id,
                        actor,
                        str(data.get("reason") or ""),
                    )
                self._respond(200, result)
            except (ValueError, KeyError) as exc:
                self._respond(400, {"error": str(exc)})
            return
        return original_post(self)

    Handler.do_GET = do_GET
    Handler.do_POST = do_POST
    Handler._require_operator = _require_operator


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _configure_settings()
    _install_store()
    _install_service()
    _install_api()
    _INSTALLED = True
