#!/usr/bin/env python3
"""Fail CI on committed credentials, private account identifiers and common PII."""
from __future__ import annotations

import argparse
import ipaddress
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
ALLOW_MARKER = "privacy-scan: allow-test"
SKIP_PARTS = {".git", ".venv", "__pycache__", "node_modules", "build", "dist"}
SKIP_PREFIXES = (".ci-",)
TEXT_SUFFIXES = {
    ".py", ".md", ".yaml", ".yml", ".json", ".js", ".css", ".html", ".sh", ".toml",
    ".service", ".timer", ".conf", ".example", ".env", ".txt", "",
}

CREDENTIAL_PATTERNS = [
    re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAtza\|[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)authorization\s*:\s*bearer\s+[A-Za-z0-9._~+/-]{20,}"),
    re.compile(
        r"(?i)(?:client_secret|refresh_token|access_token|api_key)\s*[:=]\s*['\"]?"
        r"(?!\$\{|replace|REPLACE|example|dummy|test)[A-Za-z0-9._~+/-]{20,}"
    ),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----"),
]
PROFILE_ASSIGNMENT = re.compile(
    r"(?i)(?:\bprofile[_ -]?id\b|\bPROFILE\b)\s*[:=]\s*['\"]?(\d{14,20})\b"
)
PROFILE_DISPLAY = re.compile(r"(?i)\bprofile\b[^\n\r]{0,40}\b(\d{14,20})\b")
ADS_ACCOUNT = re.compile(r"\bamzn1\.ads-account\.[A-Za-z0-9._-]{8,}\b", re.I)
EMAIL = re.compile(r"(?<![\w.+-])([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})(?![\w.-])", re.I)
IPV4 = re.compile(r"(?<!\d)(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?!\d)")


def _is_excluded(path: Path) -> bool:
    return any(part in SKIP_PARTS or part.startswith(SKIP_PREFIXES) for part in path.parts)


def _tracked_files(root: Path) -> list[Path]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return [root / item.decode("utf-8") for item in result.stdout.split(b"\0") if item]
    except (FileNotFoundError, subprocess.CalledProcessError, UnicodeDecodeError):
        return [path for path in root.rglob("*") if path.is_file()]


def _dummy_numeric(value: str) -> bool:
    if len(set(value)) <= 2:
        return True
    normalized = value.replace("0", "")
    return normalized in {"123456789123456", "987654321987654"}


def _safe_email(value: str) -> bool:
    lower = value.lower()
    return lower.endswith(("@example.com", "@example.org", "@example.net", "@users.noreply.github.com")) or lower.startswith(("test@", "user@"))


def _public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(address.version == 4 and address.is_global)


def scan(root: Path = ROOT) -> list[tuple[Path, int, str]]:
    hits: list[tuple[Path, int, str]] = []
    for path in _tracked_files(root):
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if not path.is_file() or _is_excluded(relative):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {"Dockerfile", ".gitignore"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if ALLOW_MARKER in line:
                continue
            if any(pattern.search(line) for pattern in CREDENTIAL_PATTERNS):
                hits.append((relative, lineno, "credential"))
                continue
            profile = PROFILE_ASSIGNMENT.search(line) or PROFILE_DISPLAY.search(line)
            if profile and not _dummy_numeric(profile.group(1)):
                hits.append((relative, lineno, "amazon-profile-id"))
                continue
            account = ADS_ACCOUNT.search(line)
            if account and not any(token in account.group(0).lower() for token in ("example", "dummy", "test")):
                hits.append((relative, lineno, "amazon-advertiser-account-id"))
                continue
            emails = [match.group(1) for match in EMAIL.finditer(line)]
            if any(not _safe_email(value) for value in emails):
                hits.append((relative, lineno, "email-address"))
                continue
            ips = [match.group(0) for match in IPV4.finditer(line)]
            if any(_public_ip(value) for value in ips):
                hits.append((relative, lineno, "public-ip-address"))
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan committed source for credentials, account IDs and PII")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    hits = scan(args.root)
    if hits:
        for path, lineno, kind in hits:
            print(f"privacy leak ({kind}): {path}:{lineno}", file=sys.stderr)
        return 1
    print("privacy-and-secret-scan: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
