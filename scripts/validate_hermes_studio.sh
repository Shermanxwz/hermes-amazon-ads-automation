#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
HERMES_PROFILE="${HERMES_PROFILE:-default}"
HERMES_BIN="${HERMES_BIN:-$(command -v hermes || true)}"
HERMES_STUDIO_URL="${HERMES_STUDIO_URL:-}"
HERMES_STUDIO_AUTH_TOKEN="${HERMES_STUDIO_AUTH_TOKEN:-}"
LIVE=0
STUDIO_LIVE=0
for arg in "$@"; do
  case "$arg" in
    --live) LIVE=1 ;;
    --studio-live) LIVE=1; STUDIO_LIVE=1 ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

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

PLUGIN="$HERMES_PROFILE_HOME/plugins/amazon-ads-control"
[[ -e "$PLUGIN/plugin.yaml" ]]
[[ -e "$PLUGIN/__init__.py" ]]
[[ -e "$PLUGIN/skill/SKILL.md" ]]

if [[ -z "$HERMES_BIN" ]]; then
  if [[ "$LIVE" == "1" ]]; then
    echo "Hermes CLI is required for live Hermes Studio acceptance" >&2
    exit 1
  fi
  echo "hermes-studio-integration: static plugin layout OK profile=$HERMES_PROFILE home=$HERMES_PROFILE_HOME; Hermes CLI unavailable for live acceptance"
  exit 0
fi

ARGS=(--profile "$HERMES_PROFILE")
HERMES_HOME="$HERMES_BASE_HOME" "$HERMES_BIN" "${ARGS[@]}" plugins enable amazon-ads-control >/dev/null
LISTING="$(HERMES_HOME="$HERMES_BASE_HOME" "$HERMES_BIN" "${ARGS[@]}" plugins list)"
grep -q 'amazon-ads-control' <<<"$LISTING"

PROMPT='Call ads_control_status exactly once. If and only if that tool succeeds and returns a controller role and mode, output exactly HERMES_ADS_CONTROL_OK. Otherwise output exactly HERMES_ADS_CONTROL_FAIL.'
if [[ "$LIVE" == "1" ]]; then
  RESPONSE="$(HERMES_HOME="$HERMES_BASE_HOME" "$HERMES_BIN" "${ARGS[@]}" --source tool --max-turns 20 -z "$PROMPT")"
  [[ "$RESPONSE" == "HERMES_ADS_CONTROL_OK" ]] || {
    echo "Hermes one-shot could not reach the Amazon Ads Control plugin/control plane" >&2
    exit 1
  }
fi

if [[ "$STUDIO_LIVE" == "1" ]]; then
  [[ -n "$HERMES_STUDIO_URL" ]] || { echo "--studio-live requires HERMES_STUDIO_URL" >&2; exit 1; }
  [[ -n "$HERMES_STUDIO_AUTH_TOKEN" ]] || { echo "--studio-live requires HERMES_STUDIO_AUTH_TOKEN" >&2; exit 1; }
  command -v curl >/dev/null 2>&1 || { echo "--studio-live requires curl" >&2; exit 1; }

  PAYLOAD="$(python3 - "$HERMES_PROFILE" "$PROMPT" <<'PY'
import json, sys
print(json.dumps({
    "profile": sys.argv[1],
    "input": sys.argv[2],
    "source": "tool",
    "timeout_ms": 300000,
    "include_events": True,
}))
PY
)"
  STUDIO_RESPONSE="$(curl --fail-with-body --silent --show-error \
    --connect-timeout 15 --max-time 330 \
    -H "Authorization: Bearer $HERMES_STUDIO_AUTH_TOKEN" \
    -H 'Content-Type: application/json' \
    -X POST "${HERMES_STUDIO_URL%/}/api/chat-run/runs" \
    --data-binary "$PAYLOAD")"
  printf '%s' "$STUDIO_RESPONSE" | python3 -c '
import json, sys
payload = json.load(sys.stdin)
if payload.get("ok") is not True or str(payload.get("output") or "").strip() != "HERMES_ADS_CONTROL_OK":
    raise SystemExit("Hermes Studio /api/chat-run/runs did not complete the ads_control_status tool path")
events = payload.get("events") or []
if not any(str(event.get("event") or "").startswith("tool.") for event in events if isinstance(event, dict)):
    raise SystemExit("Hermes Studio response did not contain a tool execution event")
'
fi

echo "hermes-studio-integration: OK profile=$HERMES_PROFILE live=$LIVE studio_live=$STUDIO_LIVE"
