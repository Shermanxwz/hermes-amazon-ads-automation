#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
python3 -m compileall -q "$ROOT/control-plane" "$ROOT/hermes-plugin" "$ROOT/scripts" "$ROOT/integrations" "$ROOT/tests"
PYTHONPATH="$ROOT/control-plane:$ROOT/hermes-plugin:$ROOT/tests" python3 -m unittest discover -s "$ROOT/tests" -p 'test_*.py' -v
python3 "$ROOT/scripts/verify-no-secrets.py"
echo "validation: OK"
