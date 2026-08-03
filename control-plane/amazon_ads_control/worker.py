"""Optional external worker heartbeat utility.

Hermes native delegate_task workers are the primary execution path. This command is kept for
future remote workers and health probes; it deliberately does not execute Amazon operations.
"""
from __future__ import annotations

import argparse
import os
import socket
import time

from .client import ControlClient


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    client = ControlClient(
        os.getenv("ADS_CONTROL_URL", "http://127.0.0.1:8790"),
        os.getenv("ADS_CONTROL_AGENT_TOKEN", ""),
    )
    while True:
        client.request("POST", "/api/agent/events", {
            "type": "external_worker.heartbeat",
            "actor": "external-worker",
            "message": "External worker heartbeat",
            "data": {"host": socket.gethostname()},
        })
        if args.once:
            return 0
        time.sleep(max(10, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
