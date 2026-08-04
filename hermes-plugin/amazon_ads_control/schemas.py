SYNC_CATALOG = {
    "name": "ads_control_sync_catalog",
    "description": "Synchronize raw live Amazon Ads MCP names and JSON schemas. The controller independently derives semantic, family and risk.",
    "parameters": {"type": "object", "properties": {}},
}
CREATE_REPORT = {
    "name": "ads_control_create_report_job",
    "description": "Create or recover one persistent Amazon Ads report job from a stable profile/type/window/schema key.",
    "parameters": {
        "type": "object",
        "properties": {"spec": {"type": "object"}},
        "required": ["spec"],
    },
}
TRANSITION_REPORT = {
    "name": "ads_control_transition_report",
    "description": "Advance one persistent report through the enforced lifecycle. INGESTED requires content, normalized and schema hashes plus row_count.",
    "parameters": {
        "type": "object",
        "properties": {
            "report_job_id": {"type": "string"},
            "status": {"type": "string", "enum": ["SUBMITTED", "IN_PROGRESS", "SUCCEEDED", "DOWNLOADED", "VALIDATED", "INGESTED", "FAILED", "QUARANTINED"]},
            "data": {"type": "object"},
        },
        "required": ["report_job_id", "status"],
    },
}
PLAN_CYCLE = {
    "name": "ads_control_plan_cycle",
    "description": "Run deterministic strategy only on a mature normalized snapshot cryptographically tied to one or more INGESTED report jobs.",
    "parameters": {
        "type": "object",
        "properties": {
            "snapshot": {"type": "object"},
            "lineage": {
                "type": "object",
                "properties": {
                    "report_job_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    "action_ids": {"type": "array", "items": {"type": "integer"}},
                    "normalized_hash": {"type": "string"},
                },
                "required": ["report_job_ids"],
            },
            "policy": {"type": "object", "description": "Optional validated per-cycle strategy overrides."},
        },
        "required": ["snapshot", "lineage"],
    },
}
CREATE_TASK = {
    "name": "ads_control_create_task",
    "description": "Create an execution task from a deterministic lineage-backed cycle.",
    "parameters": {
        "type": "object",
        "properties": {"cycle_id": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}},
        "required": ["cycle_id"],
    },
}
STATUS = {
    "name": "ads_control_status",
    "description": "Read role, mode, catalog, report lifecycle, runtime health and bound deterministic decisions.",
    "parameters": {"type": "object", "properties": {}},
}
NOTE = {
    "name": "ads_control_record_note",
    "description": "Record a concise operator-visible decision or anomaly in the audit timeline.",
    "parameters": {
        "type": "object",
        "properties": {"message": {"type": "string"}, "task_id": {"type": "string"}, "level": {"type": "string", "enum": ["info", "warning", "error"]}},
        "required": ["message"],
    },
}
PREPARE_WRITE = {
    "name": "ads_control_prepare_write",
    "description": "Executor-only: bind a fresh structured Amazon read to one planned decision and verify the current value still equals the planned before value.",
    "parameters": {
        "type": "object",
        "properties": {"decision_id": {"type": "string"}, "evidence_action_id": {"type": "integer", "minimum": 1}},
        "required": ["decision_id", "evidence_action_id"],
    },
}
READ_EVIDENCE = {
    "name": "ads_control_read_evidence",
    "description": "Verifier-only: list recent structured Amazon read actions for one decision.",
    "parameters": {
        "type": "object",
        "properties": {"decision_id": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}},
        "required": ["decision_id"],
    },
}
VERIFY = {
    "name": "ads_control_verify_decision",
    "description": "Verifier-only: verify expected fields inside the single exact entity object from a fresh recorded Amazon read; sibling rows cannot satisfy the comparison.",
    "parameters": {
        "type": "object",
        "properties": {"decision_id": {"type": "string"}, "evidence_action_id": {"type": "integer", "minimum": 1}, "message": {"type": "string"}},
        "required": ["decision_id", "evidence_action_id"],
    },
}
FINALIZE = {
    "name": "ads_control_finalize_task",
    "description": "Finalize a task only after every decision is independently verified or recorded as an issue.",
    "parameters": {
        "type": "object",
        "properties": {"task_id": {"type": "string"}, "summary": {"type": "string"}},
        "required": ["task_id", "summary"],
    },
}
STREAM = {
    "name": "ads_control_ingest_stream_events",
    "description": "Ingest deduplicated Amazon Marketing Stream envelopes for risk monitoring and audit.",
    "parameters": {
        "type": "object",
        "properties": {"events": {"type": "array", "items": {"type": "object"}}},
        "required": ["events"],
    },
}
