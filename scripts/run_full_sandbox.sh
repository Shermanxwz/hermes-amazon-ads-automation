#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARTIFACT_DIR="${FULL_SANDBOX_ARTIFACT_DIR:-$ROOT/artifacts/full-managed-sandbox}"
RESULTS="$ARTIFACT_DIR/results.tsv"
mkdir -p "$ARTIFACT_DIR"
: > "$RESULTS"

record() {
  local status="$1" layer="$2" name="$3" detail="${4:-}"
  printf '%s\t%s\t%s\t%s\n' "$status" "$layer" "$name" "${detail//$'\n'/ }" >> "$RESULTS"
}
run_required() {
  local layer="$1" name="$2"; shift 2
  echo "==> $name"
  if "$@"; then record PASS "$layer" "$name"; else local code=$?; record FAIL "$layer" "$name" "exit=$code"; fi
  return 0
}

export PYTHONPATH="$ROOT/control-plane:$ROOT/hermes-plugin:$ROOT/tests:$ROOT"
export PYTHONWARNINGS=error

run_required repository "Compile all Python sources" python3 -m compileall -q "$ROOT/control-plane" "$ROOT/hermes-plugin" "$ROOT/scripts" "$ROOT/integrations" "$ROOT/tests"
run_required repository "Unit and integration suite" bash "$ROOT/scripts/validate.sh"
run_required decision_os "Full-managed ACOS v5 focused suite" python3 -m unittest discover -s "$ROOT/tests" -p 'test_*v5.py' -v
run_required amazon_mcp "Offline MCP initialize/tools-list/schema/authority fixture" python3 "$ROOT/scripts/check_amazon_mcp_contract.py" --fixture "$ROOT/tests/fixtures/amazon_ads_mcp_contract.json" --check --output "$ARTIFACT_DIR/mcp-fixture-manifest.json"
run_required amazon_postman "Official legacy Postman semantic compiler and strict capability matrix" python3 "$ROOT/scripts/sync_official_contracts.py" --check --strict-extended --output "$ARTIFACT_DIR/postman-capabilities.json"
run_required amazon_postman "Official Unified API GA/Beta separation contract" python3 "$ROOT/scripts/check_unified_api_contract.py" --check --output "$ARTIFACT_DIR/unified-api-contract.json"
run_required amazon_postman "Every official capability has an explicit project policy" python3 "$ROOT/scripts/check_project_capability_policy.py" --manifest "$ARTIFACT_DIR/postman-capabilities.json" --policy "$ROOT/official/project-capability-policy.json" --output "$ARTIFACT_DIR/project-capability-policy.json"
run_required amazon_postman "Official Postman fingerprint drift gate" python3 "$ROOT/scripts/check_official_fingerprint.py" --manifest "$ARTIFACT_DIR/postman-capabilities.json" --baseline "$ROOT/official/postman-semantic-baseline.json"
run_required amazon_mcp "Official Amazon Ads MCP endpoint reachability" python3 "$ROOT/scripts/check_amazon_mcp_reachability.py"
run_required recovery "Concurrency, recovery, HTTP and SQLite stress" python3 "$ROOT/tests/stress_recovery.py"

if command -v coverage >/dev/null 2>&1; then run_required quality "Branch coverage gate" bash "$ROOT/scripts/coverage.sh"; else record EXTERNAL quality "Branch coverage gate" "coverage package is not installed"; fi
if command -v ruff >/dev/null 2>&1 && command -v bandit >/dev/null 2>&1 && command -v node >/dev/null 2>&1; then run_required quality "Ruff, Bandit, JavaScript and secret checks" env PYTHONWARNINGS=default bash "$ROOT/scripts/quality.sh"; else record EXTERNAL quality "Ruff, Bandit, JavaScript and secret checks" "ruff, bandit and Node.js are required"; fi
if command -v nginx >/dev/null 2>&1; then run_required deployment "Wheel, fresh install, systemd and Nginx validation" env PYTHONWARNINGS=default bash "$ROOT/scripts/validate_deploy.sh"; else record EXTERNAL deployment "Wheel, fresh install, systemd and Nginx validation" "nginx is not installed"; fi
# Historical gate marker: run_required browser "Real Chromium Web and approval E2E"
if python3 -c 'import playwright' >/dev/null 2>&1; then
  run_required browser "Real Chromium full-managed Web E2E" python3 "$ROOT/tests/browser_e2e.py"
  run_required browser "Chromium/Firefox/WebKit matrix E2E" python3 "$ROOT/tests/browser_matrix_e2e.py"
