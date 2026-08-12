from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import hmac
import os
import secrets
import time
import threading
from typing import MutableMapping


def hash_password(password: str, *, iterations: int = 310_000, salt: bytes | None = None) -> str:
    if len(password) < 14:
        raise ValueError("password must contain at least 14 characters")
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations,
        base64.urlsafe_b64encode(salt).decode().rstrip("="),
        base64.urlsafe_b64encode(digest).decode().rstrip("="),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_s, salt_s, digest_s = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_s + "=" * (-len(salt_s) % 4))
        expected = base64.urlsafe_b64decode(digest_s + "=" * (-len(digest_s) % 4))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iterations_s))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def constant_token_match(provided: str | None, expected: str) -> bool:
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided.encode(), expected.encode())


@dataclass
class BrowserSession:
    csrf: str
    expires_at: float


class SessionStore:
    def __init__(self, ttl_seconds: int = 43200, max_sessions: int = 8):
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self._sessions: MutableMapping[str, BrowserSession] = {}
        self._lock = threading.RLock()

    def create(self) -> tuple[str, str]:
        with self._lock:
            self.cleanup()
            while len(self._sessions) >= self.max_sessions:
                oldest = min(self._sessions, key=lambda key: self._sessions[key].expires_at)
                del self._sessions[oldest]
            sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
            self._sessions[sid] = BrowserSession(csrf=csrf, expires_at=time.time() + self.ttl_seconds)
            return sid, csrf

    def validate(self, sid: str | None) -> BrowserSession | None:
        if not sid:
            return None
        with self._lock:
            session = self._sessions.get(sid)
            if not session or session.expires_at <= time.time():
                self._sessions.pop(sid, None)
                return None
            return session

    def revoke(self, sid: str | None) -> None:
        if sid:
            with self._lock:
                self._sessions.pop(sid, None)

    def cleanup(self) -> None:
        with self._lock:
            now = time.time()
            for sid in [key for key, value in self._sessions.items() if value.expires_at <= now]:
                del self._sessions[sid]


@dataclass
class LoginAttempt:
    failures: list[float]
    blocked_until: float = 0.0


class LoginRateLimiter:
    """Small in-memory brute-force guard for the single-operator dashboard.

    Nginx normally connects over loopback, so the key intentionally protects
    the whole dashboard rather than trusting spoofable forwarding headers.
    """

    def __init__(self, max_failures: int = 5, window_seconds: int = 300, block_seconds: int = 900):
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.block_seconds = block_seconds
        self._attempts: MutableMapping[str, LoginAttempt] = {}
        self._lock = threading.RLock()

    def allowed(self, key: str) -> tuple[bool, int]:
        now = time.time()
        with self._lock:
            state = self._attempts.get(key)
            if not state:
                return True, 0
            state.failures[:] = [value for value in state.failures if value >= now - self.window_seconds]
            if state.blocked_until > now:
                return False, max(1, int(state.blocked_until - now))
            if not state.failures:
                self._attempts.pop(key, None)
            return True, 0

    def failure(self, key: str) -> tuple[bool, int]:
        now = time.time()
        with self._lock:
            state = self._attempts.setdefault(key, LoginAttempt(failures=[]))
            state.failures[:] = [value for value in state.failures if value >= now - self.window_seconds]
            state.failures.append(now)
            if len(state.failures) >= self.max_failures:
                state.blocked_until = now + self.block_seconds
                return False, self.block_seconds
            return True, 0

    def success(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)
