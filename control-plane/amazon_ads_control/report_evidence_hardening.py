from __future__ import annotations

import gzip
import json
import re
from typing import Any

from . import closed_loop as closed_loop_module
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
    elif isinstance(value, str):
        # MCP tool adapters may serialize a CallToolResult payload as a JSON
        # string inside the outer result object. Parse nested JSON so reportId
        # evidence remains bindable to the persistent report job.
        candidate = value.strip()
        if candidate.startswith("{") or candidate.startswith("["):
            try:
                yield from _walk(json.loads(candidate))
            except (TypeError, ValueError, json.JSONDecodeError):
                pass


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
    levels = ("campaigns", "targets", "search_terms", "placements", "budget_usage", "recommendations", "hourly", "rows")
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


def _snapshot_bytes(snapshot: dict[str, Any]) -> bytes:
    return json.dumps(
        snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")


def _stored_snapshot(row: Any) -> dict[str, Any] | None:
    compressed = row["normalized_snapshot_gzip"] if "normalized_snapshot_gzip" in row.keys() else None
    if compressed:
        return json.loads(gzip.decompress(compressed).decode("utf-8"))
    raw = row["normalized_snapshot_json"] if "normalized_snapshot_json" in row.keys() else None
    return json.loads(raw) if raw else None


def _action_result_hash(result: Any) -> str:
    if isinstance(result, dict) and result.get("_compacted") and isinstance(result.get("sha256"), str):
        return result["sha256"]
    return canonical_hash(result)


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
    original_store_transition = Store.transition_report
    original_transition = Service.transition_report
    original_validate_lineage = Store.validate_snapshot_lineage
    original_report_dict = closed_loop_module._report_dict

    def safe_report_dict(row):
        item = original_report_dict(row)
        raw = item.pop("normalized_snapshot_json", None)
        compressed = item.pop("normalized_snapshot_gzip", None)
        item["normalized_snapshot_stored"] = bool(raw or compressed)
        item["normalized_snapshot_compressed_bytes"] = len(compressed) if compressed else 0
        return item

    def init(self, path):
        original_init(self, path)
        with self.connection() as conn:
            db_module.Store._ensure_column(conn, "report_jobs", "normalized_snapshot_json", "TEXT")
            db_module.Store._ensure_column(conn, "report_jobs", "normalized_snapshot_gzip", "BLOB")

    def store_transition(self, identifier: str, new_status: str, data: dict[str, Any], actor: str = "hermes-main"):
        data = dict(data or {})
        snapshot = data.get("snapshot")
        compressed = None
        persisted = dict(data)
        if str(new_status).upper() == "VALIDATED" and isinstance(snapshot, dict):
            encoded = _snapshot_bytes(snapshot)
            compressed = gzip.compress(encoded, compresslevel=6)
            data["normalized_hash"] = snapshot_hash(snapshot)
            data["schema_hash"] = canonical_hash(data.get("schema") or _schema_fingerprint(snapshot))
            data["row_count"] = _row_count(snapshot)
            persisted = dict(data)
            persisted.pop("snapshot", None)
            persisted["snapshot_sha256"] = hashlib_sha256(encoded)
            persisted["snapshot_original_bytes"] = len(encoded)
            persisted["snapshot_compressed_bytes"] = len(compressed)
        result = original_store_transition(self, identifier, new_status, persisted, actor)
        if compressed is not None:
            with self.connection() as conn:
                conn.execute(
                    "UPDATE report_jobs SET normalized_snapshot_json=NULL,normalized_snapshot_gzip=? WHERE id=?",
                    (compressed, result["id"]),
                )
            result = self.get_report_job(result["id"]) or result
        return result

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
            data["evidence_hash"] = _action_result_hash(action["result"])
            if status == "DOWNLOADED":
                data["content_hash"] = data["evidence_hash"]
        if status == "VALIDATED":
            normalized = data.get("snapshot")
            if not isinstance(normalized, dict):
                raise ValueError("VALIDATED report requires the normalized snapshot object")
        if status == "INGESTED":
            current = self.store.get_report_job(job["id"]) or {}
            with self.store.connection() as conn:
                row = conn.execute(
                    "SELECT normalized_snapshot_json,normalized_snapshot_gzip FROM report_jobs WHERE id=?",
                    (job["id"],),
                ).fetchone()
            if not row or _stored_snapshot(row) is None:
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
        serialized = _snapshot_bytes(snapshot)
        with self.connection() as conn:
            for job_id in normalized["report_job_ids"]:
                row = conn.execute(
                    "SELECT normalized_snapshot_json,normalized_snapshot_gzip FROM report_jobs WHERE id=?",
                    (job_id,),
                ).fetchone()
                stored = _stored_snapshot(row) if row else None
                if stored is None:
                    raise ValueError("snapshot lineage report has no controller-stored normalized snapshot")
                if _snapshot_bytes(stored) != serialized:
                    raise ValueError("submitted snapshot differs from the controller-stored report snapshot")
        return normalized

    closed_loop_module._report_dict = safe_report_dict
    Store.__init__ = init
    Store.transition_report = store_transition
    Store.validate_snapshot_lineage = validate_snapshot_lineage
    Service.report_evidence = report_evidence
    Service.transition_report = transition_report
    _INSTALLED = True


def hashlib_sha256(value: bytes) -> str:
    import hashlib

    return hashlib.sha256(value).hexdigest()
