#!/usr/bin/env python3
"""Summarize and validate Amazon's official Ads Advanced Tools Postman collection.

No credentials are used. The script only downloads/reads the public collection and emits a
small capability manifest suitable for CI drift detection and documentation.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any
from urllib.request import Request, urlopen

OFFICIAL_URL = "https://raw.githubusercontent.com/amzn/ads-advanced-tools-docs/main/postman/Amazon_Ads_API.postman_collection.json"
REQUIRED_CAPABILITIES = {
    "authentication": ("authentication", "oauth"),
    "profiles": ("profiles",),
    "sponsored_products": ("sponsored products", "sp v3"),
    "sponsored_brands": ("sponsored brands", "sb v4"),
    "sponsored_display": ("sponsored display",),
    "reporting": ("reporting", "reports"),
    "marketing_stream": ("marketing stream",),
    "recommendations": ("recommendation", "recommendations"),
    "budget": ("budget",),
    "test_accounts": ("test account",),
    "exports": ("exports", "export"),
}


def load_source(source: str) -> bytes:
    if source.startswith(("https://", "http://")):
        request = Request(source, headers={"User-Agent": "hermes-amazon-ads-contract-sync/2"})
        with urlopen(request, timeout=60) as response:
            return response.read()
    return Path(source).read_bytes()


def request_url(request: dict[str, Any]) -> str:
    value = request.get("url")
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("raw") or "/".join(str(x) for x in value.get("path", [])))
    return ""


def walk_items(items: Any, parents: tuple[str, ...] = ()):
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "unnamed")
        children = item.get("item")
        if isinstance(children, list):
            yield from walk_items(children, (*parents, name))
        request = item.get("request")
        if isinstance(request, dict):
            yield {
                "name": name,
                "path": " / ".join((*parents, name)),
                "method": str(request.get("method") or "UNKNOWN").upper(),
                "url": request_url(request),
            }


def summarize(raw: bytes, source: str) -> dict[str, Any]:
    document = json.loads(raw)
    rows = list(walk_items(document.get("item", [])))
    searchable = "\n".join(
        f"{row['path']} {row['method']} {row['url']}".lower() for row in rows
    )
    capabilities = {}
    for name, needles in REQUIRED_CAPABILITIES.items():
        capabilities[name] = any(needle in searchable for needle in needles)
    methods = Counter(row["method"] for row in rows)
    top_folders = Counter(row["path"].split(" / ", 1)[0] for row in rows)
    return {
        "source": source,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "collection_name": document.get("info", {}).get("name"),
        "collection_schema": document.get("info", {}).get("schema"),
        "request_count": len(rows),
        "methods": dict(sorted(methods.items())),
        "top_folders": dict(top_folders.most_common()),
        "capabilities": capabilities,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "examples": rows[:20],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=OFFICIAL_URL)
    parser.add_argument("--output")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        manifest = summarize(load_source(args.source), args.source)
    except Exception as exc:
        print(f"official-contract: unable to load {args.source}: {exc}", file=sys.stderr)
        return 2
    missing = [name for name, present in manifest["capabilities"].items() if not present]
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({
        "request_count": manifest["request_count"],
        "sha256": manifest["sha256"],
        "missing": missing,
    }, ensure_ascii=False))
    if args.check and missing:
        print("official-contract: missing required capabilities: " + ", ".join(missing), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
