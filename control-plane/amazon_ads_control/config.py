from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
from urllib.parse import urlparse


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


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
        if not host:
            raise ValueError("ADS_CONTROL_HOST cannot be empty")
        home = Path(os.getenv("HERMES_HOME", Path.home() / ".hermes"))
        public_origin = os.getenv("ADS_CONTROL_PUBLIC_ORIGIN", "").rstrip("/")
        if public_origin:
            parsed = urlparse(public_origin)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
                raise ValueError("ADS_CONTROL_PUBLIC_ORIGIN must be an http(s) origin without a path")
        return cls(
            host=host,
            port=_int("ADS_CONTROL_PORT", 8790, 1, 65535),
            db_path=Path(os.getenv("ADS_CONTROL_DB", home / "amazon-ads-control" / "state.db")),
            public_origin=public_origin,
            control_password_hash=os.getenv("ADS_CONTROL_PASSWORD_HASH", ""),
            agent_token=os.getenv("ADS_CONTROL_AGENT_TOKEN", ""),
            session_ttl_seconds=_int("ADS_CONTROL_SESSION_TTL", 43200, 300, 604800),
            max_sessions=_int("ADS_CONTROL_MAX_SESSIONS", 8, 1, 64),
            retention_days=_int("ADS_CONTROL_RETENTION_DAYS", 180, 7, 3650),
            allow_remote_bind=allow_remote,
        )

    def validate_runtime(self) -> None:
        if len(self.agent_token) < 32 or self.agent_token.strip() != self.agent_token:
            raise ValueError("ADS_CONTROL_AGENT_TOKEN must be at least 32 non-whitespace-delimited characters")
        parts = self.control_password_hash.split("$")
        if len(parts) != 4 or parts[0] != "pbkdf2_sha256" or not parts[1].isdigit():
            raise ValueError(
                "ADS_CONTROL_PASSWORD_HASH is missing or invalid; generate it with "
                "python scripts/control_cli.py hash-password"
            )
