from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from typing import Any


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
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode())
        except HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode())
            except Exception:
                detail = {"error": str(exc)}
            detail["http_status"] = exc.code
            return detail
        except (URLError, TimeoutError, OSError) as exc:
            return {"error": "control_plane_unavailable", "detail": str(exc)}

    def get_context(self, session_id: str | None) -> dict[str, Any]:
        query = "?" + urlencode({"session_id": session_id}) if session_id else ""
        return self.request("GET", "/api/agent/context" + query)
