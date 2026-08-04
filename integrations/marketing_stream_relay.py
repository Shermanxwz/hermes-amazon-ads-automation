#!/usr/bin/env python3
"""Small Amazon Marketing Stream relay for Lambda/container use.

Accepts EventBridge/SQS/SNS style event envelopes, normalizes them, and posts them to the loopback
control API. It never performs advertising writes. Transient HTTP failures are retried; permanent
failures are raised so the upstream queue/Lambda runtime can redeliver rather than lose events.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

CONTROL_URL = os.getenv("ADS_CONTROL_URL", "http://127.0.0.1:8790").rstrip("/")
TOKEN = os.getenv("ADS_CONTROL_AGENT_TOKEN", "")
TIMEOUT_SECONDS = max(1, int(os.getenv("ADS_STREAM_RELAY_TIMEOUT", "15")))
MAX_ATTEMPTS = max(1, min(5, int(os.getenv("ADS_STREAM_RELAY_ATTEMPTS", "3"))))


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
                    parsed = {"raw": parsed["Message"]}
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


def _decode(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"control plane returned invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("control plane response must be a JSON object")
    return value


def relay(event: Any) -> dict[str, Any]:
    events = [normalize(item) for item in _unwrap(event)]
    if not events:
        return {"inserted": 0, "duplicates": 0}
    if len(TOKEN) < 32:
        raise RuntimeError("ADS_CONTROL_AGENT_TOKEN is missing or too short")
    body = json.dumps({"events": events}, ensure_ascii=False).encode()
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        request = Request(
            CONTROL_URL + "/api/agent/stream-events", data=body, method="POST",
            headers={
                "Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json",
                "User-Agent": "hermes-amazon-ads-stream-relay/2.1",
            },
        )
        try:
            with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                return _decode(response.read())
        except HTTPError as exc:
            last_error = exc
            if exc.code != 429 and exc.code < 500:
                detail = exc.read().decode("utf-8", errors="replace")[:1000]
                raise RuntimeError(f"control plane rejected stream events with HTTP {exc.code}: {detail}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc
        if attempt < MAX_ATTEMPTS:
            time.sleep(min(2.0, 0.25 * (2 ** (attempt - 1))))
    raise RuntimeError(f"unable to relay Marketing Stream events after {MAX_ATTEMPTS} attempts: {last_error}")


def lambda_handler(event, context):
    return relay(event)
