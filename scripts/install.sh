#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
HERMES_PROFILE="${HERMES_PROFILE:-}"
HERMES_BIN="${HERMES_BIN:-$(command -v hermes || true)}"

python3 -m venv "$ROOT/.venv"
SITE_DIR="$($ROOT/.venv/bin/python - <<'PY2'
import site
print(site.getsitepackages()[0])
PY2
)"
printf '%s\n' "$ROOT/control-plane" > "$SITE_DIR/hermes_amazon_ads_control.pth"
mkdir -p "$HERMES_HOME/plugins"
ln -sfn "$ROOT/hermes-plugin/amazon_ads_control" "$HERMES_HOME/plugins/amazon-ads-control"
"$ROOT/.venv/bin/python" -c 'import amazon_ads_control; print("control-plane", amazon_ads_control.__version__)'

if [[ -n "$HERMES_BIN" ]]; then
  HERMES_ARGS=()
  if [[ -n "$HERMES_PROFILE" ]]; then
    HERMES_ARGS+=(--profile "$HERMES_PROFILE")
  fi
  HERMES_HOME="$HERMES_HOME" "$HERMES_BIN" "${HERMES_ARGS[@]}" plugins enable amazon-ads-control >/dev/null
  HERMES_HOME="$HERMES_HOME" "$HERMES_BIN" "${HERMES_ARGS[@]}" plugins list >/dev/null
  echo "Hermes plugin linked and enabled for the selected Hermes/Hermes Studio profile."
else
  echo "Hermes CLI not found; plugin linked but not enabled. Run: hermes plugins enable amazon-ads-control" >&2
fi

echo "Configure /etc/hermes-amazon-ads-control.env, start the control service, then restart the Hermes Studio/Hermes runtime using the same HERMES_HOME and profile."
