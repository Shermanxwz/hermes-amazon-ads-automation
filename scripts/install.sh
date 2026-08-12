#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
HERMES_PROFILE="${HERMES_PROFILE:-default}"
HERMES_BIN="${HERMES_BIN:-$(command -v hermes || true)}"

# Hermes Studio uses one base home and stores non-default Profiles below
# <base>/profiles/<name>. Accept either the base home (recommended) or an
# already-resolved named Profile home, but always invoke Hermes from the base.
HERMES_BASE_HOME="$HERMES_HOME"
if [[ "$HERMES_PROFILE" != "default" ]]; then
  if [[ "$HERMES_HOME" == */profiles/"$HERMES_PROFILE" ]]; then
    HERMES_PROFILE_HOME="$HERMES_HOME"
    HERMES_BASE_HOME="${HERMES_HOME%/profiles/$HERMES_PROFILE}"
  else
    HERMES_PROFILE_HOME="$HERMES_HOME/profiles/$HERMES_PROFILE"
  fi
else
  HERMES_PROFILE_HOME="$HERMES_HOME"
fi

python3 -m venv "$ROOT/.venv"
SITE_DIR="$($ROOT/.venv/bin/python - <<'PY2'
import site
print(site.getsitepackages()[0])
PY2
)"
printf '%s\n' "$ROOT/control-plane" > "$SITE_DIR/hermes_amazon_ads_control.pth"
mkdir -p "$HERMES_PROFILE_HOME/plugins"
ln -sfn "$ROOT/hermes-plugin/amazon_ads_control" "$HERMES_PROFILE_HOME/plugins/amazon-ads-control"
"$ROOT/.venv/bin/python" -c 'import amazon_ads_control; print("control-plane", amazon_ads_control.__version__)'

if [[ -n "$HERMES_BIN" ]]; then
  HERMES_ARGS=(--profile "$HERMES_PROFILE")
  HERMES_HOME="$HERMES_BASE_HOME" "$HERMES_BIN" "${HERMES_ARGS[@]}" plugins enable amazon-ads-control >/dev/null
  HERMES_HOME="$HERMES_BASE_HOME" "$HERMES_BIN" "${HERMES_ARGS[@]}" plugins list >/dev/null
  echo "Hermes plugin linked and enabled for profile=$HERMES_PROFILE home=$HERMES_PROFILE_HOME"
else
  echo "Hermes CLI not found; plugin linked but not enabled. Run Hermes with HERMES_HOME=$HERMES_BASE_HOME and profile=$HERMES_PROFILE, then enable amazon-ads-control." >&2
fi

echo "Configure /etc/hermes-amazon-ads-control.env, start the control service, then restart Hermes Studio/Hermes with the same base home and Profile."
