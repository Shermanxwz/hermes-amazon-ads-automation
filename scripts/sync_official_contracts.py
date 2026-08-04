#!/usr/bin/env python3
"""Compile Amazon's official Postman collection into a semantic contract manifest.

The compiler never uses credentials and never stores literal authorization values.
It normalizes method/path/body/header metadata, capability coverage, asynchronous
report hints, and a stable endpoint fingerprint. An optional baseline comparison
fails closed on removed or materially changed contracts.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any
from urllib.request import Request, urlopen

OFFICIAL_URL = (
    "https://raw.githubusercontent.com/amzn/ads-advanced-tools-docs/main/"
    "postman/Amazon_Ads_API.postman_collection.json"
)

CORE_REQUIRED_CAPABILITIES = {
    "authentication": (
        "authentication", "oauth", "bearer", "authorization",
        "amazon-advertising-api-clientid", "access token", "accesstoken",
    ),
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

EXTENDED_CAPABILITIES = {
    "manager_accounts": ("manager account", "manager accounts"),
    "dsp_reporting": ("dsp reporting", "dsp report"),
    "snapshots": ("snapshot", "snapshots"),
    "amazon_marketing_cloud": ("amazon marketing cloud", "amc"),
    "product_metadata": ("product metadata",),
    "budget_rules": ("budget rule", "budget rules", "budget usage"),
    "creative_assets": ("creative asset", "creative assets"),
    "stores": ("stores", "store management"),
    "locations": ("locations", "location management"),
    "sponsored_tv": ("sponsored tv",),
    "partner_opportunities": ("partner opportunity", "partner opportunities"),
}

REQUIRED_CAPABILITIES = CORE_REQUIRED_CAPABILITIES  # Backward-compatible import.
VARIABLE_RE = re.compile(r"\{\{[^{}]+\}\}")
VERSION_RE = re.compile(r"/(v\d+)(?:/|$)", re.I)
SECRET_HEADER_NAMES = {"authorization", "proxy-authorization"}


def load_source(source: str) -> bytes:
    if source.startswith(("https://", "http://")):
        request = Request(
            source,
            headers={"User-Agent": "hermes-amazon-ads-contract-sync/3"},
        )
        with urlopen(request, timeout=60) as response:
            return response.read()
    return Path(source).read_bytes()


def request_url(request: dict[str, Any]) -> str:
    value = request.get("url")
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(
            value.get("raw")
            or "/" + "/".join(str(x) for x in value.get("path", []))
        )
    return ""


def _sanitize_url(raw: str) -> str:
    raw = raw.split("?", 1)[0].strip()
    raw = re.sub(r"^https?://[^/]+", "", raw, flags=re.I)
    raw = VARIABLE_RE.sub("{var}", raw)
    raw = re.sub(r"/+", "/", raw)
    return raw or "/"


def _header_contract(request: dict[str, Any]) -> list[dict[str, Any]]:
    headers = request.get("header")
    out: list[dict[str, Any]] = []
    if not isinstance(headers, list):
        return out
    for header in headers:
        if not isinstance(header, dict):
            continue
        name = str(header.get("key") or "").strip()
        if not name:
            continue
        value = str(header.get("value") or "")
        variable = value if VARIABLE_RE.fullmatch(value.strip()) else None
        out.append({
            "name": name.lower(),
            "required": not bool(header.get("disabled")),
            "variable": variable,
            "redacted": name.lower() in SECRET_HEADER_NAMES and variable is None,
        })
    return sorted(out, key=lambda item: item["name"])


def _json_paths(value: Any, prefix: str = "$") -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key in sorted(value):
            child = f"{prefix}.{key}"
            paths.append(child)
            paths.extend(_json_paths(value[key], child))
    elif isinstance(value, list):
        paths.append(f"{prefix}[]")
        for item in value[:3]:
            paths.extend(_json_paths(item, f"{prefix}[]"))
    return paths


def _body_contract(request: dict[str, Any]) -> dict[str, Any]:
    body = request.get("body")
    if not isinstance(body, dict):
        return {"mode": None, "json_paths": [], "media_type": None}
    mode = str(body.get("mode") or "") or None
    raw = body.get("raw")
    paths: list[str] = []
    if mode == "raw" and isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if parsed is not None:
            paths = sorted(set(_json_paths(parsed)))
    options = body.get("options") if isinstance(body.get("options"), dict) else {}
    raw_options = options.get("raw") if isinstance(options.get("raw"), dict) else {}
    media_type = raw_options.get("language")
    return {
        "mode": mode,
        "json_paths": paths,
        "media_type": str(media_type) if media_type else None,
    }


def request_contract_text(request: dict[str, Any]) -> str:
    pieces: list[str] = []
    auth = request.get("auth")
    if isinstance(auth, dict):
        pieces.append(str(auth.get("type") or ""))
        pieces.extend(str(key) for key in auth)
    for header in _header_contract(request):
        pieces.append(header["name"])
        if header["variable"]:
            pieces.append(header["variable"])
    description = request.get("description")
    if isinstance(description, str):
        pieces.append(description[:4000])
    return " ".join(pieces)


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
        if not isinstance(request, dict):
            continue
        raw_url = request_url(request)
        path = _sanitize_url(raw_url)
        method = str(request.get("method") or "UNKNOWN").upper()
        description = request.get("description")
        description_text = description if isinstance(description, str) else ""
        body = _body_contract(request)
        version = None
        match = VERSION_RE.search(path)
        if match:
            version = match.group(1).lower()
        async_hints = sorted({
            hint for hint in ("report", "status", "download", "poll", "snapshot", "export")
            if hint in f"{name} {path} {description_text}".lower()
        })
        contract = {
            "name": name,
            "folder_path": " / ".join(parents),
            "display_path": " / ".join((*parents, name)),
            "method": method,
            "path": path,
            "version": version,
            "headers": _header_contract(request),
            "body": body,
            "async_hints": async_hints,
            "description_hash": hashlib.sha256(
                description_text.strip().encode("utf-8")
            ).hexdigest()[:16],
        }
        canonical = json.dumps(
            {
                "method": contract["method"],
                "path": contract["path"],
                "headers": contract["headers"],
                "body": contract["body"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        contract["contract_id"] = hashlib.sha256(canonical.encode()).hexdigest()[:24]
        contract["search_text"] = request_contract_text(request)
        yield contract


def _capabilities(searchable: str, definitions: dict[str, tuple[str, ...]]) -> dict[str, bool]:
    return {
        name: any(needle in searchable for needle in needles)
        for name, needles in definitions.items()
    }


def _semantic_index(endpoints: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        f"{row['method']} {row['path']}": {
            "contract_id": row["contract_id"],
            "display_path": row["display_path"],
            "body": row["body"],
            "headers": row["headers"],
        }
        for row in endpoints
    }


def summarize(raw: bytes, source: str) -> dict[str, Any]:
    document = json.loads(raw)
    endpoints = list(walk_items(document.get("item", [])))
    collection_contract = json.dumps(
        {
            "auth": document.get("auth"),
            "variables": [
                str(item.get("key") or "")
                for item in document.get("variable", [])
                if isinstance(item, dict)
            ],
        },
        ensure_ascii=False,
        default=str,
    )
    searchable = "\n".join(
        [collection_contract.lower()]
        + [
            f"{row['display_path']} {row['method']} {row['path']} {row.pop('search_text', '')}".lower()
            for row in endpoints
        ]
    )
    methods = Counter(row["method"] for row in endpoints)
    top_folders = Counter(
        row["display_path"].split(" / ", 1)[0] for row in endpoints
    )
    versions = Counter(row["version"] or "unversioned" for row in endpoints)
    endpoint_fingerprint = hashlib.sha256(
        json.dumps(
            _semantic_index(endpoints),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "manifest_version": 2,
        "source": source,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "semantic_sha256": endpoint_fingerprint,
        "collection_name": document.get("info", {}).get("name"),
        "collection_schema": document.get("info", {}).get("schema"),
        "request_count": len(endpoints),
        "methods": dict(sorted(methods.items())),
        "versions": dict(sorted(versions.items())),
        "top_folders": dict(top_folders.most_common()),
        "capabilities": _capabilities(searchable, CORE_REQUIRED_CAPABILITIES),
        "extended_capabilities": _capabilities(searchable, EXTENDED_CAPABILITIES),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "endpoints": endpoints,
        "examples": [
            {
                "name": row["name"],
                "display_path": row["display_path"],
                "method": row["method"],
                "path": row["path"],
                "contract_id": row["contract_id"],
            }
            for row in endpoints[:20]
        ],
    }


def semantic_diff(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    old = _semantic_index(
        baseline.get("endpoints", []) if isinstance(baseline.get("endpoints"), list) else []
    )
    new = _semantic_index(
        current.get("endpoints", []) if isinstance(current.get("endpoints"), list) else []
    )
    removed = sorted(set(old) - set(new))
    added = sorted(set(new) - set(old))
    changed = sorted(
        key for key in set(old) & set(new)
        if old[key].get("contract_id") != new[key].get("contract_id")
    )
    return {
        "removed": removed,
        "added": added,
        "changed": changed,
        "breaking": bool(removed or changed),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=OFFICIAL_URL)
    parser.add_argument("--output")
    parser.add_argument("--baseline")
    parser.add_argument("--diff-output")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--strict-extended", action="store_true")
    args = parser.parse_args()
    try:
        manifest = summarize(load_source(args.source), args.source)
    except Exception as exc:
        print(f"official-contract: unable to load {args.source}: {exc}", file=sys.stderr)
        return 2

    missing = [
        name for name, present in manifest["capabilities"].items() if not present
    ]
    missing_extended = [
        name
        for name, present in manifest["extended_capabilities"].items()
        if not present
    ]
    diff = None
    if args.baseline:
        try:
            baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
            diff = semantic_diff(baseline, manifest)
        except Exception as exc:
            print(f"official-contract: invalid baseline: {exc}", file=sys.stderr)
            return 2

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.diff_output and diff is not None:
        Path(args.diff_output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.diff_output).write_text(
            json.dumps(diff, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    print(json.dumps({
        "request_count": manifest["request_count"],
        "sha256": manifest["sha256"],
        "semantic_sha256": manifest["semantic_sha256"],
        "missing": missing,
        "missing_extended": missing_extended,
        "breaking_diff": bool(diff and diff["breaking"]),
    }, ensure_ascii=False))

    failures: list[str] = []
    if missing:
        failures.append("missing required capabilities: " + ", ".join(missing))
    if args.strict_extended and missing_extended:
        failures.append("missing extended capabilities: " + ", ".join(missing_extended))
    if diff and diff["breaking"]:
        failures.append(
            f"semantic drift removed={len(diff['removed'])} changed={len(diff['changed'])}"
        )
    if args.check and failures:
        print("official-contract: " + "; ".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
