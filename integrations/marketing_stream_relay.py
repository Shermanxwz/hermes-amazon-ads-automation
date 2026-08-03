#!/usr/bin/env python3
"""Small Amazon Marketing Stream relay for Lambda/container use.

Accepts EventBridge/SQS/SNS style event envelopes, normalizes them, and posts them to the loopback
control API. It never performs advertising writes.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any
from urllib.request import Request, urlopen

CONTROL_URL = os.getenv("ADS_CONTROL_URL", "http://127.0.0.1:8790").rstrip("/")
TOKEN = os.getenv("ADS_CONTROL_AGENT_TOKEN", "")


def _unwrap(event: Any) -> list[dict[str, Any]]:
    if isinstance(event, dict) and isinstance(event.get("Records"), list):
        output = []
        for record in event["Records"]:
            body = record.get("body") if isinstance(record, dict) else None
            if isinstance(body, str):
                try:
                    parsed = json.loads(body)
                except json.JSONDecodeError:
                    parsed = {"raw": body}
            else:
                parsed = record
            if isinstance(parsed, dict) and isinstance(parsed.get("Message"), str):
                try:
                    parsed = json.loads(parsed["Message"])
                except json.JSONDecodeError:
                    pass
            output.extend(_unwrap(parsed))
        return output
    if isinstance(event, list):
        return [item for item in event if isinstance(item, dict)]
    return [event] if isinstance(event, dict) else []


def normalize(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("detail") if isinstance(event.get("detail"), dict) else event
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return {
        "profile_id": payload.get("profileId") or payload.get("profile_id"),
        "dataset_id": payload.get("datasetId") or payload.get("dataset_id") or payload.get("dataset") or "unknown",
        "event_time": payload.get("timeWindowStart") or payload.get("eventTime") or event.get("time"),
        "dedupe_key": event.get("id") or hashlib.sha256(canonical.encode()).hexdigest(),
        "payload": payload,
    }


def relay(event: Any) -> dict[str, Any]:
    events = [normalize(item) for item in _unwrap(event)]
    if not events:
        return {"inserted": 0, "duplicates": 0}
    body = json.dumps({"events": events}, ensure_ascii=False).encode()
    request = Request(
        CONTROL_URL + "/api/agent/stream-events", data=body, method="POST",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode())


def lambda_handler(event, context):
    return relay(event)
