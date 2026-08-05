from __future__ import annotations

from collections import defaultdict, deque
import hashlib
import json
import os
import secrets
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE_URL = os.getenv("ADS_CONTROL_URL", "http://127.0.0.1:8790").rstrip("/")
TOKEN = os.getenv("ADS_CONTROL_AGENT_TOKEN", "")
OPERATOR_TOKEN = os.getenv("ADS_CONTROL_OPERATOR_TOKEN", "")
COMMAND_APPROVAL_ENABLED = os.getenv(
    "ADS_CONTROL_ENABLE_COMMAND_APPROVAL", ""
).strip().lower() in {"1", "true", "yes", "on"}

_AUTH_LOCK = threading.Lock()
_AUTH_BY_CALL: dict[str, dict[str, Any]] = {}
_AUTH_FALLBACK: dict[tuple[str, str, str], deque[dict[str, Any]]] = defaultdict(deque)
_LATEST_CATALOG_SYNC: dict[str, Any] = {}


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _authorization_ttl() -> int:
    # Align with the default decision reservation TTL so durable outbox retries
    # can recover from a transient control-plane outage without weakening the
    # one-shot binding. A process restart still fails closed.
    return _bounded_env_int("ADS_AUTHORIZATION_CACHE_TTL_SECONDS", 900, 5, 3600)


def _authorization_limit() -> int:
    return _bounded_env_int("ADS_AUTHORIZATION_CACHE_MAX_ENTRIES", 2048, 32, 20000)


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value if isinstance(value, dict) else {},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _entry_count() -> int:
    return len(_AUTH_BY_CALL) + sum(len(queue) for queue in _AUTH_FALLBACK.values())


def _purge_expired_locked(now: float) -> None:
    ttl = _authorization_ttl()
    for call_id, entry in list(_AUTH_BY_CALL.items()):
        if now - float(entry.get("created_at") or 0) > ttl:
            _AUTH_BY_CALL.pop(call_id, None)
    for key, queue in list(_AUTH_FALLBACK.items()):
        while queue and now - float(queue[0].get("created_at") or 0) > ttl:
            queue.popleft()
        if not queue:
            _AUTH_FALLBACK.pop(key, None)


def _evict_oldest_locked() -> None:
    limit = _authorization_limit()
    while _entry_count() >= limit:
        candidates: list[tuple[float, str, Any]] = []
        candidates.extend(
            (float(entry.get("created_at") or 0), "call", call_id)
            for call_id, entry in _AUTH_BY_CALL.items()
            if not entry.get("lease_token")
        )
        candidates.extend(
            (float(entry.get("created_at") or 0), "fallback", (key, index))
            for key, queue in _AUTH_FALLBACK.items()
            for index, entry in enumerate(queue)
            if not entry.get("lease_token")
        )
        if not candidates:
            return
        _, kind, key = min(candidates, key=lambda item: item[0])
        if kind == "call":
            _AUTH_BY_CALL.pop(key, None)
        else:
            fallback_key, index = key
            queue = _AUTH_FALLBACK.get(fallback_key)
            if queue and index < len(queue):
                del queue[index]
            if not queue:
                _AUTH_FALLBACK.pop(fallback_key, None)


def _remember_authorization(
    request_payload: dict[str, Any], response: dict[str, Any]
) -> None:
    if response.get("error") or response.get("allowed") is not True:
        return
    session_id = str(request_payload.get("session_id") or "")
    tool_name = str(request_payload.get("tool_name") or "")
    tool_call_id = str(request_payload.get("tool_call_id") or "")
    args_hash = _canonical_hash(request_payload.get("args"))
    entry = {
        "created_at": time.monotonic(),
        "session_id": session_id,
        "tool_name": tool_name,
        "args_hash": args_hash,
        "authorization": {
            key: response.get(key)
            for key in (
                "task_id",
                "decision_id",
                "plan_key",
                "reservation_token",
                "operation",
                "action_id",
            )
        },
    }
    with _AUTH_LOCK:
        now = time.monotonic()
        _purge_expired_locked(now)
        _evict_oldest_locked()
        if tool_call_id:
            _AUTH_BY_CALL[tool_call_id] = entry
        else:
            _AUTH_FALLBACK[(session_id, tool_name, args_hash)].append(entry)


