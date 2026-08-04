from __future__ import annotations

import json
import secrets
from typing import Any

from . import db as db_module
from . import service as service_module
from .evidence import canonical_hash
from .reporting import normalize_report_spec, report_key

_INSTALLED = False
_ALLOWED_PRODUCTS = {"SPONSORED_PRODUCTS", "SPONSORED_BRANDS", "SPONSORED_DISPLAY"}


def _report_dict(row) -> dict[str, Any]:
    item = dict(row)
    item["request"] = json.loads(item.pop("request_json"))
    return item


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    db_module.SAFETY_LOCKED_SETTINGS["require_result_event_id"] = True
    db_module.DEFAULT_SETTINGS["require_result_event_id"] = True
    db_module.BOOLEAN_SETTINGS.add("require_result_event_id")

    Store = db_module.Store
    Service = service_module.ControlService
    original_validate = Store.validate_strategy_overrides
    original_update = Store.update_settings
    original_get_cycle = Store.get_cycle
    original_finish_tool = Service.finish_tool

    def create_report_job(self, spec: dict[str, Any], actor: str = "hermes-main") -> dict[str, Any]:
        normalized = normalize_report_spec(spec)
        key = report_key(normalized)
        now = db_module.now_iso()
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM report_jobs WHERE report_key=?", (key,)).fetchone()
            if row:
                return _report_dict(row)
            job_id = secrets.token_hex(10)
            conn.execute(
                "INSERT INTO report_jobs(id,report_key,profile_id,report_type,ad_product,start_date,end_date,timezone,status,request_json,created_by,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, key, normalized["profile_id"], normalized["report_type"], normalized["ad_product"],
                 normalized["start_date"], normalized["end_date"], normalized["timezone"], "REQUESTED",
                 json.dumps(normalized, ensure_ascii=False, sort_keys=True), actor[:80], now, now),
            )
            conn.execute(
                "INSERT INTO report_transitions(report_job_id,from_status,to_status,data_json,actor,created_at) VALUES(?,NULL,'REQUESTED','{}',?,?)",
                (job_id, actor[:80], now),
            )
        return self.get_report_job(job_id)

    @staticmethod
    def validate_strategy_overrides(values: dict[str, Any], current: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized = original_validate(values, current)
        if "auto_write_ad_products" in normalized:
            products = normalized["auto_write_ad_products"]
            if not isinstance(products, list) or not products:
                raise ValueError("auto_write_ad_products must be a non-empty list")
            products = sorted({str(item).strip().upper() for item in products if str(item).strip()})
            unknown = set(products) - _ALLOWED_PRODUCTS
            if unknown:
                raise ValueError("unsupported autonomous ad products: " + ", ".join(sorted(unknown)))
            normalized["auto_write_ad_products"] = products
        return normalized

    def update_settings(self, updates: dict[str, Any]):
        if "auto_write_ad_products" in updates:
            updates = dict(updates)
            updates.update(validate_strategy_overrides(
                {"auto_write_ad_products": updates["auto_write_ad_products"]}, self.get_settings()
            ))
        return original_update(self, updates)

    def get_cycle(self, cycle_id: str):
        item = original_get_cycle(self, cycle_id)
        if not item:
            return item
        raw = item.pop("lineage_json", None)
        item["lineage"] = json.loads(raw) if raw else {}
        return item

    def finish_tool(self, payload: dict[str, Any]):
        tool = self.store.get_tool(str(payload.get("tool_name") or ""))
        if tool and tool.get("semantic") == "write" and not payload.get("event_id"):
            if self.store.get_settings().get("require_result_event_id", True):
                raise ValueError("write result requires event_id, decision_id and reservation_token")
            payload = dict(payload)
            payload["event_id"] = canonical_hash({
                "legacy_test": True,
                "decision_id": payload.get("decision_id"),
                "reservation_token": payload.get("reservation_token"),
                "tool_name": payload.get("tool_name"),
                "result": payload.get("result"),
            })[:32]
        return original_finish_tool(self, payload)

    Store.create_report_job = create_report_job
    Store.validate_strategy_overrides = validate_strategy_overrides
    Store.update_settings = update_settings
    Store.get_cycle = get_cycle
    Service.finish_tool = finish_tool
    _INSTALLED = True
