#!/usr/bin/env python3
"""Audit the live Amazon Ads MCP contract without trusting model prose.

The checker speaks MCP over Streamable HTTP, records the initialize contract,
retrieves every tools/list page, validates schemas, classifies tool authority,
and emits a deterministic manifest. It never prints or stores credentials.

A fixture mode is included so the full protocol and policy path can be tested in
an isolated sandbox without Amazon credentials. A credentialed live run remains
required before production release.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

DEFAULT_ENDPOINT = "https://advertising-ai.amazon.com/mcp"
PROTOCOL_VERSION = "2025-06-18"
_ALLOWED_HOST_RE = re.compile(r"^advertising-ai(?:-[a-z0-9-]+)?\.amazon\.com$", re.I)

_PERMANENT_TOKENS = {
    "billing", "invoice", "payment", "financial", "account_setting",
    "account_settings", "user", "users", "role", "roles", "permission",
    "permissions", "invitation", "delete_account", "delete_profile",
}
_DELETE_TOKENS = {"delete", "remove", "archive", "terminate", "purge"}
_COMPOSITE_TOKENS = {
    "workflow", "end_to_end", "end-to-end", "expand_locale", "expand_to_locale",
    "cross_region", "cross-region", "launch_campaign", "campaign_builder",
    "composite", "bulk", "multi_step", "multi-step",
}
_JOB_TOKENS = {"report", "export", "snapshot", "stream", "job", "poll", "download"}
_READ_TOKENS = {
    "get", "list", "query", "search", "read", "describe", "inspect", "preview",
    "recommend", "forecast", "status", "history", "metadata", "report",
}
_WRITE_TOKENS = {
    "create", "update", "set", "apply", "enable", "disable", "pause", "resume",
    "add", "associate", "negative", "launch", "expand", "copy", "submit",
}


class ContractError(RuntimeError):
    pass


class AuthRequired(ContractError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _contains_any(text: str, tokens: set[str]) -> bool:
    normalized = _slug(text)
    padded = f"_{normalized}_"
    return any(token in normalized or f"_{_slug(token)}_" in padded for token in tokens)


def validate_endpoint(endpoint: str, *, allow_localhost: bool = False) -> None:
    parsed = urlparse(endpoint)
    allowed_schemes = {"https"}
    if allow_localhost:
        allowed_schemes.add("http")
    if parsed.scheme not in allowed_schemes:
        raise ValueError("MCP endpoint must use HTTPS")
    host = (parsed.hostname or "").lower()
    if allow_localhost and host in {"127.0.0.1", "localhost", "::1"}:
        return
    if not _ALLOWED_HOST_RE.fullmatch(host):
        raise ValueError("MCP endpoint host is outside the Amazon Ads allowlist")
    if parsed.username or parsed.password:
        raise ValueError("MCP endpoint must not contain embedded credentials")
    if parsed.path.rstrip("/") != "/mcp":
        raise ValueError("MCP endpoint path must be /mcp")


def _json_or_sse(raw: bytes, content_type: str) -> dict[str, Any]:
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return {}
    if "text/event-stream" in content_type.lower() or text.startswith(("event:", "data:")):
        payloads: list[dict[str, Any]] = []
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            value = line[5:].strip()
            if not value or value == "[DONE]":
                continue
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                payloads.append(parsed)
        if not payloads:
            raise ContractError("MCP response contained no JSON SSE data")
        return payloads[-1]
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ContractError("MCP response must be a JSON object")
    return parsed


@dataclass
class MCPResponse:
    body: dict[str, Any]
    headers: dict[str, str]
    status: int


class MCPHTTPClient:
    def __init__(self, endpoint: str, token: str | None, timeout: float = 60.0):
        self.endpoint = endpoint
        self.token = token
        self.timeout = timeout
        self.session_id = ""
        self._next_id = 1
        self._ssl = ssl.create_default_context()

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "hermes-amazon-ads-mcp-audit/3.2",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def post(self, payload: dict[str, Any], *, notification: bool = False) -> MCPResponse:
        request = Request(
            self.endpoint,
            data=_canonical(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout, context=self._ssl) as response:
                raw = response.read()
                headers = {key.lower(): value for key, value in response.headers.items()}
                body = _json_or_sse(raw, headers.get("content-type", "")) if raw else {}
                result = MCPResponse(body=body, headers=headers, status=response.status)
        except HTTPError as exc:
            raw = exc.read()
            if exc.code in {401, 403}:
                raise AuthRequired(f"MCP authorization required (HTTP {exc.code})") from None
            detail = raw.decode("utf-8", errors="replace")[:500]
            raise ContractError(f"MCP HTTP {exc.code}: {detail}") from None
        except URLError as exc:
            raise ContractError(f"MCP connection failed: {exc.reason}") from None
        except (TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise ContractError(f"MCP transport failure: {exc}") from None
        session = result.headers.get("mcp-session-id")
        if session:
            self.session_id = session
        if not notification and result.body.get("error"):
            raise ContractError(f"MCP JSON-RPC error: {result.body['error']}")
        return result

    def call(self, method: str, params: dict[str, Any] | None = None) -> MCPResponse:
        request_id = self._next_id
        self._next_id += 1
        return self.post({
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        })

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self.post({"jsonrpc": "2.0", "method": method, "params": params or {}}, notification=True)

    def discover(self) -> dict[str, Any]:
        initialized = self.call("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "hermes-amazon-ads-mcp-audit", "version": "3.2.0"},
        })
        result = initialized.body.get("result")
        if not isinstance(result, dict):
            raise ContractError("initialize result is missing")
        self.notify("notifications/initialized")
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            params = {"cursor": cursor} if cursor else {}
            page = self.call("tools/list", params).body.get("result")
            if not isinstance(page, dict) or not isinstance(page.get("tools"), list):
                raise ContractError("tools/list result is malformed")
            tools.extend(item for item in page["tools"] if isinstance(item, dict))
            next_cursor = page.get("nextCursor") or page.get("next_cursor")
            if not next_cursor:
                break
            cursor = str(next_cursor)
            if cursor in seen_cursors:
                raise ContractError("tools/list cursor loop detected")
            seen_cursors.add(cursor)
            if len(seen_cursors) > 100:
                raise ContractError("tools/list exceeded 100 pages")
        return {
            "initialize": result,
            "session_id_present": bool(self.session_id),
            "tools": tools,
        }


def _tool_text(tool: dict[str, Any]) -> str:
    return " ".join((str(tool.get("name") or ""), str(tool.get("description") or "")))


def classify_tool(tool: dict[str, Any]) -> dict[str, Any]:
    text = _tool_text(tool)
    annotations = tool.get("annotations") if isinstance(tool.get("annotations"), dict) else {}
    read_only = annotations.get("readOnlyHint") is True
    destructive = annotations.get("destructiveHint") is True
    permanent = _contains_any(text, _PERMANENT_TOKENS)
    delete_like = destructive or _contains_any(text, _DELETE_TOKENS)
    composite = _contains_any(text, _COMPOSITE_TOKENS)
    job = _contains_any(text, _JOB_TOKENS)
    write = not read_only and (_contains_any(text, _WRITE_TOKENS) or delete_like or composite)
    read = read_only or (not write and _contains_any(text, _READ_TOKENS))
    if permanent or delete_like:
        authority = "permanently_blocked"
    elif composite:
        authority = "compile_or_approval_only"
    elif write:
        authority = "planned_executor_only"
    elif job:
        authority = "bounded_data_job"
    elif read:
        authority = "read_only"
    else:
        authority = "unknown_fail_closed"
    return {
        "read": read,
        "write": write,
        "job": job,
        "composite": composite,
        "delete_like": delete_like,
        "account_or_billing": permanent,
        "authority": authority,
    }


def _workflow_coverage(tools: list[dict[str, Any]]) -> dict[str, bool]:
    searchable = "\n".join(_tool_text(tool).lower() for tool in tools)
    definitions = {
        "account_or_profile_discovery": ("advertiser account", "profile", "list account", "query account"),
        "report_generation": ("generate report", "create report", "reporting", "performance report"),
        "campaign_read": ("query campaign", "list campaign", "get campaign"),
        "campaign_create": ("create campaign", "campaign creation"),
        "campaign_update": ("update campaign", "set campaign", "pause campaign", "resume campaign"),
        "end_to_end_sp_campaign": ("end-to-end sponsored products", "end to end sponsored products", "launch sponsored products"),
        "locale_expansion": ("expand to", "new locale", "new country", "cross-region", "cross region"),
        "recommendations": ("recommendation", "recommendations"),
        "billing_or_finance_visible": ("billing", "invoice", "financial"),
    }
    return {
        name: any(needle in searchable for needle in needles)
        for name, needles in definitions.items()
    }


def audit_contract(discovery: dict[str, Any], source: str) -> dict[str, Any]:
    tools = discovery.get("tools") if isinstance(discovery.get("tools"), list) else []
    errors: list[str] = []
    warnings: list[str] = []
    names: set[str] = set()
    audited: list[dict[str, Any]] = []
    authority_counts: dict[str, int] = {}
    for index, tool in enumerate(tools):
        name = str(tool.get("name") or "").strip()
        if not name:
            errors.append(f"tools[{index}] has no name")
            continue
        if name in names:
            errors.append(f"duplicate tool name: {name}")
        names.add(name)
        schema = tool.get("inputSchema")
        if schema is None:
            schema = tool.get("input_schema")
        if not isinstance(schema, dict) or not schema:
            errors.append(f"{name}: missing inputSchema")
            schema = {}
        elif schema.get("type") not in {None, "object"}:
            errors.append(f"{name}: inputSchema root must be object")
        description = str(tool.get("description") or "").strip()
        if not description:
            warnings.append(f"{name}: missing description")
        classification = classify_tool(tool)
        authority = classification["authority"]
        authority_counts[authority] = authority_counts.get(authority, 0) + 1
        if authority == "unknown_fail_closed":
            warnings.append(f"{name}: unknown semantics must remain blocked")
        audited.append({
            "name": name,
            "description_hash": hashlib.sha256(description.encode()).hexdigest()[:16],
            "schema_hash": _digest(schema),
            "classification": classification,
            "annotations": tool.get("annotations") if isinstance(tool.get("annotations"), dict) else {},
        })
    coverage = _workflow_coverage(tools)
    for required in ("account_or_profile_discovery", "report_generation", "campaign_read", "campaign_create"):
        if not coverage[required]:
            errors.append(f"missing live MCP workflow coverage: {required}")
    initialize = discovery.get("initialize") if isinstance(discovery.get("initialize"), dict) else {}
    protocol = initialize.get("protocolVersion") or initialize.get("protocol_version")
    if not protocol:
        warnings.append("initialize response did not report protocolVersion")
    server_info = initialize.get("serverInfo") if isinstance(initialize.get("serverInfo"), dict) else {}
    manifest = {
        "manifest_version": 1,
        "source": source,
        "protocol_version": protocol,
        "server_info": {
            "name": server_info.get("name"),
            "version": server_info.get("version"),
        },
        "session_id_present": bool(discovery.get("session_id_present")),
        "tool_count": len(audited),
        "tool_manifest_hash": _digest(audited),
        "workflow_coverage": coverage,
        "authority_counts": dict(sorted(authority_counts.items())),
        "errors": errors,
        "warnings": warnings,
        "tools": sorted(audited, key=lambda item: item["name"]),
    }
    manifest["ok"] = not errors
    return manifest


def load_fixture(path: str) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("fixture must be a JSON object")
    if "tools" not in data and isinstance(data.get("result"), dict):
        data = {"initialize": {}, "tools": data["result"].get("tools", [])}
    data.setdefault("initialize", {
        "protocolVersion": PROTOCOL_VERSION,
        "serverInfo": {"name": "fixture", "version": "sandbox"},
        "capabilities": {"tools": {}},
    })
    data.setdefault("session_id_present", True)
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default=os.getenv("AMAZON_ADS_MCP_URL", DEFAULT_ENDPOINT))
    parser.add_argument("--token-env", default="AMAZON_ADS_MCP_ACCESS_TOKEN")
    parser.add_argument("--fixture")
    parser.add_argument("--output")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--allow-auth-required", action="store_true")
    parser.add_argument("--allow-localhost", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    source = args.fixture or args.endpoint
    try:
        if args.fixture:
            discovery = load_fixture(args.fixture)
        else:
            validate_endpoint(args.endpoint, allow_localhost=args.allow_localhost)
            token = os.getenv(args.token_env)
            discovery = MCPHTTPClient(args.endpoint, token).discover()
        manifest = audit_contract(discovery, source)
    except AuthRequired as exc:
        result = {
            "ok": False,
            "external_required": True,
            "reason": str(exc),
            "endpoint": args.endpoint,
            "credential_env": args.token_env,
        }
        if args.output:
            Path(args.output).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
        return 0 if args.allow_auth_required else 3
    except Exception as exc:
        print(f"amazon-mcp-contract: {exc}", file=sys.stderr)
        return 2
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": manifest["ok"],
        "tool_count": manifest["tool_count"],
        "workflow_coverage": manifest["workflow_coverage"],
        "authority_counts": manifest["authority_counts"],
        "errors": manifest["errors"],
        "warnings": manifest["warnings"],
        "tool_manifest_hash": manifest["tool_manifest_hash"],
    }, ensure_ascii=False))
    return 1 if args.check and not manifest["ok"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