def _lease_authorization(
    result_payload: dict[str, Any],
) -> tuple[dict[str, Any], tuple[str, Any, str] | None]:
    session_id = str(result_payload.get("session_id") or "")
    tool_name = str(result_payload.get("tool_name") or "")
    tool_call_id = str(result_payload.get("tool_call_id") or "")
    args_hash = _canonical_hash(result_payload.get("args"))
    with _AUTH_LOCK:
        now = time.monotonic()
        _purge_expired_locked(now)
        entry: dict[str, Any] | None = None
        lease: tuple[str, Any, str] | None = None
        if tool_call_id:
            candidate = _AUTH_BY_CALL.get(tool_call_id)
            if candidate and not candidate.get("lease_token") and (
                candidate.get("session_id") == session_id
                and candidate.get("tool_name") == tool_name
                and candidate.get("args_hash") == args_hash
            ):
                lease_token = secrets.token_urlsafe(12)
                candidate["lease_token"] = lease_token
                entry = candidate
                lease = ("call", tool_call_id, lease_token)
            elif candidate:
                # Reusing a call ID with different arguments is an integrity
                # failure. Invalidate it immediately rather than permitting a
                # later replay with the original arguments.
                _AUTH_BY_CALL.pop(tool_call_id, None)
        else:
            key = (session_id, tool_name, args_hash)
            queue = _AUTH_FALLBACK.get(key)
            candidate = next((item for item in queue or () if not item.get("lease_token")), None)
            if candidate:
                lease_token = secrets.token_urlsafe(12)
                candidate["lease_token"] = lease_token
                entry = candidate
                lease = ("fallback", key, lease_token)
    return (dict(entry.get("authorization") or {}) if entry else {}, lease)


def _commit_authorization(lease: tuple[str, Any, str] | None) -> None:
    if not lease:
        return
    kind, key, token = lease
    with _AUTH_LOCK:
        if kind == "call":
            entry = _AUTH_BY_CALL.get(key)
            if entry and entry.get("lease_token") == token:
                _AUTH_BY_CALL.pop(key, None)
            return
        queue = _AUTH_FALLBACK.get(key)
        if queue:
            for index, entry in enumerate(queue):
                if entry.get("lease_token") == token:
                    del queue[index]
                    break
        if not queue:
            _AUTH_FALLBACK.pop(key, None)


def _release_authorization(lease: tuple[str, Any, str] | None) -> None:
    if not lease:
        return
    kind, key, token = lease
    with _AUTH_LOCK:
        if kind == "call":
            entry = _AUTH_BY_CALL.get(key)
            if entry and entry.get("lease_token") == token:
                entry.pop("lease_token", None)
            return
        queue = _AUTH_FALLBACK.get(key)
        for entry in queue or ():
            if entry.get("lease_token") == token:
                entry.pop("lease_token", None)
                break


def _clear_session(session_id: str) -> None:
    if not session_id:
        return
    with _AUTH_LOCK:
        for call_id, entry in list(_AUTH_BY_CALL.items()):
            if entry.get("session_id") == session_id:
                _AUTH_BY_CALL.pop(call_id, None)
        for key in list(_AUTH_FALLBACK):
            if key[0] == session_id:
                _AUTH_FALLBACK.pop(key, None)


