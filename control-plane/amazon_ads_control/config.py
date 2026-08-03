from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    db_path: Path
    public_origin: str
    control_password_hash: str
    agent_token: str
    session_ttl_seconds: int
    max_sessions: int
    retention_days: int
    allow_remote_bind: bool

    @classmethod
    def from_env(cls) -> "Settings":
        host = os.getenv("ADS_CONTROL_HOST", "127.0.0.1").strip()
        allow_remote = _bool("ADS_CONTROL_ALLOW_REMOTE_BIND", False)
        if host not in {"127.0.0.1", "::1", "localhost"} and not allow_remote:
            raise ValueError(
                "Refusing non-loopback bind. Publish through Nginx/Caddy or set "
                "ADS_CONTROL_ALLOW_REMOTE_BIND=true explicitly."
            )
        home = Path(os.getenv("HERMES_HOME", Path.home() / ".hermes"))
        return cls(
            host=host,
            port=_int("ADS_CONTROL_PORT", 8790, 1, 65535),
            db_path=Path(os.getenv("ADS_CONTROL_DB", home / "amazon-ads-control" / "state.db")),
            public_origin=os.getenv("ADS_CONTROL_PUBLIC_ORIGIN", "").rstrip("/"),
            control_password_hash=os.getenv("ADS_CONTROL_PASSWORD_HASH", ""),
            agent_token=os.getenv("ADS_CONTROL_AGENT_TOKEN", ""),
            session_ttl_seconds=_int("ADS_CONTROL_SESSION_TTL", 43200, 300, 604800),
            max_sessions=_int("ADS_CONTROL_MAX_SESSIONS", 8, 1, 64),
            retention_days=_int("ADS_CONTROL_RETENTION_DAYS", 180, 7, 3650),
            allow_remote_bind=allow_remote,
        )

    def validate_runtime(self) -> None:
        if len(self.agent_token) < 32:
            raise ValueError("ADS_CONTROL_AGENT_TOKEN must be at least 32 characters")
        if not self.control_password_hash.startswith("pbkdf2_sha256$"):
            raise ValueError(
                "ADS_CONTROL_PASSWORD_HASH is missing or invalid; generate it with "
                "python scripts/control_cli.py hash-password"
            )
