#!/usr/bin/env bash
set -euo pipefail

ROOT="${ADS_AUTOPILOT_ROOT:-/opt/hermes-amazon-ads-automation}"
LOCK="${ADS_ORCHESTRATOR_LOCK:-/run/lock/hermes-amazon-ads-us-only.lock}"

exec 9>"$LOCK"
flock -n 9 || exit 0

exec /usr/bin/python3 "$ROOT/scripts/us-only-daily-orchestrator.py"
