#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
python3 -m compileall -q "$ROOT/control-plane" "$ROOT/hermes-plugin" "$ROOT/scripts"
PYTHONPATH="$ROOT/control-plane:$ROOT/hermes-plugin" python3 -m unittest discover -s "$ROOT/tests" -v
python3 "$ROOT/scripts/verify-no-secrets.py"
echo "validation: OK"