def _cache_stats() -> dict[str, Any]:
    with _AUTH_LOCK:
        _purge_expired_locked(time.monotonic())
        return {
            "protocol": 1,
            "ttl_seconds": _authorization_ttl(),
            "max_entries": _authorization_limit(),
            "pending": _entry_count(),
            "call_id_entries": len(_AUTH_BY_CALL),
            "fallback_entries": sum(len(queue) for queue in _AUTH_FALLBACK.values()),
        }


def _prepare_payload(
    method: str, path: str, payload: Any
) -> tuple[Any, tuple[str, Any, str] | None]:
    if not isinstance(payload, dict):
        return payload, None
    prepared = dict(payload)
    lease = None
    if method == "POST" and path == "/api/agent/tool-result":
        authorization, lease = _lease_authorization(prepared)
        for key in ("task_id", "decision_id", "plan_key", "reservation_token"):
            prepared.pop(key, None)
        if authorization:
            for key in ("task_id", "decision_id", "plan_key", "reservation_token"):
                value = authorization.get(key)
                if value is not None:
                    prepared[key] = value
        else:
            prepared["authorization_cache_miss"] = True
    elif method == "POST" and path == "/api/agent/runtime-status":
        state = prepared.get("state") if isinstance(prepared.get("state"), dict) else {}
        prepared["state"] = {
            **state,
            "readiness_protocol": 1,
            "catalog_sync": dict(_LATEST_CATALOG_SYNC),
            "authorization_cache": _cache_stats(),
        }
    return prepared, lease


def _decode(raw):
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"error": "invalid_control_response", "detail": str(exc)}
    return (
        data
        if isinstance(data, dict)
        else {
            "error": "invalid_control_response",
            "detail": "control response must be a JSON object",
        }
    )


def _request(method, path, payload, timeout, headers):
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    req = Request(
        BASE_URL + path,
        data=body,
        method=method,
        headers={
            **headers,
            "Content-Type": "application/json",
            "User-Agent": "hermes-amazon-ads-plugin/3.3",
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
    global _LATEST_CATALOG_SYNC
    prepared, authorization_lease = _prepare_payload(method, path, payload)
    response = _request(
        method,
        path,
        prepared,
        timeout,
        {"Authorization": f"Bearer {TOKEN}"},
    )
    if method == "POST" and path == "/api/agent/tool-result" and authorization_lease:
        if isinstance(response, dict) and not response.get("error"):
            _commit_authorization(authorization_lease)
        else:
            _release_authorization(authorization_lease)
    if method == "POST" and path == "/api/agent/tool-check" and isinstance(
        prepared, dict
    ):
        _remember_authorization(prepared, response)
    elif method == "POST" and path == "/api/agent/catalog-sync":
        _LATEST_CATALOG_SYNC = {
            key: response.get(key)
            for key in ("error", "detail", "tool_count", "created", "updated", "drifted")
            if response.get(key) is not None
        }
        _LATEST_CATALOG_SYNC["ok"] = not bool(response.get("error"))
    elif method == "POST" and path == "/api/agent/session-event" and isinstance(
        prepared, dict
    ):
        if str(prepared.get("state") or "").lower() in {"ended", "reset"}:
            _clear_session(str(prepared.get("session_id") or ""))
    elif method == "POST" and path == "/api/agent/worker-stop" and isinstance(
        prepared, dict
    ):
        _clear_session(str(prepared.get("worker_session_id") or ""))
    return response


def operator_request(method, path, payload=None, timeout=10):
    if not COMMAND_APPROVAL_ENABLED:
        return {
            "error": "command_approval_disabled",
            "detail": "Use the authenticated control Web. Enable command approval only in a restricted Hermes gateway without terminal/file/environment tools.",
        }
    if not OPERATOR_TOKEN:
        return {"error": "operator_token_unavailable"}
    return _request(
        method,
        path,
        payload,
        timeout,
        {"X-Operator-Token": OPERATOR_TOKEN},
    )


def context(session_id):
    q = "?" + urlencode({"session_id": session_id}) if session_id else ""
    return request("GET", "/api/agent/context" + q)
