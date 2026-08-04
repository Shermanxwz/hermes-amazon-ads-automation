SYNC_CATALOG = {
    "name": "ads_control_sync_catalog",
    "description": "Synchronize raw live Amazon Ads MCP names and JSON schemas. The controller independently derives semantic, family and risk.",
    "parameters": {"type": "object", "properties": {}},
}
CREATE_REPORT = {
    "name": "ads_control_create_report_job",
    "description": "Create or recover one persistent Amazon Ads report job from a stable profile/type/window/schema key.",
    "parameters": {"type": "object", "properties": {"spec": {"type": "object"}}, "required": ["spec"]},
}
REPORT_EVIDENCE = {
    "name": "ads_control_report_evidence",
    "description": "List successful structured Amazon report actions for the current Hermes session so lifecycle transitions can cite exact evidence_action_id values.",
    "parameters": {
        "type": "object",
        "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}},
    },
}
TRANSITION_REPORT = {
    "name": "ads_control_transition_report",
    "description": "Advance one persistent report. Amazon-driven states require a recorded report action; VALIDATED requires the normalized snapshot; the controller computes all hashes.",
    "parameters": {
        "type": "object",
        "properties": {
            "report_job_id": {"type": "string"},
            "status": {"type": "string", "enum": ["SUBMITTED", "IN_PROGRESS", "SUCCEEDED", "DOWNLOADED", "VALIDATED", "INGESTED", "FAILED", "QUARANTINED"]},
            "evidence_action_id": {"type": "integer", "minimum": 1},
            "data": {"type": "object"},
        },
        "required": ["report_job_id", "status"],
    },
}
PLAN_CYCLE = {
    "name": "ads_control_plan_cycle",
    "description": "Run deterministic strategy only on the exact controller-stored normalized snapshot of one or more INGESTED report jobs.",
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
MANAGED_PLAN = {
    "name": "ads_control_create_managed_plan",
    "description": "Create a payload-bound high-risk structural plan from live Amazon MCP schemas. The AI can request approval but can never approve its own plan. Later actions may reference an earlier action's resolved Amazon ID with {{decision:<plan_key>.entity_id}} and must declare that plan_key in depends_on.",
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 240},
            "objective": {"type": "string", "maxLength": 8000},
            "profile": {
                "type": "object",
                "properties": {
                    "profile_id": {"type": "string"},
                    "name": {"type": "string"},
                    "marketplace": {"type": "string"},
                    "country_code": {"type": "string"},
                    "currency": {"type": "string"},
                },
                "required": ["profile_id"],
            },
            "actions": {
                "type": "array",
                "minItems": 1,
                "maxItems": 50,
                "items": {
                    "type": "object",
                    "properties": {
                        "tool_name": {"type": "string"},
                        "action_type": {"type": "string"},
                        "entity_type": {"type": "string"},
                        "entity_id": {"type": "string"},
                        "plan_key": {"type": "string", "minLength": 1, "maxLength": 240},
                        "depends_on": {
                            "type": "array",
                            "maxItems": 49,
                            "uniqueItems": True,
                            "items": {"type": "string", "minLength": 1, "maxLength": 240},
                        },
                        "arguments": {"type": "object"},
                        "expected_state": {"type": "object", "minProperties": 1},
                        "reason": {"type": "string"},
                        "evidence": {"type": "object"},
                        "risk": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                        "maximum_daily_budget": {"type": "number", "minimum": 0},
                        "priority": {"type": "integer"},
                    },
                    "required": ["tool_name", "action_type", "arguments", "expected_state"],
                },
            },
            "approval_summary": {"type": "string", "maxLength": 1000},
            "approval_ttl_minutes": {"type": "integer", "minimum": 1, "maximum": 1440},
        },
        "required": ["title", "profile", "actions"],
    },
}
REQUEST_APPROVAL = {
    "name": "ads_control_request_approval",
    "description": "Move an existing deterministic task into payload-bound operator approval. This tool only requests approval; it cannot approve.",
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "summary": {"type": "string", "maxLength": 1000},
            "ttl_minutes": {"type": "integer", "minimum": 1, "maximum": 1440},
        },
        "required": ["task_id"],
    },
}
STATUS = {
    "name": "ads_control_status",
    "description": "Read role, mode, catalog, report lifecycle, approvals, runtime health and bound deterministic decisions.",
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
    "parameters": {"type": "object", "properties": {"events": {"type": "array", "items": {"type": "object"}}}, "required": ["events"]},
}
