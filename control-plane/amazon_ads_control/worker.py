"""Optional external worker heartbeat utility.

Hermes native delegate_task workers are the primary execution path. This command is kept for
future remote workers and health probes; it deliberately does not execute Amazon operations.
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import time

from . import __version__
from .client import ControlClient


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send a non-writing external worker heartbeat")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    token = os.getenv("ADS_CONTROL_AGENT_TOKEN", "")
    if len(token) < 32:
        print("ADS_CONTROL_AGENT_TOKEN is missing or too short", file=sys.stderr)
        return 2
    client = ControlClient(os.getenv("ADS_CONTROL_URL", "http://127.0.0.1:8790"), token)
    while True:
        result = client.request("POST", "/api/agent/events", {
            "type": "external_worker.heartbeat", "actor": "external-worker",
            "message": "External worker heartbeat", "data": {"host": socket.gethostname()},
        })
        if result.get("error"):
            print(f"heartbeat failed: {result['error']}", file=sys.stderr)
            if args.once:
                return 2
        elif args.once:
            return 0
        time.sleep(max(10, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
