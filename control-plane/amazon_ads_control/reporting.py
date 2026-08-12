from __future__ import annotations

from datetime import date
import hashlib
import json
from typing import Any

REPORT_STATES = {
    "REQUESTED", "SUBMITTED", "IN_PROGRESS", "SUCCEEDED", "DOWNLOADED",
    "VALIDATED", "INGESTED", "FAILED", "QUARANTINED",
}
TERMINAL_REPORT_STATES = {"INGESTED", "FAILED", "QUARANTINED"}
REPORT_TRANSITIONS: dict[str, set[str]] = {
    "REQUESTED": {"SUBMITTED", "FAILED", "QUARANTINED"},
    "SUBMITTED": {"IN_PROGRESS", "SUCCEEDED", "FAILED", "QUARANTINED"},
    "IN_PROGRESS": {"IN_PROGRESS", "SUCCEEDED", "FAILED", "QUARANTINED"},
    "SUCCEEDED": {"DOWNLOADED", "FAILED", "QUARANTINED"},
    "DOWNLOADED": {"VALIDATED", "FAILED", "QUARANTINED"},
    "VALIDATED": {"INGESTED", "FAILED", "QUARANTINED"},
    "INGESTED": {"INGESTED"},
    "FAILED": set(),
    "QUARANTINED": set(),
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def normalize_report_spec(spec: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise ValueError("report spec must be an object")
    profile_id = str(spec.get("profile_id") or spec.get("profileId") or "").strip()
    report_type = str(spec.get("report_type") or spec.get("reportTypeId") or "").strip()
    start = str(spec.get("start_date") or spec.get("startDate") or "").strip()
    end = str(spec.get("end_date") or spec.get("endDate") or "").strip()
    timezone = str(spec.get("timezone") or spec.get("timeZone") or "UTC").strip()
    if not profile_id or not report_type or not start or not end or not timezone:
        raise ValueError("report profile_id, report_type, start_date, end_date and timezone are required")
    try:
        start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError as exc:
        raise ValueError("report dates must use YYYY-MM-DD") from exc
    if start_date > end_date:
        raise ValueError("report start_date cannot exceed end_date")
    columns = spec.get("columns") if isinstance(spec.get("columns"), list) else []
    filters = spec.get("filters") if isinstance(spec.get("filters"), list) else []
    group_by = spec.get("group_by") or spec.get("groupBy") or []
    if not isinstance(group_by, list):
        group_by = [group_by]
    normalized = {
        "profile_id": profile_id,
        "report_type": report_type,
        "start_date": start,
        "end_date": end,
        "timezone": timezone,
        "ad_product": str(spec.get("ad_product") or spec.get("adProduct") or "").upper(),
        "columns": sorted({str(item) for item in columns if str(item).strip()}),
        "filters": sorted(
            [item for item in filters if isinstance(item, dict)],
            key=canonical_json,
        ),
        "group_by": sorted({str(item) for item in group_by if str(item).strip()}),
        "format": str(spec.get("format") or "GZIP_JSON").upper(),
    }
    return normalized


def report_key(spec: dict[str, Any]) -> str:
    return canonical_hash(normalize_report_spec(spec))[:40]


def validate_transition(current: str, new: str) -> None:
    current, new = str(current).upper(), str(new).upper()
    if current not in REPORT_STATES or new not in REPORT_STATES:
        raise ValueError("invalid report lifecycle state")
    if new not in REPORT_TRANSITIONS[current]:
        raise ValueError(f"invalid report transition {current} -> {new}")


def validate_ingested_payload(payload: dict[str, Any]) -> None:
    required = ("content_hash", "normalized_hash", "schema_hash", "row_count")
    missing = [name for name in required if payload.get(name) in (None, "")]
    if missing:
        raise ValueError("INGESTED report requires " + ", ".join(missing))
    row_count = payload.get("row_count")
    if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count < 0:
        raise ValueError("report row_count must be a non-negative integer")
    for name in ("content_hash", "normalized_hash", "schema_hash"):
        value = str(payload.get(name) or "")
        if len(value) < 16:
            raise ValueError(f"{name} is too short to identify report content safely")


def snapshot_hash(snapshot: dict[str, Any]) -> str:
    clean = dict(snapshot)
    clean.pop("lineage", None)
    return canonical_hash(clean)


def lineage_payload(snapshot: dict[str, Any], lineage: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(lineage, dict):
        raise ValueError("snapshot lineage must be an object")
    report_job_ids = lineage.get("report_job_ids")
    action_ids = lineage.get("action_ids", [])
    if not isinstance(report_job_ids, list) or not report_job_ids:
        raise ValueError("snapshot lineage requires at least one ingested report_job_id")
    if not isinstance(action_ids, list):
        raise ValueError("snapshot lineage action_ids must be an array")
    computed = snapshot_hash(snapshot)
    declared = str(lineage.get("normalized_hash") or computed)
    if declared != computed:
        raise ValueError("snapshot normalized_hash does not match the submitted snapshot")
    return {
        "report_job_ids": sorted({str(item) for item in report_job_ids if str(item).strip()}),
        "action_ids": sorted({int(item) for item in action_ids}),
        "normalized_hash": computed,
        "source": str(lineage.get("source") or "amazon-report-pipeline"),
    }
