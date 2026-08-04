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
    original_validate_lineage = Store.validate_snapshot_lineage
    original_finish_tool = Service.finish_tool

    def create_report_job(self, spec: dict[str, Any], actor: str = "hermes-main") -> dict[str, Any]:
        normalized = normalize_report_spec(spec)
        key = report_key(normalized)
        now = db_module.now_iso()
        retry_failed = bool(spec.get("retry_failed", False))
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute("SELECT * FROM report_jobs WHERE report_key=?", (key,)).fetchone()
                if row and retry_failed:
                    if row["status"] not in {"FAILED", "QUARANTINED"}:
                        raise ValueError("only FAILED or QUARANTINED report jobs may be explicitly retried")
                    conn.execute(
                        "UPDATE report_jobs SET status='REQUESTED',report_id=NULL,content_hash=NULL,normalized_hash=NULL,schema_hash=NULL,row_count=NULL,"
                        "attempt_count=attempt_count+1,poll_count=0,error=NULL,submitted_at=NULL,completed_at=NULL,updated_at=? WHERE id=?",
                        (now, row["id"]),
                    )
                    conn.execute(
                        "INSERT INTO report_transitions(report_job_id,from_status,to_status,data_json,actor,created_at) VALUES(?,?,'REQUESTED',?, ?,?)",
                        (row["id"], row["status"], json.dumps({"retry_failed": True}), actor[:80], now),
                    )
                    conn.commit()
                    return self.get_report_job(row["id"])
                if row:
                    conn.commit()
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
                conn.commit()
            except Exception:
                conn.rollback()
                raise
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

    def validate_snapshot_lineage(self, snapshot: dict[str, Any], lineage: dict[str, Any]):
        normalized = original_validate_lineage(self, snapshot, lineage)
        with self.connection() as conn:
            for job_id in normalized["report_job_ids"]:
                row = conn.execute(
                    "SELECT normalized_hash,content_hash,schema_hash,row_count FROM report_jobs WHERE id=?",
                    (job_id,),
                ).fetchone()
                if not row or row["normalized_hash"] != normalized["normalized_hash"]:
                    raise ValueError("snapshot hash does not match its ingested report lineage")
                if not row["content_hash"] or not row["schema_hash"] or row["row_count"] is None:
                    raise ValueError("snapshot lineage report lacks complete ingestion evidence")
        return normalized

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
    Store.validate_snapshot_lineage = validate_snapshot_lineage
    Service.finish_tool = finish_tool
    _INSTALLED = True
