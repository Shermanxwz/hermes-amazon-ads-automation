SYNC_CATALOG = {
    "name": "ads_control_sync_catalog",
    "description": "Synchronize the exact live Amazon Ads MCP tool names and JSON schemas from the Hermes registry into the control plane.",
    "parameters": {"type": "object", "properties": {}},
}
PLAN_CYCLE = {
    "name": "ads_control_plan_cycle",
    "description": "Run the deterministic Amazon Ads strategy engine on a normalized mature data snapshot. Returns a cycle and explainable decisions; it does not execute writes.",
    "parameters": {
        "type": "object",
        "properties": {
            "snapshot": {
                "type": "object",
                "description": "Normalized snapshot containing profile, source, window, account, campaigns, targets, search_terms, placements, budget_usage and recommendations.",
            },
            "policy": {"type": "object", "description": "Optional per-cycle strategy overrides."},
        },
        "required": ["snapshot"],
    },
}
CREATE_TASK = {
    "name": "ads_control_create_task",
    "description": "Create an execution task from a deterministic cycle. Returns a task ID for [ads-task:<id>] markers.",
    "parameters": {
        "type": "object",
        "properties": {
            "cycle_id": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        "required": ["cycle_id"],
    },
}
STATUS = {
    "name": "ads_control_status",
    "description": "Read role, mode, catalog health, bound task and deterministic decisions for this Hermes session.",
    "parameters": {"type": "object", "properties": {}},
}
NOTE = {
    "name": "ads_control_record_note",
    "description": "Record a concise operator-visible decision or anomaly in the audit timeline.",
    "parameters": {
        "type": "object",
        "properties": {
            "message": {"type": "string"}, "task_id": {"type": "string"},
            "level": {"type": "string", "enum": ["info", "warning", "error"]},
        },
        "required": ["message"],
    },
}
READ_EVIDENCE = {
    "name": "ads_control_read_evidence",
    "description": "Verifier-only: list recent structured Amazon read actions that can serve as independent evidence for one decision.",
    "parameters": {
        "type": "object",
        "properties": {
            "decision_id": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        "required": ["decision_id"],
    },
}
VERIFY = {
    "name": "ads_control_verify_decision",
    "description": "Verifier-only: verify one decision from a recorded, fresh, structured Amazon read action. Arbitrary model-supplied state is not accepted.",
    "parameters": {
        "type": "object",
        "properties": {
            "decision_id": {"type": "string"},
            "evidence_action_id": {"type": "integer", "minimum": 1},
            "message": {"type": "string"},
        },
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
    "description": "Ingest deduplicated Amazon Marketing Stream event envelopes into the audit/data layer.",
    "parameters": {
        "type": "object",
        "properties": {"events": {"type": "array", "items": {"type": "object"}}},
        "required": ["events"],
    },
}
