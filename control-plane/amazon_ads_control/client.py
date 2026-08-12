from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from typing import Any


def _decode(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"error": "invalid_control_response", "detail": str(exc)}
    if not isinstance(value, dict):
        return {"error": "invalid_control_response", "detail": "control response must be a JSON object"}
    return value


class ControlClient:
    def __init__(self, base_url: str, token: str, timeout: float = 4.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
        request = Request(
            self.base_url + path,
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "hermes-amazon-ads-control/2.1",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                result = _decode(response.read())
                result.setdefault("http_status", response.status)
                return result
        except HTTPError as exc:
            detail = _decode(exc.read())
            detail["http_status"] = exc.code
            return detail
        except (URLError, TimeoutError, OSError) as exc:
            return {"error": "control_plane_unavailable", "detail": str(exc)}

    def get_context(self, session_id: str | None) -> dict[str, Any]:
        query = "?" + urlencode({"session_id": session_id}) if session_id else ""
        return self.request("GET", "/api/agent/context" + query)
