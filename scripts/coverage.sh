#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
rm -f .coverage
PYTHONPATH="$ROOT/control-plane:$ROOT/hermes-plugin:$ROOT/tests" PYTHONWARNINGS=error coverage run --branch --omit="*/scripts/us-only-daily-orchestrator.py" -m unittest discover -s tests -p 'test_*.py'
coverage report --fail-under="${COVERAGE_MIN:-78}"
