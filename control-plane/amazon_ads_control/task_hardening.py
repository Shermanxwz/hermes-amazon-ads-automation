from __future__ import annotations

from typing import Any

from .service import ControlService

_INSTALLED = False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    current = ControlService.create_task

    def create_task(self: ControlService, payload: dict[str, Any], actor: str) -> dict[str, Any]:
        if self.store.get_settings().get("require_snapshot_lineage", True):
            cycle_id = str(payload.get("cycle_id") or "").strip()
            if not cycle_id:
                decision_ids = payload.get("decision_ids") if isinstance(payload.get("decision_ids"), list) else []
                if decision_ids:
                    decision = self.store.get_decision(str(decision_ids[0]))
                    cycle_id = str((decision or {}).get("cycle_id") or "")
            if cycle_id:
                cycle = self.store.get_cycle(cycle_id)
                if not cycle or not isinstance(cycle.get("lineage"), dict) or not cycle["lineage"].get("report_job_ids"):
                    raise ValueError("execution task requires a cycle with persistent report lineage")
        return current(self, payload, actor)

    ControlService.create_task = create_task
    _INSTALLED = True
