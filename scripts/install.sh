#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
python3 -m venv "$ROOT/.venv"
SITE_DIR="$($ROOT/.venv/bin/python - <<'PY2'
import site
print(site.getsitepackages()[0])
PY2
)"
printf '%s\n' "$ROOT/control-plane" > "$SITE_DIR/hermes_amazon_ads_control.pth"
mkdir -p "$HERMES_HOME/plugins"
ln -sfn "$ROOT/hermes-plugin/amazon_ads_control" "$HERMES_HOME/plugins/amazon_ads_control"
"$ROOT/.venv/bin/python" -c 'import amazon_ads_control; print("control-plane", amazon_ads_control.__version__)'
echo "Installed zero-dependency control plane and linked Hermes plugin."
echo "Next: configure /etc/hermes-amazon-ads-control.env, start the service, then restart Hermes gateway."
