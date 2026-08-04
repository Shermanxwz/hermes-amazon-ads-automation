from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE_URL = os.getenv("ADS_CONTROL_URL", "http://127.0.0.1:8790").rstrip("/")
TOKEN = os.getenv("ADS_CONTROL_AGENT_TOKEN", "")
OPERATOR_TOKEN = os.getenv("ADS_CONTROL_OPERATOR_TOKEN", "")


def _decode(raw):
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"error": "invalid_control_response", "detail": str(exc)}
    return data if isinstance(data, dict) else {
        "error": "invalid_control_response", "detail": "control response must be a JSON object",
    }


def _request(method, path, payload, timeout, headers):
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    req = Request(
        BASE_URL + path, data=body, method=method,
        headers={
            **headers,
            "Content-Type": "application/json",
            "User-Agent": "hermes-amazon-ads-plugin/3.2",
        },
    )
    try:
        with urlopen(req, timeout=timeout) as response:
            data = _decode(response.read())
            data.setdefault("http_status", response.status)
            return data
    except HTTPError as exc:
        data = _decode(exc.read())
        data["http_status"] = exc.code
        return data
    except (URLError, TimeoutError, OSError) as exc:
        return {"error": "control_plane_unavailable", "detail": str(exc)}


def request(method, path, payload=None, timeout=4):
    return _request(
        method, path, payload, timeout,
        {"Authorization": f"Bearer {TOKEN}"},
    )


def operator_request(method, path, payload=None, timeout=10):
    if not OPERATOR_TOKEN:
        return {"error": "operator_token_unavailable"}
    return _request(
        method, path, payload, timeout,
        {"X-Operator-Token": OPERATOR_TOKEN},
    )


def context(session_id):
    q = "?" + urlencode({"session_id": session_id}) if session_id else ""
    return request("GET", "/api/agent/context" + q)
