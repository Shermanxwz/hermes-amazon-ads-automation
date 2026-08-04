from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any, Callable, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - Linux production uses fcntl.
    fcntl = None

UTC = timezone.utc
_LOCK = threading.RLock()


class OutboxCorruptError(RuntimeError):
    pass


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
            json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()[:32]
    body.setdefault("recorded_at", datetime.now(UTC).isoformat(timespec="seconds"))
    return body


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with _LOCK:
        handle = lock_path.open("a+b")
        try:
            os.chmod(lock_path, 0o600)
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict) or not str(value.get("event_id") or ""):
                raise OutboxCorruptError(f"invalid outbox event at line {number}")
            rows.append(value)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OutboxCorruptError(f"unable to parse durable outbox: {exc}") from exc
    return rows


def _quarantine(path: Path) -> Path | None:
    if not path.exists():
        return None
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = path.with_name(path.name + f".corrupt.{timestamp}")
    os.replace(path, target)
    os.chmod(target, 0o600)
    return target


def _load(path: Path) -> tuple[list[dict[str, Any]], Path | None]:
    try:
        return _read(path), None
    except OutboxCorruptError:
        return [], _quarantine(path)


def _write(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n" for row in rows)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def enqueue(payload: dict[str, Any]) -> dict[str, Any]:
    event = _event(payload)
    path = _path()
    with _file_lock(path):
        rows, quarantined = _load(path)
        existing = next((row for row in rows if row.get("event_id") == event["event_id"]), None)
        if existing and existing != event:
            raise ValueError("outbox event_id collision with different payload")
        if not existing:
            rows.append(event)
            _write(path, rows)
    if quarantined:
        event = dict(event)
        event["quarantined_corrupt_outbox"] = str(quarantined)
    return event


def pending_count() -> int:
    path = _path()
    with _file_lock(path):
        rows, _quarantined = _load(path)
        return len(rows)


def status() -> dict[str, Any]:
    path = _path()
    with _file_lock(path):
        rows, quarantined = _load(path)
    corrupt_files = sorted(str(item) for item in path.parent.glob(path.name + ".corrupt.*")) if path.parent.exists() else []
    if quarantined:
        corrupt_files.append(str(quarantined))
    return {"path": str(path), "pending": len(rows), "corrupt_files": sorted(set(corrupt_files))}


def deliver(payload: dict[str, Any], sender: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
    event = _event(payload)
    try:
        result = sender(event)
    except Exception as exc:
        result = {"error": "control_plane_unavailable", "detail": str(exc)}
    if not isinstance(result, dict) or result.get("error"):
        queued = enqueue(event)
        return {
            "queued": True,
            "event_id": event["event_id"],
            "error": (result or {}).get("error") if isinstance(result, dict) else "invalid_response",
            "quarantined_corrupt_outbox": queued.get("quarantined_corrupt_outbox"),
        }
    return {"queued": False, "event_id": event["event_id"], "response": result}


def flush(sender: Callable[[dict[str, Any]], dict[str, Any]], *, limit: int = 100) -> dict[str, Any]:
    path = _path()
    with _file_lock(path):
        rows, quarantined = _load(path)
        if not rows:
            return {"attempted": 0, "delivered": 0, "remaining": 0, "quarantined_corrupt_outbox": str(quarantined) if quarantined else None}
        delivered = 0
        attempted = 0
        remaining: list[dict[str, Any]] = []
        seen: dict[str, dict[str, Any]] = {}
        for row in rows:
            event_id = str(row.get("event_id") or "")
            previous = seen.get(event_id)
            if previous:
                if previous != row:
                    raise ValueError("durable outbox contains conflicting duplicate event IDs")
                continue
            seen[event_id] = row
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
            "quarantined_corrupt_outbox": str(quarantined) if quarantined else None,
        }
