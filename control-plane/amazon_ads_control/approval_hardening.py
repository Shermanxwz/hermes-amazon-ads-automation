from __future__ import annotations

from typing import Any

_INSTALLED = False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from . import approval_gate
    from .service import ControlService

    original_permanent_block = approval_gate._permanent_block
    original_guardrail = ControlService._guardrail_check

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
                    item for item in self.store.list_decisions(task_id=str(decision.get("task_id") or ""), limit=500)
                    if str(item.get("plan_key") or "") == reference
                ]
                dependency = matches[0] if len(matches) == 1 else None
            if not dependency:
                return False, f"approved dependency {reference} was not found uniquely"
            if dependency.get("status") not in {"executed", "verified"}:
                return False, f"approved dependency {reference} has no confirmed successful execution"
        return original_guardrail(self, decision, tool, settings)

    approval_gate._permanent_block = permanent_block
    ControlService._guardrail_check = guardrail
    _INSTALLED = True
