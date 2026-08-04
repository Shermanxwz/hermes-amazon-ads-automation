from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

_INSTALLED = False
UTC = timezone.utc


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from . import approval_gate
    from .db import Store
    from .service import ControlService

    original_permanent_block = approval_gate._permanent_block
    original_guardrail = ControlService._guardrail_check
    original_create_request = Store.create_approval_request
    original_reconcile = Store.reconcile_expired_approvals
    original_reject = Store.reject_approval
    original_complete_decision = Store.complete_approval_decision
    original_record_verification = Store.record_verification
    original_finalize_task = Store.finalize_task
    original_dashboard = Store.dashboard

    def permanent_block(tool: dict[str, Any] | None) -> str | None:
        reason = original_permanent_block(tool)
        if reason:
            return reason
        native = str((tool or {}).get("native_name") or "").lower().replace("-", "_")
        semantic = str((tool or {}).get("semantic") or "unknown")
        if semantic == "write" and any(token in native for token in (
            "account_management", "advertiser_account", "manager_account",
            "account_setting", "account_link", "user_management", "permissions",
        )):
            return "advertiser/account administration mutations remain permanently blocked"
        if semantic == "write" and any(token in native for token in (
            "composite", "workflow", "bulk", "batch", "multi_step", "multistep",
        )):
            return "black-box composite/bulk mutations must be decomposed into approved atomic actions"
        return None

    def guardrail(self, decision, tool, settings):
        reason = permanent_block(tool)
        if reason:
            return False, reason
        payload = decision.get("payload") if isinstance(decision.get("payload"), dict) else {}
        dependencies = payload.get("depends_on") if isinstance(payload.get("depends_on"), list) else []
        for reference in dependencies:
            reference = str(reference)
            dependency = self.store.get_decision(reference)
            if dependency is None:
                matches = [
                    item for item in self.store.list_decisions(
                        task_id=str(decision.get("task_id") or ""), limit=500
                    )
                    if str(item.get("plan_key") or "") == reference
                ]
                dependency = matches[0] if len(matches) == 1 else None
            if not dependency:
                return False, f"approved dependency {reference} was not found uniquely"
            if dependency.get("status") not in {"executed", "verified"}:
                return False, f"approved dependency {reference} has no confirmed successful execution"
        return original_guardrail(self, decision, tool, settings)

    def create_approval_request(
        self,
        task_id: str,
        actor: str,
        summary: str = "",
        ttl_minutes: int | None = None,
    ) -> dict[str, Any]:
        task = self.get_task(task_id)
        if not task:
            raise KeyError("task not found")
        if task.get("status") in {"executing", "verifying", "completed", "completed_with_issues"}:
            raise ValueError(f"task cannot request or replace approval from {task.get('status')}")
        with self.connection() as conn:
            active = conn.execute(
                "SELECT id,status FROM approval_requests WHERE task_id=? "
                "AND status IN ('approved','expired_in_flight') ORDER BY requested_at DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            consumed = int(conn.execute(
                "SELECT COUNT(*) FROM approval_decisions ad "
                "JOIN approval_requests ar ON ar.id=ad.approval_id "
                "WHERE ar.task_id=? AND ad.status IN ('reserved','completed','issue')",
                (task_id,),
            ).fetchone()[0])
        if consumed:
            raise ValueError(
                "a started or completed approval plan cannot be superseded; pause and reconcile the task"
            )
        if active:
            raise ValueError(
                f"approval {active['id']} is already {active['status']}; explicitly reject it before requesting a replacement"
            )
        return original_create_request(self, task_id, actor, summary, ttl_minutes)

    def reconcile_expired_approvals(self) -> list[str]:
        expired = list(original_reconcile(self))
        now = _now()
        newly_expired: list[tuple[str, str, bool]] = []
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                rows = conn.execute(
                    "SELECT id,task_id FROM approval_requests "
                    "WHERE status='approved' AND expires_at<?",
                    (now,),
                ).fetchall()
                for row in rows:
                    approval_id = str(row["id"])
                    reserved = int(conn.execute(
                        "SELECT COUNT(*) FROM approval_decisions "
                        "WHERE approval_id=? AND status='reserved'",
                        (approval_id,),
                    ).fetchone()[0])
                    status = "expired_in_flight" if reserved else "expired"
                    conn.execute(
                        "UPDATE approval_requests SET status=? WHERE id=? AND status='approved'",
                        (status, approval_id),
                    )
                    conn.execute(
                        "UPDATE approval_decisions SET status='expired' "
                        "WHERE approval_id=? AND status='pending'",
                        (approval_id,),
                    )
                    conn.execute(
                        "UPDATE tasks SET write_allowed=0,"
                        "status=CASE WHEN status='planned' THEN 'blocked' ELSE status END "
                        "WHERE id=?",
                        (row["task_id"],),
                    )
                    newly_expired.append((approval_id, str(row["task_id"]), bool(reserved)))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        for approval_id, task_id, in_flight in newly_expired:
            self.approval_event(
                approval_id,
                "expired_in_flight" if in_flight else "expired",
                "controller",
            )
            self.event(
                "critical" if in_flight else "warning",
                "approval.expired_in_flight" if in_flight else "approval.expired",
                "controller",
                task_id,
                "Operator approval expired; no additional Amazon Ads writes may start",
                {"approval_id": approval_id, "in_flight": in_flight},
            )
            expired.append(approval_id)
        return expired

    def reject_approval(self, approval_id: str, actor: str, reason: str = ""):
        approval = self.get_approval(approval_id)
        if approval and approval.get("status") in {"approved", "expired_in_flight"}:
            with self.connection() as conn:
                consumed = int(conn.execute(
                    "SELECT COUNT(*) FROM approval_decisions "
                    "WHERE approval_id=? AND status IN ('reserved','completed','issue')",
                    (approval_id,),
                ).fetchone()[0])
            if consumed:
                raise ValueError(
                    "an approval with started or completed actions cannot be rejected; pause the controller and reconcile it"
                )
        return original_reject(self, approval_id, actor, reason)

    def complete_approval_decision(self, decision_id: str) -> None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT ar.id,ar.status FROM approval_requests ar "
                "JOIN approval_decisions ad ON ad.approval_id=ar.id "
                "WHERE ad.decision_id=? AND ad.status='reserved' "
                "ORDER BY ad.reserved_at DESC LIMIT 1",
                (decision_id,),
            ).fetchone()
        previous_status = str(row["status"] or "") if row else ""
        approval_id = str(row["id"] or "") if row else ""
        original_complete_decision(self, decision_id)
        if previous_status not in {"expired", "expired_in_flight"} or not approval_id:
            return
        with self.connection() as conn:
            current = conn.execute(
                "SELECT status FROM approval_requests WHERE id=?", (approval_id,)
            ).fetchone()
            if current and current["status"] == "completed":
                conn.execute(
                    "UPDATE approval_requests SET status='completed_after_expiry' WHERE id=?",
                    (approval_id,),
                )
                self.approval_event(
                    approval_id,
                    "completed_after_expiry",
                    "controller",
                    {"decision_id": decision_id},
                )

    def record_verification(self, **kwargs):
        result = original_record_verification(self, **kwargs)
        decision_id = str(kwargs.get("decision_id") or "")
        verification_status = str(kwargs.get("status") or "")
        if not decision_id or verification_status == "verified":
            return result
        now = _now()
        approval_id = ""
        final_status = ""
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT approval_id FROM approval_decisions WHERE decision_id=? "
                    "AND status IN ('reserved','completed') ORDER BY reserved_at DESC LIMIT 1",
                    (decision_id,),
                ).fetchone()
                if not row:
                    conn.rollback()
                    return result
                approval_id = str(row["approval_id"])
                conn.execute(
                    "UPDATE approval_decisions SET status='issue',completed_at=? "
                    "WHERE approval_id=? AND decision_id=?",
                    (now, approval_id, decision_id),
                )
                open_count = int(conn.execute(
                    "SELECT COUNT(*) FROM approval_decisions WHERE approval_id=? "
                    "AND status IN ('pending','reserved')",
                    (approval_id,),
                ).fetchone()[0])
                if open_count == 0:
                    current = conn.execute(
                        "SELECT status FROM approval_requests WHERE id=?",
                        (approval_id,),
                    ).fetchone()
                    current_status = str(current["status"] or "") if current else ""
                    final_status = (
                        "completed_with_issues_after_expiry"
                        if current_status in {"expired", "expired_in_flight", "completed_after_expiry"}
                        else "completed_with_issues"
                    )
                    conn.execute(
                        "UPDATE approval_requests SET status=?,completed_at=? WHERE id=?",
                        (final_status, now, approval_id),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        self.approval_event(
            approval_id,
            "decision_issue",
            "verifier",
            {"decision_id": decision_id, "verification_status": verification_status},
        )
        if final_status:
            self.approval_event(approval_id, final_status, "controller")
        return result

    def finalize_task(self, task_id: str, actor: str, summary: str = "") -> dict[str, Any]:
        task = original_finalize_task(self, task_id, actor, summary)
        now = _now()
        with self.connection() as conn:
            approvals = conn.execute(
                "SELECT id,status FROM approval_requests WHERE task_id=? "
                "AND status NOT IN ('rejected','cancelled','expired') ORDER BY requested_at DESC",
                (task_id,),
            ).fetchall()
            for approval in approvals:
                approval_id = str(approval["id"])
                rows = conn.execute(
                    "SELECT ad.decision_id,d.status FROM approval_decisions ad "
                    "JOIN decisions d ON d.id=ad.decision_id WHERE ad.approval_id=?",
                    (approval_id,),
                ).fetchall()
                issues = 0
                for row in rows:
                    status = str(row["status"] or "")
                    if status == "verified":
                        conn.execute(
                            "UPDATE approval_decisions SET status='completed',completed_at=COALESCE(completed_at,?) "
                            "WHERE approval_id=? AND decision_id=?",
                            (now, approval_id, row["decision_id"]),
                        )
                    elif status in {"failed", "mismatch", "blocked"}:
                        issues += 1
                        conn.execute(
                            "UPDATE approval_decisions SET status='issue',completed_at=COALESCE(completed_at,?) "
                            "WHERE approval_id=? AND decision_id=?",
                            (now, approval_id, row["decision_id"]),
                        )
                previous = str(approval["status"] or "")
                after_expiry = previous in {"expired_in_flight", "completed_after_expiry"}
                final_status = (
                    "completed_with_issues_after_expiry" if issues and after_expiry
                    else "completed_after_expiry" if after_expiry
                    else "completed_with_issues" if issues
                    else "completed"
                )
                conn.execute(
                    "UPDATE approval_requests SET status=?,completed_at=COALESCE(completed_at,?) WHERE id=?",
                    (final_status, now, approval_id),
                )
                self.approval_event(
                    approval_id,
                    final_status,
                    actor,
                    {"task_id": task_id, "issues": issues},
                )
        return task

    def dashboard(self):
        result = original_dashboard(self)
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT status,COUNT(*) AS count FROM approval_requests GROUP BY status"
            ).fetchall()
        counts = {str(row["status"]): int(row["count"]) for row in rows}
        recent = self.list_approvals(50)
        result["approvals"] = {
            "recent": recent,
            "counts": counts,
            "pending": counts.get("pending", 0),
            "approved": counts.get("approved", 0),
            "in_flight": sum(
                1 for item in recent
                if any(event.get("event") == "decision_reserved" for event in item.get("events", []))
                and item.get("status") in {"approved", "expired_in_flight"}
            ),
        }
        return result

    approval_gate._permanent_block = permanent_block
    ControlService._guardrail_check = guardrail
    Store.create_approval_request = create_approval_request
    Store.reconcile_expired_approvals = reconcile_expired_approvals
    Store.reject_approval = reject_approval
    Store.complete_approval_decision = complete_approval_decision
    Store.record_verification = record_verification
    Store.finalize_task = finalize_task
    Store.dashboard = dashboard
    _INSTALLED = True
