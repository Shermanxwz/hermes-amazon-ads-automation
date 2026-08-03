CREATE_TASK = {
    "name": "ads_control_create_task",
    "description": "Create an observable Amazon Ads task before delegating execution to a Hermes worker. Returns a task ID that must be included in the worker goal as [ads-task:<id>].",
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "kind": {"type": "string", "enum": ["audit", "optimization", "recovery", "report", "maintenance"]},
            "objective": {"type": "string"},
            "write_allowed": {"type": "boolean"},
            "constraints": {"type": "object"},
            "evidence": {"type": "object"},
            "expected_actions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "idempotency_key": {"type": "string"},
                        "tool_contains": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
                        "entity_id": {"type": "string"},
                        "field": {"type": "string"},
                        "before": {},
                        "after": {},
                        "reason": {"type": "string"}
                    },
                    "required": ["idempotency_key", "tool_contains", "reason"]
                }
            },
        },
        "required": ["title", "kind", "objective"],
    },
}
STATUS = {
    "name": "ads_control_status",
    "description": "Read the current control-plane role, mode, task, and guardrails for this Hermes session.",
    "parameters": {"type": "object", "properties": {}},
}
TASK = {
    "name": "ads_control_task",
    "description": "Read one task from the Amazon Ads control plane.",
    "parameters": {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]},
}
NOTE = {
    "name": "ads_control_record_note",
    "description": "Record a concise decision, anomaly, or verification note in the immutable daily activity timeline.",
    "parameters": {
        "type": "object",
        "properties": {"message": {"type": "string"}, "task_id": {"type": "string"}, "level": {"type": "string", "enum": ["info", "warning", "error"]}},
        "required": ["message"],
    },
}

COMPLETE = {
    "name": "ads_control_complete_task",
    "description": "Complete the currently bound Worker task after read-back verification. Workers must call this before returning their final summary.",
    "parameters": {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["completed", "failed"]},
            "summary": {"type": "string"},
            "verification": {"type": "object"}
        },
        "required": ["status", "summary", "verification"]
    }
}
