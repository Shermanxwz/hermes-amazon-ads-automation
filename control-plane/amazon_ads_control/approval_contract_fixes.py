from __future__ import annotations

from typing import Any

_INSTALLED = False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from . import db as db_module
    from .db import Store

    # The legacy flag is no longer the delete authority: irreversible delete is
    # rejected by the live-tool permanent-block classifier. Keep the setting
    # immutable at False so operators cannot accidentally revive the obsolete
    # global switch or misread it as an approval bypass.
    db_module.SAFETY_LOCKED_SETTINGS["block_deletes"] = False
    db_module.DEFAULT_SETTINGS["block_deletes"] = False

    original_get_approval = Store.get_approval
    original_create_approval_request = Store.create_approval_request

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

    Store.get_approval = get_approval
    Store.create_approval_request = create_approval_request
    _INSTALLED = True
