#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--baseline", required=True)
    args = parser.parse_args()
    try:
        current = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"official-fingerprint: invalid input: {exc}", file=sys.stderr)
        return 2

    errors: list[str] = []
    expected_sha = str(baseline.get("semantic_sha256") or "")
    actual_sha = str(current.get("semantic_sha256") or "")
    if not expected_sha or actual_sha != expected_sha:
        errors.append(f"semantic fingerprint changed expected={expected_sha or '[missing]'} actual={actual_sha or '[missing]'}")
    expected_count = baseline.get("request_count")
    actual_count = current.get("request_count")
    if not isinstance(expected_count, int) or actual_count != expected_count:
        errors.append(f"request count changed expected={expected_count!r} actual={actual_count!r}")
    missing_core = sorted(name for name, present in (current.get("capabilities") or {}).items() if not present)
    missing_extended = sorted(name for name, present in (current.get("extended_capabilities") or {}).items() if not present)
    if missing_core:
        errors.append("missing core capabilities: " + ", ".join(missing_core))
    if missing_extended:
        errors.append("missing extended capabilities: " + ", ".join(missing_extended))
    if errors:
        print("official-fingerprint: " + "; ".join(errors), file=sys.stderr)
        return 1
    print(json.dumps({
        "ok": True,
        "request_count": actual_count,
        "semantic_sha256": actual_sha,
        "core_capabilities": len(current.get("capabilities") or {}),
        "extended_capabilities": len(current.get("extended_capabilities") or {}),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
