from __future__ import annotations

import json
import re
from typing import Any

from . import db as db_module
from . import service as service_module
from .evidence import canonical_hash
from .reporting import snapshot_hash

_INSTALLED = False
_AMAZON_STATES = {"SUBMITTED", "IN_PROGRESS", "SUCCEEDED", "DOWNLOADED"}
_REPORT_ID_KEYS = {"reportid", "report_id", "id"}


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9_]", "", str(value).lower())


def _report_ids(value: Any) -> set[str]:
    found: set[str] = set()
    for obj in _walk(value):
        for name, item in obj.items():
            if _key(name) in _REPORT_ID_KEYS and isinstance(item, (str, int)) and str(item).strip():
                found.add(str(item).strip())
    return found


def _row_count(snapshot: dict[str, Any]) -> int:
    levels = ("campaigns", "targets", "search_terms", "placements", "budget_usage", "recommendations", "hourly")
    return sum(len(snapshot.get(level, [])) for level in levels if isinstance(snapshot.get(level), list))


def _schema_fingerprint(snapshot: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for level in ("account", "campaigns", "targets", "search_terms", "placements", "budget_usage", "recommendations", "hourly"):
        value = snapshot.get(level)
        if isinstance(value, dict):
            result[level] = sorted(value)
        elif isinstance(value, list):
            keys: set[str] = set()
            for row in value:
                if isinstance(row, dict):
                    keys.update(str(item) for item in row)
            result[level] = sorted(keys)
    return result


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    db_module.SAFETY_LOCKED_SETTINGS["require_report_action_evidence"] = True
    db_module.DEFAULT_SETTINGS["require_report_action_evidence"] = True
    db_module.BOOLEAN_SETTINGS.add("require_report_action_evidence")

    Store = db_module.Store
    Service = service_module.ControlService
    original_init = Store.__init__
    original_transition = Service.transition_report
    original_validate_lineage = Store.validate_snapshot_lineage

    def init(self, path):
        original_init(self, path)
        with self.connection() as conn:
            db_module.Store._ensure_column(conn, "report_jobs", "normalized_snapshot_json", "TEXT")

    def report_evidence(self, payload: dict[str, Any]) -> dict[str, Any]:
        session_id = str(payload.get("session_id") or "").strip()
        if not session_id:
            raise ValueError("session_id is required")
        limit = min(100, max(1, int(payload.get("limit") or 20)))
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT a.id,a.tool_name,a.operation,a.outcome_status,a.result_summary,a.created_at "
                "FROM actions a JOIN mcp_tools t ON t.registered_name=a.tool_name "
                "WHERE a.session_id=? AND a.phase='after' AND a.allowed=1 AND a.structured_result=1 "
                "AND a.result_json IS NOT NULL AND t.family='report' AND a.operation IN ('job','read') "
                "ORDER BY a.id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return {"session_id": session_id, "evidence": [dict(row) for row in rows]}

    def transition_report(self, payload: dict[str, Any]):
        identifier = str(payload.get("report_job_id") or payload.get("report_key") or payload.get("report_id") or "")
        if not identifier:
            raise ValueError("report_job_id, report_key or report_id is required")
        status = str(payload.get("status") or "").upper()
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        data = dict(data)
        job = self.store.get_report_job(identifier)
        if not job:
            raise KeyError("report job not found")
        settings = self.store.get_settings()
        if settings.get("require_report_action_evidence", True) and status in _AMAZON_STATES:
            try:
                action_id = int(payload.get("evidence_action_id") or data.get("evidence_action_id"))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{status} report transition requires evidence_action_id") from exc
            session_id = str(payload.get("session_id") or "").strip()
            if not session_id:
                raise ValueError(f"{status} report transition requires session_id")
            action = self.store.get_action(action_id)
            if not action or action.get("session_id") != session_id:
                raise ValueError("report evidence must belong to the current Hermes session")
            if (action.get("phase") != "after" or action.get("operation") not in {"job", "read"}
                    or not action.get("allowed") or action.get("structured_result") is not True
                    or not isinstance(action.get("result"), (dict, list))):
                raise ValueError("report evidence must be a successful structured Amazon report action")
            tool = self.store.get_tool(str(action.get("tool_name") or ""))
            if not tool or tool.get("family") != "report":
                raise ValueError("report evidence tool family must be report")
            ids = _report_ids(action["result"])
            known = str(job.get("report_id") or data.get("report_id") or "").strip()
            if status == "SUBMITTED" and not known:
                if len(ids) != 1:
                    raise ValueError("report submission evidence must contain exactly one report ID")
                known = next(iter(ids))
                data["report_id"] = known
            elif known and known not in ids:
                raise ValueError("report evidence does not contain the persistent report ID")
            data["evidence_action_id"] = action_id
            data["evidence_hash"] = canonical_hash(action["result"])
            if status == "DOWNLOADED":
                data["content_hash"] = data["evidence_hash"]
        if status == "VALIDATED":
            normalized = data.get("snapshot")
            if not isinstance(normalized, dict):
                raise ValueError("VALIDATED report requires the normalized snapshot object")
            computed_hash = snapshot_hash(normalized)
            data["normalized_hash"] = computed_hash
            data["schema_hash"] = canonical_hash(data.get("schema") or _schema_fingerprint(normalized))
            data["row_count"] = _row_count(normalized)
            with self.store.connection() as conn:
                conn.execute(
                    "UPDATE report_jobs SET normalized_snapshot_json=? WHERE id=?",
                    (json.dumps(normalized, ensure_ascii=False, sort_keys=True, default=str), job["id"]),
                )
        if status == "INGESTED":
            current = self.store.get_report_job(job["id"]) or {}
            with self.store.connection() as conn:
                row = conn.execute("SELECT normalized_snapshot_json FROM report_jobs WHERE id=?", (job["id"],)).fetchone()
            if not row or not row["normalized_snapshot_json"]:
                raise ValueError("INGESTED report requires a previously validated normalized snapshot")
            data.update({
                "content_hash": current.get("content_hash"),
                "normalized_hash": current.get("normalized_hash"),
                "schema_hash": current.get("schema_hash"),
                "row_count": current.get("row_count"),
            })
        return original_transition(self, {
            **payload,
            "report_job_id": job["id"],
            "status": status,
            "data": data,
        })

    def validate_snapshot_lineage(self, snapshot: dict[str, Any], lineage: dict[str, Any]):
        normalized = original_validate_lineage(self, snapshot, lineage)
        serialized = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)
        with self.connection() as conn:
            for job_id in normalized["report_job_ids"]:
                row = conn.execute("SELECT normalized_snapshot_json FROM report_jobs WHERE id=?", (job_id,)).fetchone()
                if not row or not row["normalized_snapshot_json"]:
                    raise ValueError("snapshot lineage report has no controller-stored normalized snapshot")
                stored = json.loads(row["normalized_snapshot_json"])
                if json.dumps(stored, ensure_ascii=False, sort_keys=True, default=str) != serialized:
                    raise ValueError("submitted snapshot differs from the controller-stored report snapshot")
        return normalized

    Store.__init__ = init
    Store.validate_snapshot_lineage = validate_snapshot_lineage
    Service.report_evidence = report_evidence
    Service.transition_report = transition_report
    _INSTALLED = True
