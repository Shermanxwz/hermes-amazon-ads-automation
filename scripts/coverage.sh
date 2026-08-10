#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
rm -f .coverage
PYTHONPATH="$ROOT/control-plane:$ROOT/hermes-plugin:$ROOT/tests" PYTHONWARNINGS=error coverage run --branch -m unittest discover -s tests -p 'test_*.py'
# The release gate measures production runtime code. Stand-alone contract/CI
# compiler scripts are exercised by their own CI jobs and must not dilute the
# runtime branch metric. The scheduled orchestrator is explicitly in scope.
coverage report \
  --include="control-plane/amazon_ads_control/*,hermes-plugin/amazon_ads_control/*,integrations/*,scripts/us-only-daily-orchestrator.py" \
  --fail-under="${COVERAGE_MIN:-80}"
