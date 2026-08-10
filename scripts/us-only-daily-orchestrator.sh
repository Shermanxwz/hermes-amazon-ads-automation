#!/usr/bin/env bash
set -euo pipefail

export HOME=/var/lib/hermes-studio
export PYTHONPATH=/opt/hermes-amazon-ads-automation/control-plane:/opt/hermes-amazon-ads-automation/hermes-plugin

LOCK=/run/lock/hermes-amazon-ads-us-only.lock
exec 9>"$LOCK"
flock -n 9 || exit 0

exec /opt/hermes-agent/venv/bin/python3 /opt/hermes-amazon-ads-automation/scripts/us-only-daily-orchestrator.py
