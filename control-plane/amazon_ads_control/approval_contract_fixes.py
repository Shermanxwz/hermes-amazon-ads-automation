from __future__ import annotations

from typing import Any

_INSTALLED = False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from . import approval_gate
    from . import db as db_module
    from .db import Store

    # The legacy flag is no longer the delete authority: irreversible delete is
    # rejected by the live-tool permanent-block classifier. Keep the setting
    # immutable at False so operators cannot accidentally revive the obsolete
    # global switch or misread it as an approval bypass.
    db_module.SAFETY_LOCKED_SETTINGS["block_deletes"] = False
    db_module.DEFAULT_SETTINGS["block_deletes"] = False

    def decision_plan(decision: dict[str, Any]) -> dict[str, Any]:
        payload = decision.get("payload") if isinstance(decision.get("payload"), dict) else {}
        arguments = payload.get("approved_args") if isinstance(payload.get("approved_args"), dict) else {}
        expected_template = payload.get("approved_expected_state")
        if not isinstance(expected_template, dict):
            expected_template = payload.get("expected_state") if isinstance(payload.get("expected_state"), dict) else {}
        depends_on = payload.get("depends_on") if isinstance(payload.get("depends_on"), list) else []
        return {
            "decision_id": str(decision.get("id") or ""),
            "plan_key": str(decision.get("plan_key") or ""),
            "action_type": str(decision.get("action_type") or ""),
            "entity_type": str(decision.get("entity_type") or ""),
            # A successful create later exposes the real Amazon ID as entity_id,
            # but approval remains bound to the original logical object and
            # placeholder templates. Runtime binding must never rewrite consent.
            "entity_id": str(decision.get("logical_entity_id") or decision.get("entity_id") or ""),
            "expected_family": str(decision.get("expected_family") or ""),
            "risk": str(decision.get("risk") or "critical"),
            "tool_name": str(payload.get("tool_name") or ""),
            # The operator must see and approve the canonical exact arguments,
            # not only their digest. The digest remains for independent runtime
            # comparison, while the full templates are part of the plan Hash.
            "arguments": arguments,
            "arguments_hash": str(payload.get("approved_args_hash") or ""),
            "expected_state": expected_template,
            "depends_on": [str(item) for item in depends_on],
            "maximum_daily_budget": payload.get("maximum_daily_budget"),
        }

    def approval_plan(task: dict[str, Any], decisions: list[dict[str, Any]]) -> dict[str, Any]:
        rows = sorted((decision_plan(item) for item in decisions), key=lambda row: row["decision_id"])
        return {
            "version": 2,
            "task_id": str(task.get("id") or ""),
            "cycle_id": str(task.get("cycle_id") or ""),
            "profile_id": str(decisions[0].get("profile_id") if decisions else ""),
            "title": str(task.get("title") or ""),
            "actions": rows,
        }

    # Store.create_approval_request resolves this module global at call time, so
    # installing the v2 plan compiler upgrades every subsequent approval without
    # rewriting or weakening the underlying reservation/verification flow.
    approval_gate._decision_plan = decision_plan
    approval_gate._approval_plan = approval_plan

    original_get_approval = Store.get_approval
    original_create_approval_request = Store.create_approval_request
    original_update_settings = Store.update_settings

    def update_settings(self, updates: dict[str, Any]) -> dict[str, Any]:
        # Safety invariants are controller-owned and read-only. Reject even an
        # idempotent explicit write so generic settings forms cannot create the
        # impression that an operator or model controls these boundaries.
        locked = sorted(set(updates) & set(db_module.SAFETY_LOCKED_SETTINGS))
        if locked:
            raise ValueError(f"{locked[0]} is a locked safety invariant")
        return original_update_settings(self, updates)

    def get_approval(self, approval_id: str) -> dict[str, Any] | None:
        item = original_get_approval(self, approval_id)
        if item is None:
            return None
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT ad.decision_id,ad.status,ad.reserved_at,ad.completed_at,"
                "d.plan_key,d.action_type,d.entity_type,d.entity_id "
                "FROM approval_decisions ad JOIN decisions d ON d.id=ad.decision_id "
                "WHERE ad.approval_id=? ORDER BY d.id",
                (approval_id,),
            ).fetchall()
        item["decisions"] = [dict(row) for row in rows]
        return item

    def create_approval_request(
        self,
        task_id: str,
        actor: str,
        summary: str = "",
        ttl_minutes: int | None = None,
    ) -> dict[str, Any]:
        task = self.get_task(task_id)
        if task and task.get("status") in {
            "executing", "verifying", "completed", "completed_with_issues",
        }:
            with self.connection() as conn:
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
        return original_create_approval_request(
            self, task_id, actor, summary, ttl_minutes,
        )

    Store.update_settings = update_settings
    Store.get_approval = get_approval
    Store.create_approval_request = create_approval_request
    _INSTALLED = True
