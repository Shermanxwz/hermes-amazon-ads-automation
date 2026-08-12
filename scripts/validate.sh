#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
python3 -m compileall -q "$ROOT/control-plane" "$ROOT/hermes-plugin" "$ROOT/scripts" "$ROOT/integrations" "$ROOT/tests"
PYTHONPATH="$ROOT/control-plane:$ROOT/hermes-plugin:$ROOT/tests" PYTHONWARNINGS=error python3 -X dev -m unittest discover -s "$ROOT/tests" -p 'test_*.py' -v
python3 "$ROOT/scripts/check_amazon_mcp_contract.py" \
  --fixture "$ROOT/tests/fixtures/amazon_ads_mcp_contract.json" \
  --check
if command -v node >/dev/null 2>&1; then node --check "$ROOT/control-plane/amazon_ads_control/static/app.js"; fi
while IFS= read -r script; do bash -n "$script"; done < <(find "$ROOT/scripts" -maxdepth 1 -type f -name '*.sh' -print | sort)
python3 "$ROOT/scripts/verify-no-secrets.py"
echo "validation: OK"
