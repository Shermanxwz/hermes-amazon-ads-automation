from __future__ import annotations

import json
from typing import Any

from .sealed_envelope import canonical, digest, marker, marker_shape_valid, standing_authorized
from .sealed_plan import validate_standing_plan

_INSTALLED = False


def _configure_settings() -> None:
    from . import db as db
    db.DEFAULT_SETTINGS.update({
        "sealed_sp_autonomy_enabled": True, "sealed_sp_allow_all_observed_asins": True,
        "sealed_sp_namespace": "HERMES-SP", "sealed_sp_max_campaign_budget": 50,
        "sealed_sp_max_new_budget_per_day": 100, "sealed_sp_max_campaign_creates_per_day": 2,
        "allow_campaign_creation": True, "allow_state_changes": True,
        "auto_write_ad_products": ["SPONSORED_PRODUCTS"], "posterior_prior_clicks": 24,
        "posterior_prior_cvr_pct": 8, "posterior_prior_aov_orders": 3,
        "posterior_default_aov": 30, "posterior_reduce_probability": 0.90,
        "posterior_scale_probability": 0.80, "posterior_budget_scale_probability": 0.60,
        "posterior_min_confidence": 0.30,
        "enable_global_budget_allocator": True, "enable_hourly_pacing": True,
        "hourly_max_bid_change_pct": 8,
    })
    db.BOOLEAN_SETTINGS.update({"sealed_sp_autonomy_enabled", "sealed_sp_allow_all_observed_asins",
        "allow_campaign_creation", "allow_state_changes", "enable_global_budget_allocator", "enable_hourly_pacing"})
    db.INTEGER_SETTING_RANGES.update({"sealed_sp_max_campaign_creates_per_day": (1, 25),
        "posterior_prior_clicks": (1, 10000), "posterior_prior_aov_orders": (1, 1000)})
    db.NUMERIC_SETTING_RANGES.update({"sealed_sp_max_campaign_budget": (1, 1000000),
        "sealed_sp_max_new_budget_per_day": (1, 1000000000), "posterior_prior_cvr_pct": (0.01, 95),
        "posterior_default_aov": (0.01, 1000000), "posterior_reduce_probability": (0.5, 0.999),
        "posterior_scale_probability": (0.5, 0.999), "posterior_budget_scale_probability": (0.5, 0.95),
        "posterior_min_confidence": (0, 1), "hourly_max_bid_change_pct": (1, 15)})
    db.STRATEGY_SETTING_KEYS.update({"sealed_sp_autonomy_enabled", "sealed_sp_allow_all_observed_asins",
        "sealed_sp_namespace", "sealed_sp_max_campaign_budget", "sealed_sp_max_new_budget_per_day",
        "sealed_sp_max_campaign_creates_per_day", "posterior_prior_clicks", "posterior_prior_cvr_pct",
        "posterior_prior_aov_orders", "posterior_default_aov", "posterior_reduce_probability",
        "posterior_scale_probability", "posterior_budget_scale_probability", "posterior_min_confidence",
        "enable_global_budget_allocator", "enable_hourly_pacing", "hourly_max_bid_change_pct"})
    db.SAFETY_LOCKED_SETTINGS.update({"sealed_sp_product_scope": "SPONSORED_PRODUCTS",
        "sealed_sp_permanent_delete_block": True, "sealed_sp_require_paused_create": True,
        "sealed_sp_require_independent_verification": True})


def _install_service() -> None:
    from . import approval_gate
    from .service import ControlService
    old_requires, old_guard, old_plan = approval_gate._requires_approval, ControlService._guardrail_check, ControlService.create_managed_plan

    def requires(decision: dict[str, Any], tool: dict[str, Any] | None = None) -> bool:
        return False if marker_shape_valid(decision) else old_requires(decision, tool)

    def guard(self, decision, tool, settings):
        if not marker(decision):
            return old_guard(self, decision, tool, settings)
        allowed, reason = standing_authorized(self.store, decision, tool)
        if not allowed:
            return False, reason
        adjusted, adjusted_tool = dict(settings), dict(tool)
        adjusted.update({"block_high_risk_writes": False, "allow_campaign_creation": True, "allow_state_changes": True})
        if adjusted_tool.get("risk") == "high":
            adjusted_tool["risk"] = "medium"
        ok, base = old_guard(self, decision, adjusted_tool, adjusted)
        return ok, f"{base}; {reason}" if ok else base

    def create_plan(self, payload: dict[str, Any], actor: str = "hermes-main"):
        if payload.get("standing_authorization") is not True:
            return old_plan(self, payload, actor)
        actions = validate_standing_plan(self, payload)
        clean = dict(payload); clean.pop("standing_authorization", None)
        clean["actions"] = [{k: v for k, v in row.items() if k != "standing_marker"} for row in actions]
        result = old_plan(self, clean, actor)
        task, cycle, approval = result["task"], result["cycle"], result.get("approval") or {}
        by_hash = {digest(row["arguments"]): row["standing_marker"] for row in actions}
        with self.store.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                rows = conn.execute("SELECT id,payload_json FROM decisions WHERE task_id=?", (task["id"],)).fetchall()
                for row in rows:
                    body = json.loads(row["payload_json"] or "{}")
                    auth = by_hash.get(str(body.get("approved_args_hash") or ""))
                    if not auth:
                        raise ValueError("standing authorization could not bind the exact payload")
                    body.update({"approval_required": False, "ad_product": "SPONSORED_PRODUCTS", "standing_authorization": auth})
                    conn.execute("UPDATE decisions SET payload_json=? WHERE id=?", (canonical(body), row["id"]))
                conn.execute("UPDATE tasks SET status='planned',write_allowed=1,kind='sealed-structural-plan' WHERE id=?", (task["id"],))
                if approval.get("id"):
                    conn.execute("UPDATE approval_requests SET status='cancelled',cancelled_by='standing-authorization',cancelled_at=datetime('now') WHERE id=?", (approval["id"],))
                conn.commit()
            except Exception:
                conn.rollback(); raise
        self.store.event("warning", "standing_authorization.applied", actor, task["id"],
            "Exact Sponsored Products structural plan released under the sealed envelope",
            {"actions": len(actions), "approval_id": approval.get("id"), "cycle_id": cycle["id"]})
        return {"cycle": self.store.get_cycle(cycle["id"]), "task": self.store.get_task(task["id"]),
            "approval": self.store.get_approval(approval["id"]) if approval.get("id") else None,
            "standing_authorization": {"applied": True, "scope": "SPONSORED_PRODUCTS",
                "atomic_actions": len(actions), "envelope_hash": actions[0]["standing_marker"]["envelope_hash"]}}

    approval_gate._requires_approval = requires
    ControlService._guardrail_check = guard
    ControlService.create_managed_plan = create_plan


def install() -> None:
    global _INSTALLED
    if not _INSTALLED:
        _configure_settings(); _install_service(); _INSTALLED = True
