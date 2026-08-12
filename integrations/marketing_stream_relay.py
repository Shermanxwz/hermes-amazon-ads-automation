#!/usr/bin/env python3
"""Amazon Marketing Stream relay for Lambda/container use.

Accept EventBridge/SQS/SNS envelopes, normalize them, and post them to the
loopback control API. It never performs advertising writes. Profile binding is
explicit: an event-supplied Profile wins; otherwise the deployment must provide
ADS_STREAM_PROFILE_ID. This prevents advertiser-only stream records from being
stored without the Profile boundary used by the controller.
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
PROFILE_ID = os.getenv("ADS_STREAM_PROFILE_ID", "").strip()
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


def _first(payload: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = payload.get(name)
        if value not in (None, ""):
            return value
    return None


def _event_time(payload: dict[str, Any], event: dict[str, Any]) -> Any:
    direct = _first(payload, "timeWindowStart", "eventTime", "event_time", "timestamp")
    if direct not in (None, ""):
        return direct
    if event.get("time") not in (None, ""):
        return event.get("time")
    date = _first(payload, "date", "eventDate", "metricDate")
    hour = _first(payload, "hour", "hourOfDay")
    if isinstance(date, str) and date:
        try:
            hour_value = int(hour) if hour not in (None, "") else 0
        except (TypeError, ValueError):
            hour_value = 0
        if 0 <= hour_value <= 23:
            return f"{date}T{hour_value:02d}:00:00"
    return None


def normalize(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("detail") if isinstance(event.get("detail"), dict) else event
    profile_id = str(
        _first(payload, "profileId", "profile_id", "advertisingProfileId")
        or _first(event, "profileId", "profile_id", "advertisingProfileId")
        or PROFILE_ID
        or ""
    ).strip()
    if not profile_id:
        raise RuntimeError(
            "Marketing Stream event has no Profile id; configure ADS_STREAM_PROFILE_ID "
            "for the exact controller-bound Profile"
        )
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    idempotency = _first(
        payload,
        "idempotency_id",
        "idempotencyId",
        "idempotencyID",
        "messageId",
        "message_id",
    )
    dedupe = str(idempotency or event.get("id") or "").strip()
    if not dedupe:
        dedupe = hashlib.sha256(canonical.encode()).hexdigest()
    return {
        "profile_id": profile_id,
        "dataset_id": _first(payload, "datasetId", "dataset_id", "dataset") or "unknown",
        "event_time": _event_time(payload, event),
        "dedupe_key": dedupe,
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
    source = _unwrap(event)
    if not source:
        return {"inserted": 0, "duplicates": 0}
    events = [normalize(item) for item in source]
    if len(TOKEN) < 32:
        raise RuntimeError("ADS_CONTROL_AGENT_TOKEN is missing or too short")
    body = json.dumps({"events": events}, ensure_ascii=False).encode()
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        request = Request(
            CONTROL_URL + "/api/agent/stream-events",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "Content-Type": "application/json",
                "User-Agent": "hermes-amazon-ads-stream-relay/4.2",
            },
        )
        try:
            with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                return _decode(response.read())
        except HTTPError as exc:
            last_error = exc
            if exc.code != 429 and exc.code < 500:
                detail = exc.read().decode("utf-8", errors="replace")[:1000]
                raise RuntimeError(
                    f"control plane rejected stream events with HTTP {exc.code}: {detail}"
                ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc
        if attempt < MAX_ATTEMPTS:
            time.sleep(min(2.0, 0.25 * (2 ** (attempt - 1))))
    raise RuntimeError(
        f"unable to relay Marketing Stream events after {MAX_ATTEMPTS} attempts: {last_error}"
    )


def lambda_handler(event, context):
    return relay(event)
