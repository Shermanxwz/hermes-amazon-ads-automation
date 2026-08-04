#!/usr/bin/env python3
"""Require an explicit project policy for every audited Amazon Ads capability."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

MCP_REQUIRED = {
    "mcp_end_to_end_campaign",
    "mcp_locale_expansion",
    "billing_finance",
    "account_administration",
    "irreversible_delete",
}


def load_object(path: str) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        manifest = load_object(args.manifest)
        policy = load_object(args.policy)
    except Exception as exc:
        print(f"capability-policy: {exc}", file=sys.stderr)
        return 2

    official = set()
    for key in ("capabilities", "extended_capabilities"):
        values = manifest.get(key)
        if not isinstance(values, dict):
            print(f"capability-policy: manifest is missing {key}", file=sys.stderr)
            return 2
        official.update(str(name) for name, present in values.items() if present is True)

    allowed_modes = set(policy.get("allowed_modes") or [])
    declared = policy.get("capabilities")
    if not isinstance(declared, dict):
        print("capability-policy: policy.capabilities must be an object", file=sys.stderr)
        return 2

    required = official | MCP_REQUIRED
    missing = sorted(required - set(declared))
    invalid: list[str] = []
    for name, item in declared.items():
        if not isinstance(item, dict):
            invalid.append(f"{name}: policy must be an object")
            continue
        mode = item.get("mode")
        if mode not in allowed_modes:
            invalid.append(f"{name}: invalid mode {mode!r}")
        notes = item.get("notes")
        if not isinstance(notes, str) or not notes.strip():
            invalid.append(f"{name}: non-empty notes are required")
        if mode == "production_acceptance_only" and not item.get("owner_evidence"):
            invalid.append(f"{name}: production acceptance requires owner_evidence")
        if mode == "permanently_blocked" and item.get("owner_evidence"):
            invalid.append(f"{name}: permanently blocked policy must not request owner evidence")

    result = {
        "ok": not missing and not invalid,
        "official_present": sorted(official),
        "required": sorted(required),
        "declared_count": len(declared),
        "missing": missing,
        "invalid": invalid,
        "modes": {
            mode: sorted(name for name, item in declared.items() if isinstance(item, dict) and item.get("mode") == mode)
            for mode in sorted(allowed_modes)
        },
    }
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
