#!/usr/bin/env python3
"""Conservative repository secret scan for committed source files."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {".git", ".venv", "__pycache__", "node_modules", "build", "dist"}
PATTERNS = [
    re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAtza\|[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)authorization\s*:\s*bearer\s+[A-Za-z0-9._~+/-]{20,}"),
    re.compile(r"(?i)(?:client_secret|refresh_token|access_token|api_key)\s*[:=]\s*['\"]?(?!\$\{|replace|REPLACE|example|dummy|test)[A-Za-z0-9._~+/-]{20,}"),
]
TEXT_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".json", ".js", ".css", ".html", ".sh", ".toml", ".service", ".conf", ".example", ".env", ".txt", ""}


def scan(root: Path = ROOT) -> list[tuple[Path, int]]:
    hits: list[tuple[Path, int]] = []
    for path in root.rglob("*"):
        if not path.is_file() or any(part in SKIP_PARTS for part in path.parts):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {"Dockerfile", ".gitignore"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if any(pattern.search(line) for pattern in PATTERNS):
                hits.append((path.relative_to(root), lineno))
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan source files for common committed credential patterns")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    hits = scan(args.root)
    if hits:
        for path, lineno in hits:
            print(f"possible secret: {path}:{lineno}", file=sys.stderr)
        return 1
    print("secret-scan: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
