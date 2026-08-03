#!/usr/bin/env python3
"""Verify the official Amazon Ads MCP endpoint is reachable and auth-protected.

No credentials are sent. A 401/403 response is the expected healthy contract:
the endpoint exists and refuses unauthenticated MCP initialization.
"""
from __future__ import annotations

import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

URL = "https://advertising-ai.amazon.com/mcp"
BODY = json.dumps({
    "jsonrpc": "2.0",
    "id": "reachability-check",
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "hermes-amazon-ads-ci", "version": "2.0.0"}},
}).encode()


def main() -> int:
    request = Request(URL, data=BODY, method="POST", headers={
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": "hermes-amazon-ads-contract-check/2",
    })
    try:
        with urlopen(request, timeout=30) as response:
            status = response.status
            body = response.read(512).decode("utf-8", "replace")
    except HTTPError as exc:
        status = exc.code
        body = exc.read(512).decode("utf-8", "replace")
    except URLError as exc:
        print(f"amazon-mcp-reachability: network failure: {exc}", file=sys.stderr)
        return 2
    if status not in {401, 403}:
        print(f"amazon-mcp-reachability: expected auth protection, got HTTP {status}: {body[:200]}", file=sys.stderr)
        return 1
    print(f"amazon-mcp-reachability: OK (HTTP {status}, auth protected)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
