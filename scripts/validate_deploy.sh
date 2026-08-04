#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
cp -a "$ROOT" "$TMP/package-src"
rm -rf "$TMP/package-src/.venv" "$TMP/package-src/build" "$TMP/package-src/.coverage"
find "$TMP/package-src" -type d \( -name __pycache__ -o -name '*.egg-info' \) -prune -exec rm -rf {} +
cd "$TMP/package-src"
if python3 -c 'import setuptools.build_meta' >/dev/null 2>&1; then
  python3 -m pip wheel . --no-deps --no-build-isolation -w "$TMP/dist" >/dev/null
else
  # Minimal hosted Python images may omit setuptools from the runner environment.
  # Let PEP 517 create an isolated build environment from pyproject.toml instead.
  python3 -m pip wheel . --no-deps -w "$TMP/dist" >/dev/null
fi
python3 -m venv "$TMP/installed"
"$TMP/installed/bin/pip" install --no-deps "$TMP"/dist/*.whl >/dev/null
"$TMP/installed/bin/amazon-ads-control" --help >/dev/null
[[ "$("$TMP/installed/bin/amazon-ads-control" --version)" == *"2.1.0" ]]
"$TMP/installed/bin/amazon-ads-worker" --help >/dev/null
[[ "$("$TMP/installed/bin/amazon-ads-worker" --version)" == *"2.1.0" ]]
"$TMP/installed/bin/amazon-ads-backtest" --help >/dev/null
export ADS_CONTROL_HOST=127.0.0.1 ADS_CONTROL_PORT=8790 ADS_CONTROL_DB="$TMP/state.db"
export ADS_CONTROL_AGENT_TOKEN="$(python3 -c 'print("x"*48)')"
export ADS_CONTROL_PASSWORD_HASH="$(PYTHONPATH="$ROOT/control-plane" python3 -c 'from amazon_ads_control.security import hash_password; print(hash_password("correct horse battery staple"))')"
"$TMP/installed/bin/amazon-ads-control" --check >/dev/null
cp -a "$ROOT" "$TMP/source"
rm -rf "$TMP/source/.venv" "$TMP/source/build" "$TMP/source/.coverage"
find "$TMP/source" -type d \( -name __pycache__ -o -name '*.egg-info' \) -prune -exec rm -rf {} +
HERMES_HOME="$TMP/hermes" bash "$TMP/source/scripts/install.sh" >/dev/null
[[ -L "$TMP/hermes/plugins/amazon-ads-control" ]]
cd "$ROOT"
if command -v nginx >/dev/null 2>&1; then
  { echo 'events {}'; echo 'http {'; echo 'server {'; echo 'listen 8080;'; cat deploy/nginx.conf; echo '}'; echo '}'; } > "$TMP/nginx.conf"
  nginx -t -c "$TMP/nginx.conf" -p "$TMP" >/dev/null
fi
if command -v systemd-analyze >/dev/null 2>&1; then
  sed -e 's/User=amazonbot/User=root/' -e 's/Group=amazonbot/Group=root/' \
      -e "s#WorkingDirectory=/opt/hermes-amazon-ads-automation#WorkingDirectory=$ROOT#" \
      -e "s#EnvironmentFile=/etc/hermes-amazon-ads-control.env#EnvironmentFile=-$TMP/control.env#" \
      -e "s#Environment=PYTHONPATH=/opt/hermes-amazon-ads-automation/control-plane#Environment=PYTHONPATH=$ROOT/control-plane#" \
      -e "s#ExecStart=/opt/hermes-amazon-ads-automation/.venv/bin/python -m amazon_ads_control.server#ExecStart=$TMP/installed/bin/python -m amazon_ads_control.server#" \
      -e "s#ReadWritePaths=/var/lib/hermes-amazon-ads-control#ReadWritePaths=$TMP#" \
      deploy/amazon-ads-control.service > "$TMP/control.service"
  : > "$TMP/control.env"
  systemd-analyze verify "$TMP/control.service" >/dev/null
fi
echo "deploy-validation: OK"
