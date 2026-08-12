#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

prepare_source() {
  local destination="$1"
  cp -a "$ROOT" "$destination"
  rm -rf \
    "$destination/.git" \
    "$destination/.ci-hermes-agent" \
    "$destination/.venv" \
    "$destination/.coverage" \
    "$destination/.pytest_cache" \
    "$destination/.ruff_cache" \
    "$destination/artifacts" \
    "$destination/build" \
    "$destination/dist"
  find "$destination" -type d \( -name __pycache__ -o -name '*.egg-info' \) -prune -exec rm -rf {} +
}

prepare_source "$TMP/package-src"
cd "$TMP/package-src"
EXPECTED_VERSION="$(python3 - <<'PY'
import tomllib
with open('pyproject.toml', 'rb') as handle:
    print(tomllib.load(handle)['project']['version'])
PY
)"
[[ -n "$EXPECTED_VERSION" ]]
if python3 -c 'import setuptools.build_meta' >/dev/null 2>&1; then
  python3 -m pip wheel . --no-deps --no-build-isolation -w "$TMP/dist" >/dev/null
else
  python3 -m pip wheel . --no-deps -w "$TMP/dist" >/dev/null
fi
python3 -m venv "$TMP/installed"
"$TMP/installed/bin/pip" install --no-deps "$TMP"/dist/*.whl >/dev/null
INSTALLED_VERSION="$("$TMP/installed/bin/python" -c 'import amazon_ads_control; print(amazon_ads_control.__version__)')"
[[ "$INSTALLED_VERSION" == "$EXPECTED_VERSION" ]]
"$TMP/installed/bin/amazon-ads-control" --help >/dev/null
[[ "$("$TMP/installed/bin/amazon-ads-control" --version)" == *"$EXPECTED_VERSION"* ]]
"$TMP/installed/bin/amazon-ads-worker" --help >/dev/null
[[ "$("$TMP/installed/bin/amazon-ads-worker" --version)" == *"$EXPECTED_VERSION"* ]]
"$TMP/installed/bin/amazon-ads-backtest" --help >/dev/null
export ADS_CONTROL_HOST=127.0.0.1 ADS_CONTROL_PORT=8790 ADS_CONTROL_DB="$TMP/state.db"
export ADS_CONTROL_AGENT_TOKEN="$(python3 -c 'print("x"*48)')"
export ADS_CONTROL_OPERATOR_TOKEN=
export ADS_CONTROL_ENABLE_COMMAND_APPROVAL=false
export ADS_MCP_DEFAULT_REGION=fe
export ADS_MCP_TOOLSETS=mcp-amazon-ads
export ADS_CONTROL_PASSWORD_HASH="$(PYTHONPATH="$ROOT/control-plane" python3 -c 'from amazon_ads_control.security import hash_password; print(hash_password("correct horse battery staple"))')"
"$TMP/installed/bin/amazon-ads-control" --check >/dev/null
INSTALLED_SITE="$("$TMP/installed/bin/python" -c 'import site; print(site.getsitepackages()[0])')"
PYTHONPATH="$INSTALLED_SITE" python3 "$ROOT/scripts/control_cli.py" storage-status --database "$TMP/state.db" >/dev/null

prepare_source "$TMP/source"
HERMES_HOME="$TMP/hermes" HERMES_BIN= bash "$TMP/source/scripts/install.sh" >/dev/null 2>&1
[[ -L "$TMP/hermes/plugins/amazon-ads-control" ]]

grep -q '^ADS_CONTROL_OPERATOR_TOKEN=$' deploy/control.env.example
grep -q '^ADS_CONTROL_ENABLE_COMMAND_APPROVAL=false$' deploy/control.env.example
grep -Eq '^ADS_MCP_DEFAULT_REGION=(na|eu|fe)$' deploy/control.env.example
grep -q '^ADS_MCP_TOOLSETS=' deploy/control.env.example
grep -q '^HERMES_HOME=' deploy/control.env.example
grep -q '^HERMES_PROFILE=' deploy/control.env.example
grep -q '^HERMES_BIN=' deploy/control.env.example
grep -q '^ADS_STREAM_PROFILE_ID=' deploy/control.env.example
grep -q '^ADS_CONTROL_MAINTENANCE_INTERVAL=' deploy/control.env.example
grep -q '^ADS_CONTROL_STORAGE_HARD_LIMIT_MB=' deploy/control.env.example
grep -q '^ADS_CONTROL_OUTBOX_MAX_BYTES=' deploy/control.env.example
grep -q '^LogRateLimitBurst=' deploy/amazon-ads-control.service
grep -Fq 'proxy_set_header X-Real-IP $remote_addr;' deploy/nginx.conf
grep -Fq 'proxy_set_header Origin $http_origin;' deploy/nginx.conf
if grep -Fq 'proxy_set_header Origin $scheme://$host;' deploy/nginx.conf; then
  echo "nginx must not synthesize a trusted Origin" >&2
  exit 1
fi

# Scheduled orchestrator is a Hermes trigger only. It must not regain root,
# direct Amazon OAuth/MCP or database-mutation authority.
grep -q '^User=amazonbot$' deploy/hermes-amazon-ads-us-orchestrator.service
grep -q '^Group=amazonbot$' deploy/hermes-amazon-ads-us-orchestrator.service
! grep -q '^User=root$' deploy/hermes-amazon-ads-us-orchestrator.service
! grep -q '^ExecStartPre=.*refresh_amazon_ads_token' deploy/hermes-amazon-ads-us-orchestrator.service
grep -q '^MemoryHigh=350M$' deploy/hermes-amazon-ads-us-orchestrator.service
grep -q '^MemoryMax=550M$' deploy/hermes-amazon-ads-us-orchestrator.service
grep -q '^CPUQuota=80%$' deploy/hermes-amazon-ads-us-orchestrator.service
grep -q '^TasksMax=128$' deploy/hermes-amazon-ads-us-orchestrator.service
grep -q '^Unit=hermes-amazon-ads-us-orchestrator.service$' deploy/hermes-amazon-ads-us-orchestrator.timer
! grep -Eq 'advertising-ai\.amazon\.com|AMAZON_ADS_MCP_ACCESS_TOKEN|sqlite3|record_action|normalized_snapshot_gzip' scripts/us-only-daily-orchestrator.py

python3 - "$EXPECTED_VERSION" <<'PY'
import json
import pathlib
import re
import sys
import tomllib

expected = sys.argv[1]
root = pathlib.Path('.')
with (root / 'pyproject.toml').open('rb') as handle:
    project = tomllib.load(handle)['project']['version']
manifest = json.loads((root / 'package-manifest.json').read_text())
runtime = (root / 'control-plane/amazon_ads_control/__init__.py').read_text()
plugin = (root / 'hermes-plugin/amazon_ads_control/plugin.yaml').read_text()
values = {
    'project': project,
    'manifest': str(manifest.get('version') or ''),
    'runtime': (re.search(r'__version__\s*=\s*"([^"]+)"', runtime) or [None, ''])[1],
    'plugin': (re.search(r'(?m)^version:\s*([0-9.]+)\s*$', plugin) or [None, ''])[1],
}
bad = {key: value for key, value in values.items() if value != expected}
if bad:
    raise SystemExit(f'deploy release identity mismatch: {bad}; expected {expected}')
PY
[[ -f .github/workflows/release.yml ]]
! compgen -G '.github/workflows/release-v*.yml' >/dev/null

cd "$ROOT"
if command -v nginx >/dev/null 2>&1; then
  { echo "pid $TMP/nginx.pid;"; echo "error_log $TMP/error.log;"; echo 'events {}'; echo 'http {'; echo "access_log $TMP/access.log;"; echo 'server {'; echo 'listen 8080;'; cat deploy/nginx.conf; echo '}'; echo '}'; } > "$TMP/nginx.conf"
  nginx -t -c "$TMP/nginx.conf" -p "$TMP" >/dev/null
fi

if command -v systemd-analyze >/dev/null 2>&1; then
  : > "$TMP/control.env"
  sed -e 's/User=amazonbot/User=root/' -e 's/Group=amazonbot/Group=root/' \
      -e "s#WorkingDirectory=/opt/hermes-amazon-ads-automation#WorkingDirectory=$ROOT#" \
      -e "s#EnvironmentFile=/etc/hermes-amazon-ads-control.env#EnvironmentFile=-$TMP/control.env#" \
      -e "s#Environment=PYTHONPATH=/opt/hermes-amazon-ads-automation/control-plane#Environment=PYTHONPATH=$ROOT/control-plane#" \
      -e "s#ExecStart=/opt/hermes-amazon-ads-automation/.venv/bin/python -m amazon_ads_control.server#ExecStart=$TMP/installed/bin/python -m amazon_ads_control.server#" \
      -e "s#ReadWritePaths=/var/lib/hermes-amazon-ads-control#ReadWritePaths=$TMP#" \
      deploy/amazon-ads-control.service > "$TMP/control.service"
  systemd-analyze verify "$TMP/control.service" >/dev/null

  mkdir -p "$TMP/orchestrator-units"
  sed -e 's/User=amazonbot/User=root/' -e 's/Group=amazonbot/Group=root/' \
      -e "s#WorkingDirectory=/opt/hermes-amazon-ads-automation#WorkingDirectory=$ROOT#" \
      -e "s#EnvironmentFile=/etc/hermes-amazon-ads-control.env#EnvironmentFile=-$TMP/control.env#" \
      -e "s#Environment=ADS_AUTOPILOT_ROOT=/opt/hermes-amazon-ads-automation#Environment=ADS_AUTOPILOT_ROOT=$ROOT#" \
      -e "s#ExecStart=/bin/bash /opt/hermes-amazon-ads-automation/scripts/us-only-daily-orchestrator.sh#ExecStart=/bin/bash $ROOT/scripts/us-only-daily-orchestrator.sh#" \
      -e "s#ReadWritePaths=/var/lib/hermes-amazon-ads-control /var/lib/hermes-studio /run/lock#ReadWritePaths=$TMP#" \
      deploy/hermes-amazon-ads-us-orchestrator.service > "$TMP/orchestrator-units/hermes-amazon-ads-us-orchestrator.service"
  cp deploy/hermes-amazon-ads-us-orchestrator.timer "$TMP/orchestrator-units/hermes-amazon-ads-us-orchestrator.timer"
  systemd-analyze verify \
    "$TMP/orchestrator-units/hermes-amazon-ads-us-orchestrator.service" \
    "$TMP/orchestrator-units/hermes-amazon-ads-us-orchestrator.timer" >/dev/null
fi

echo "deploy-validation: OK"
