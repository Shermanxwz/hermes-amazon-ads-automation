#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
HERMES_PROFILE="${HERMES_PROFILE:-default}"
HERMES_BIN="${HERMES_BIN:-$(command -v hermes || true)}"
LIVE=0
[[ "${1:-}" == "--live" ]] && LIVE=1

PLUGIN="$HERMES_HOME/plugins/amazon-ads-control"
[[ -e "$PLUGIN/plugin.yaml" ]]
[[ -e "$PLUGIN/__init__.py" ]]
[[ -e "$PLUGIN/skill/SKILL.md" ]]

if [[ -z "$HERMES_BIN" ]]; then
  if [[ "$LIVE" == "1" ]]; then
    echo "Hermes CLI is required for live Hermes Studio acceptance" >&2
    exit 1
  fi
  echo "hermes-studio-integration: static plugin layout OK; Hermes CLI unavailable for live acceptance"
  exit 0
fi

ARGS=(--profile "$HERMES_PROFILE")
HERMES_HOME="$HERMES_HOME" "$HERMES_BIN" "${ARGS[@]}" plugins enable amazon-ads-control >/dev/null
LISTING="$(HERMES_HOME="$HERMES_HOME" "$HERMES_BIN" "${ARGS[@]}" plugins list)"
grep -q 'amazon-ads-control' <<<"$LISTING"

if [[ "$LIVE" == "1" ]]; then
  RESPONSE="$(HERMES_HOME="$HERMES_HOME" "$HERMES_BIN" "${ARGS[@]}" --source tool --max-turns 20 -z \
    'Call ads_control_status exactly once. If and only if that tool succeeds and returns a controller role and mode, output exactly HERMES_ADS_CONTROL_OK. Otherwise output exactly HERMES_ADS_CONTROL_FAIL.')"
  [[ "$RESPONSE" == "HERMES_ADS_CONTROL_OK" ]] || {
    echo "Hermes one-shot could not reach the Amazon Ads Control plugin/control plane" >&2
    exit 1
  }
fi

echo "hermes-studio-integration: OK profile=$HERMES_PROFILE live=$LIVE"
