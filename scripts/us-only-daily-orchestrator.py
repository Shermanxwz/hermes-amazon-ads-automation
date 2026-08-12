#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone

PROMPT = """Run one scheduled Amazon Ads full-managed cycle through the installed amazon-ads-control plugin.

Hard requirements:
- Start with ads_control_status and synchronize the live Amazon Ads MCP catalog.
- Operate only enabled US Sponsored Products Profiles already registered in the controller. Never guess or print a Profile/account identifier.
- Treat the owner's daily maximum ad spend as the single commercial budget boundary. Before any spend-increasing action, require fresh same-day Sponsored Products spend evidence from the controller. If spend evidence is stale or missing, do not increase spend.
- Obtain a fresh complete Amazon Campaign read when a Campaign create/budget change/enable needs a monetary reservation or state proof. Do not reinterpret the owner's daily spend cap as a blanket 2x reduction of all active Campaign budgets.
- Recover or advance persistent report jobs, then use controller-stored normalized report evidence and lineage for performance optimization. Never mutate an INGESTED report snapshot.
- Historical performance evidence controls action size and confidence; lack of mature historical performance data alone is not a reason to forbid a small exploration. When useful, create a bounded HERMES-SP-EXP-* exploratory Sponsored Products plan inside the controller-reported exploration share.
- Use only atomic controller-approved Sponsored Products actions. Billing/account administration, delete/archive/remove, cross-region, unknown, drifted, composite and irreversible operations remain forbidden.
- If decisions exist, create/recover the task and delegate exactly one bound Executor. The Executor may execute only controller-reserved decisions and must obey fresh-read/CAS requirements.
- Stop the Executor and delegate a different read-only Verifier session. Independently read Amazon, bind the verifier's read evidence, verify every expected field, and reconcile any uncertain/mismatched result without blind replay.
- For structural creation, keep the graph PAUSED until independently verified, then continue every controller-released activation rank leaf-to-Campaign. Do not stop after creation if activation_transition says another verified stage is planned.
- Never self-authorize a tool result, never write runtime status on behalf of another component, and never bypass the controller because a tool appears callable.
- On any trust, daily-spend, schema, OAuth, data-quality, storage or verification uncertainty, fail closed and leave spend pressure unchanged or lower.

Finish with a short operational summary that contains no Profile ID, advertiser/account ID, token, email, IP address, campaign/customer identifier or other private identifier.
"""


def _hermes_command() -> list[str]:
    configured = os.getenv("HERMES_BIN", "").strip()
    executable = configured or shutil.which("hermes") or "/opt/hermes-agent/venv/bin/hermes"
    if not Path(executable).exists() and shutil.which(executable) is None:
        raise RuntimeError("Hermes CLI not found; set HERMES_BIN to the Hermes Studio/Hermes runtime CLI")
    command = [executable]
    profile = os.getenv("HERMES_PROFILE", "").strip()
    if profile:
        command += ["--profile", profile]
    command += [
        "--source", "tool",
        "--max-turns", os.getenv("ADS_ORCHESTRATOR_MAX_TURNS", "90"),
        "-z", PROMPT,
    ]
    return command


def main() -> int:
    started = datetime.now(timezone.utc)
    try:
        completed = subprocess.run(
            _hermes_command(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=int(os.getenv("ADS_ORCHESTRATOR_TIMEOUT_SECONDS", "1500")),
            check=False,
        )
        if completed.returncode != 0:
            print(f"hermes_ads_cycle=failed returncode={completed.returncode}", file=sys.stderr)
            return completed.returncode or 1
        elapsed = int((datetime.now(timezone.utc) - started).total_seconds())
        print(f"hermes_ads_cycle=ok elapsed_seconds={elapsed}")
        return 0
    except subprocess.TimeoutExpired:
        print("hermes_ads_cycle=failed reason=timeout", file=sys.stderr)
        return 124
    except Exception as exc:
        print(f"hermes_ads_cycle=failed reason={type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
