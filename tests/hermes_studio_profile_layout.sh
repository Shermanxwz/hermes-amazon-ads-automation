#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

BASE="$TMP/hermes-home"
FAKE="$TMP/hermes"
cat > "$FAKE" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$*" == *"plugins list"* ]]; then
  echo "amazon-ads-control enabled"
fi
if [[ "$*" == *"--source tool"* ]]; then
  echo "HERMES_ADS_CONTROL_OK"
fi
SH
chmod +x "$FAKE"

HERMES_HOME="$BASE" HERMES_PROFILE="ads-prod" HERMES_BIN="$FAKE" \
  bash "$ROOT/scripts/install.sh" >/dev/null

PROFILE_PLUGIN="$BASE/profiles/ads-prod/plugins/amazon-ads-control"
[[ -L "$PROFILE_PLUGIN" ]]
[[ -e "$PROFILE_PLUGIN/plugin.yaml" ]]
[[ ! -e "$BASE/plugins/amazon-ads-control" ]]

HERMES_HOME="$BASE" HERMES_PROFILE="ads-prod" HERMES_BIN="$FAKE" \
  bash "$ROOT/scripts/validate_hermes_studio.sh" >/dev/null

# Also accept a caller that already resolved HERMES_HOME to the named Profile.
HERMES_HOME="$BASE/profiles/ads-prod" HERMES_PROFILE="ads-prod" HERMES_BIN="$FAKE" \
  bash "$ROOT/scripts/validate_hermes_studio.sh" >/dev/null

echo "hermes-studio-profile-layout: OK"
