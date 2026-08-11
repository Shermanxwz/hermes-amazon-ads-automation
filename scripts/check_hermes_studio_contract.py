#!/usr/bin/env python3
"""Fail closed when Hermes Studio changes the integration contract we depend on.

This checker intentionally validates source semantics instead of pinning exact file
hashes. It tracks the current Hermes Studio main branch by default so upstream
Profile/plugin/chat-run drift becomes visible in CI before production deployment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from urllib.request import Request, urlopen

REPOSITORY = "EKKOLearnAI/hermes-studio"
DEFAULT_REF = "main"
FILES = {
    "profile": "packages/server/src/services/hermes/hermes-profile.ts",
    "plugins_service": "packages/server/src/services/hermes/plugins.ts",
    "plugins_route": "packages/server/src/routes/hermes/plugins.ts",
    "chat_route": "packages/server/src/routes/hermes/chat-run.ts",
    "chat_controller": "packages/server/src/controllers/chat-run.ts",
}

CHECKS: dict[str, list[tuple[str, str]]] = {
    "profile": [
        ("getProfileDir", r"function\s+getProfileDir\s*\("),
        ("named profile directory", r"join\(\s*hermesBase\s*,\s*['\"]profiles['\"]\s*,\s*name\s*\)"),
        ("default profile uses base", r"name\s*===\s*['\"]default['\"]\)\s*return\s+hermesBase"),
    ],
    "plugins_service": [
        ("PluginManager", r"PluginManager\s*\("),
        ("selected profile home", r"getProfileDir\s*\(\s*profile\s*\)"),
        ("profile HERMES_HOME propagation", r"HERMES_HOME\s*:\s*hermesHome"),
        ("profile-local user plugins", r"get_hermes_home\(\)\s*/\s*['\"]plugins['\"]"),
        ("plugin enable mutation", r"function\s+setHermesPluginEnabled\s*\("),
    ],
    "plugins_route": [
        ("plugin list route", r"['\"]/api/hermes/plugins['\"]"),
        ("plugin enable route", r"['\"]/api/hermes/plugins/:key/enable['\"]"),
    ],
    "chat_route": [
        ("HTTP chat-run route", r"chatRunRoutes\.post\(\s*['\"]/api/chat-run/runs['\"]"),
    ],
    "chat_controller": [
        ("profile selection", r"profileFrom\s*\("),
        ("profile socket query", r"query\s*:\s*\{\s*profile\s*\}"),
        ("bearer-to-socket auth", r"auth\s*:\s*token\s*\?\s*\{\s*token\s*\}"),
        ("run dispatch", r"socket\.emit\(\s*['\"]run['\"]\s*,\s*payload\s*\)"),
        ("tool events exposed", r"['\"]tool\.completed['\"]"),
    ],
}


def fetch(ref: str, path: str) -> bytes:
    url = f"https://raw.githubusercontent.com/{REPOSITORY}/{ref}/{path}"
    request = Request(url, headers={"User-Agent": "hermes-amazon-ads-studio-contract/4.2.1"})
    with urlopen(request, timeout=30) as response:
        return response.read()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ref", default=DEFAULT_REF)
    parser.add_argument("--output")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    manifest: dict[str, object] = {
        "repository": REPOSITORY,
        "ref": args.ref,
        "files": {},
        "errors": [],
    }
    semantic_rows: list[tuple[str, str]] = []

    try:
        for key, path in FILES.items():
            raw = fetch(args.ref, path)
            text = raw.decode("utf-8")
            digest = hashlib.sha256(raw).hexdigest()
            file_errors: list[str] = []
            for label, pattern in CHECKS[key]:
                if not re.search(pattern, text, flags=re.MULTILINE | re.DOTALL):
                    file_errors.append(label)
                    semantic_rows.append((key, f"missing:{label}"))
                else:
                    semantic_rows.append((key, f"present:{label}"))
            manifest["files"][key] = {
                "path": path,
                "sha256": digest,
                "missing_contracts": file_errors,
            }
            if file_errors:
                manifest["errors"].append(
                    f"{path}: missing " + ", ".join(file_errors)
                )
    except Exception as exc:
        manifest["errors"].append(f"fetch/parse failure: {exc}")

    manifest["semantic_sha256"] = hashlib.sha256(
        json.dumps(semantic_rows, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    manifest["ok"] = not manifest["errors"]

    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    print(json.dumps({
        "ok": manifest["ok"],
        "ref": args.ref,
        "semantic_sha256": manifest["semantic_sha256"],
        "errors": manifest["errors"],
    }, ensure_ascii=False))

    if args.check and not manifest["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
