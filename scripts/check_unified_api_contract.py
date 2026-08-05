#!/usr/bin/env python3
"""Validate Amazon Ads Unified API without making Beta resources a sealed dependency."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
from urllib.request import Request, urlopen

DEFAULT_URL = (
    "https://raw.githubusercontent.com/amzn/ads-advanced-tools-docs/main/"
    "postman/Amazon_Ads_Unified_API.postman_collection.json"
)
REQUIRED_GA_RESOURCES = {
    "Campaigns", "AdGroups", "Ads", "Targets", "AdAssociations",
    "CampaignForecasts", "Recommendations", "RecommendationTypes",
}
EXPECTED_BETA_RESOURCES = {"Reports", "Events", "Rules", "RuleLinks", "Labels"}


def load(source: str) -> bytes:
    if source.startswith(("https://", "http://")):
        request = Request(source, headers={"User-Agent": "hermes-unified-contract/4"})
        with urlopen(request, timeout=60) as response:
            return response.read()
    return Path(source).read_bytes()


def walk(items, parents=()):
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "unnamed")
        children = item.get("item")
        if isinstance(children, list):
            yield from walk(children, (*parents, name))
        request = item.get("request")
        if not isinstance(request, dict):
            continue
        url = request.get("url")
        if isinstance(url, dict):
            raw_url = str(url.get("raw") or "/" + "/".join(map(str, url.get("path", []))))
        else:
            raw_url = str(url or "")
        body = request.get("body") if isinstance(request.get("body"), dict) else {}
        raw_body = body.get("raw") if isinstance(body.get("raw"), str) else ""
        try:
            parsed_body = json.loads(raw_body) if raw_body else None
        except json.JSONDecodeError:
            parsed_body = None
        yield {"name": name, "parents": list(parents), "method": str(request.get("method") or "UNKNOWN").upper(),
               "url": raw_url, "body_keys": sorted(parsed_body) if isinstance(parsed_body, dict) else []}


def summarize(raw: bytes, source: str) -> dict:
    document = json.loads(raw)
    operations = list(walk(document.get("item", [])))
    prod = [row for row in operations if any("Unified API — Prod" in part or "Unified API - Prod" in part for part in row["parents"])]
    beta = [row for row in operations if any("Unified API — Beta" in part or "Unified API - Beta" in part for part in row["parents"])]
    prod_resources = {row["parents"][-1] for row in prod if row["parents"]}
    beta_resources = {row["parents"][-1] for row in beta if row["parents"]}
    canonical = [{"method": row["method"], "url": row["url"].split("?", 1)[0], "body_keys": row["body_keys"]} for row in operations]
    missing_ga = sorted(REQUIRED_GA_RESOURCES - prod_resources)
    errors = []
    if missing_ga:
        errors.append("missing GA resources: " + ", ".join(missing_ga))
    if not all("/adsApi/v1" in row["url"] or "{{token_url}}" in row["url"] or "{{auth_grant_url}}" in row["url"] for row in operations):
        errors.append("collection contains a non-Unified API operation")
    return {
        "manifest_version": 1, "source": source, "sha256": hashlib.sha256(raw).hexdigest(),
        "semantic_sha256": hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "request_count": len(operations), "prod_request_count": len(prod), "beta_request_count": len(beta),
        "methods": dict(Counter(row["method"] for row in operations)), "ga_resources": sorted(prod_resources),
        "beta_resources": sorted(beta_resources), "required_ga_resources": sorted(REQUIRED_GA_RESOURCES),
        "missing_ga_resources": missing_ga, "beta_observe_only": sorted(EXPECTED_BETA_RESOURCES & beta_resources),
        "policy": {"ga": "adapter-compatible; may be used after live schema attestation",
                   "beta": "observe-only; never a sole sealed execution or reporting dependency"},
        "errors": errors, "ok": not errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--source", default=DEFAULT_URL)
    parser.add_argument("--output"); parser.add_argument("--check", action="store_true"); args = parser.parse_args()
    try:
        manifest = summarize(load(args.source), args.source)
    except Exception as exc:
        print(f"unified-contract: {exc}", file=sys.stderr); return 2
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: manifest[key] for key in ("ok", "request_count", "prod_request_count", "beta_request_count", "semantic_sha256", "missing_ga_resources")}, ensure_ascii=False))
    if args.check and not manifest["ok"]:
        print("unified-contract: " + "; ".join(manifest["errors"]), file=sys.stderr); return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