else record EXTERNAL browser "Browser E2E" "playwright is not installed"; fi
if python3 -c 'import hermes_cli' >/dev/null 2>&1; then run_required hermes "Pinned real Hermes plugin-manager load" env PYTHONWARNINGS=default python3 "$ROOT/tests/real_hermes_smoke.py"; else record EXTERNAL hermes "Pinned real Hermes plugin-manager load" "Hermes source/package is not installed"; fi

if [[ "${FULL_SANDBOX_LIVE_MCP:-0}" == "1" ]]; then
  if [[ -n "${AMAZON_ADS_MCP_ACCESS_TOKEN:-}" ]]; then run_required amazon_live "Authenticated live MCP initialize and complete tools/list" python3 "$ROOT/scripts/check_amazon_mcp_contract.py" --check --output "$ARTIFACT_DIR/mcp-live-manifest.json"; else record FAIL amazon_live "Authenticated live MCP initialize and complete tools/list" "FULL_SANDBOX_LIVE_MCP=1 requires AMAZON_ADS_MCP_ACCESS_TOKEN"; fi
else record EXTERNAL amazon_live "Authenticated live MCP initialize and complete tools/list" "set FULL_SANDBOX_LIVE_MCP=1 with an owner access token"; fi
record EXTERNAL amazon_live "OAuth authorization and refresh rotation" "requires owner Login with Amazon consent"
record EXTERNAL amazon_live "Real Profiles, currencies and manager relationships" "account-specific evidence"
record EXTERNAL amazon_live "Real report submit, poll, GZIP download and parsing" "requires Amazon report IDs"
record EXTERNAL amazon_live "Real 429 Retry-After behavior" "must be observed safely in the owner account"
record EXTERNAL amazon_live "Marketing Stream AWS delivery" "requires the owner AWS subscription"
record EXTERNAL amazon_live "Test Account full-managed Campaign hierarchy canary" "requires Amazon Test Account or bounded real profile"
record EXTERNAL amazon_live "Independent Amazon read-back" "only Amazon can prove persisted state"
record EXTERNAL amazon_live "Full attribution-window shadow evaluation" "requires matured advertiser data"
record EXTERNAL host_live "2C2G soak, reboot and systemd recovery" "requires target VPS"
record EXTERNAL host_live "HTTPS and backup-restore drill" "requires deployed DNS, TLS and filesystem"

python3 - "$RESULTS" "$ARTIFACT_DIR/report.json" "$ARTIFACT_DIR/report.md" <<'PY'
import csv, json, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
source, json_path, md_path = map(Path, sys.argv[1:])
rows = []
with source.open(encoding="utf-8") as handle:
    for status, layer, name, detail in csv.reader(handle, delimiter="\t"):
        rows.append({"status": status, "layer": layer, "name": name, "detail": detail})
counts = Counter(row["status"] for row in rows); layers = defaultdict(Counter)
for row in rows: layers[row["layer"]][row["status"]] += 1
overall = "FAIL" if counts["FAIL"] else "PASS_WITH_EXTERNAL_ACCEPTANCE" if counts["EXTERNAL"] else "PASS"
report = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "overall": overall,
          "counts": dict(counts), "layers": {key: dict(value) for key, value in sorted(layers.items())}, "results": rows}
json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
lines = ["# Hermes Amazon Ads Full-Managed Sealed ACOS v5 Sandbox Report", "", f"- Overall: **{overall}**",
         f"- PASS: {counts['PASS']}", f"- FAIL: {counts['FAIL']}", f"- EXTERNAL: {counts['EXTERNAL']}", "",
         "| Status | Layer | Check | Detail |", "|---|---|---|---|"]
for row in rows: lines.append(f"| {row['status']} | {row['layer']} | {row['name']} | {row['detail'].replace('|', chr(92)+'|')} |")
md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(json.dumps({"overall": overall, **counts, "report": str(json_path)}, ensure_ascii=False))
sys.exit(1 if overall == "FAIL" else 0)
PY
