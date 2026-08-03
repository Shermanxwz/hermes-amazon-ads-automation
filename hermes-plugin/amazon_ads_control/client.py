from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE_URL = os.getenv("ADS_CONTROL_URL", "http://127.0.0.1:8790").rstrip("/")
TOKEN = os.getenv("ADS_CONTROL_AGENT_TOKEN", "")


def request(method, path, payload=None, timeout=4):
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    req = Request(BASE_URL + path, data=body, method=method, headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except HTTPError as exc:
        try: data = json.loads(exc.read().decode())
        except Exception: data = {"error": str(exc)}
        data["http_status"] = exc.code
        return data
    except (URLError, TimeoutError, OSError) as exc:
        return {"error": "control_plane_unavailable", "detail": str(exc)}


def context(session_id):
    q = "?" + urlencode({"session_id": session_id}) if session_id else ""
    return request("GET", "/api/agent/context" + q)
