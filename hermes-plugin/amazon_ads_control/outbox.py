from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any, Callable

UTC = timezone.utc
_LOCK = threading.RLock()


def _default_path() -> Path:
    configured = os.getenv("ADS_CONTROL_OUTBOX_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    state_home = os.getenv("XDG_STATE_HOME", "").strip()
    root = Path(state_home).expanduser() if state_home else Path.home() / ".local" / "state"
    return root / "hermes-amazon-ads" / "control-results.jsonl"


def _path() -> Path:
    return _default_path()


def _event(payload: dict[str, Any]) -> dict[str, Any]:
    body = dict(payload)
    if not body.get("event_id"):
        material = {
            "tool_call_id": body.get("tool_call_id"),
            "reservation_token": body.get("reservation_token"),
            "decision_id": body.get("decision_id"),
            "tool_name": body.get("tool_name"),
            "result": body.get("result"),
        }
        body["event_id"] = hashlib.sha256(
            json.dumps(
                material,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()[:32]
    body.setdefault("recorded_at", datetime.now(UTC).isoformat(timespec="seconds"))
    return body


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        # Corrupt outbox data is preserved for forensic recovery and the active
        # process fails closed by refusing to silently discard it.
        return []
    return rows


def _write(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n"
        for row in rows
    )
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def enqueue(payload: dict[str, Any]) -> dict[str, Any]:
    event = _event(payload)
    path = _path()
    with _LOCK:
        rows = _read(path)
        if not any(row.get("event_id") == event["event_id"] for row in rows):
            rows.append(event)
            _write(path, rows)
    return event


def pending_count() -> int:
    with _LOCK:
        return len(_read(_path()))


def deliver(
    payload: dict[str, Any],
    sender: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    event = _event(payload)
    try:
        result = sender(event)
    except Exception as exc:  # Hermes hook must never lose the callback.
        result = {"error": "control_plane_unavailable", "detail": str(exc)}
    if not isinstance(result, dict) or result.get("error"):
        enqueue(event)
        return {
            "queued": True,
            "event_id": event["event_id"],
            "error": (result or {}).get("error") if isinstance(result, dict) else "invalid_response",
        }
    return {
        "queued": False,
        "event_id": event["event_id"],
        "response": result,
    }


def flush(
    sender: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    limit: int = 100,
) -> dict[str, Any]:
    path = _path()
    with _LOCK:
        rows = _read(path)
        if not rows:
            return {"attempted": 0, "delivered": 0, "remaining": 0}
        delivered = 0
        attempted = 0
        remaining: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            event_id = str(row.get("event_id") or "")
            if event_id and event_id in seen:
                continue
            if event_id:
                seen.add(event_id)
            if attempted >= max(1, limit):
                remaining.append(row)
                continue
            attempted += 1
            try:
                result = sender(row)
            except Exception:
                result = {"error": "control_plane_unavailable"}
            if isinstance(result, dict) and not result.get("error"):
                delivered += 1
            else:
                remaining.append(row)
        _write(path, remaining)
        return {
            "attempted": attempted,
            "delivered": delivered,
            "remaining": len(remaining),
        }
