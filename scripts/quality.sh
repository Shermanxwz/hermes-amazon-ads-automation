#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
ruff check control-plane hermes-plugin integrations scripts tests --select E9,F63,F7,F82
bandit -q -r control-plane hermes-plugin integrations scripts -lll
while IFS= read -r -d '' file; do node --check "$file"; done < <(find control-plane/amazon_ads_control/static -name '*.js' -print0)
python3 scripts/verify-no-secrets.py
