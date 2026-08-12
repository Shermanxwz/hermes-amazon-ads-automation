#!/usr/bin/env bash
set -euo pipefail

ENV_FILE=/etc/hermes-amazon-ads-control.env
RUNTIME_FILE=/var/lib/hermes-amazon-ads-control/runtime.env
BACKUP_DIR=/var/lib/hermes-amazon-ads-control/backups
LOCK=/run/lock/hermes-amazon-ads-token-refresh.lock
mkdir -p "$BACKUP_DIR"
exec 8>"$LOCK"
flock -n 8 || exit 0

/usr/bin/python3 - "$ENV_FILE" "$BACKUP_DIR" <<'PY'
import json
import os
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

env_path = Path(sys.argv[1])
lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
values = {}
for line in lines:
    if "=" in line and not line.lstrip().startswith("#"):
        key, value = line.rstrip("\n").split("=", 1)
        values[key] = value
required = ("AMAZON_ADS_CLIENT_ID", "AMAZON_ADS_CLIENT_SECRET", "AMAZON_ADS_REFRESH_TOKEN")
missing = [key for key in required if not values.get(key)]
if missing:
    raise SystemExit("missing OAuth fields: " + ",".join(missing))

body = urllib.parse.urlencode({
    "grant_type": "refresh_token",
    "refresh_token": values["AMAZON_ADS_REFRESH_TOKEN"],
    "client_id": values["AMAZON_ADS_CLIENT_ID"],
    "client_secret": values["AMAZON_ADS_CLIENT_SECRET"],
}).encode()
request = urllib.request.Request(
    "https://api.amazon.com/auth/o2/token",
    data=body,
    method="POST",
    headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
)
with urllib.request.urlopen(request, timeout=30) as response:
    payload = json.loads(response.read().decode())
access = str(payload.get("access_token") or "").strip()
if not access:
    raise SystemExit("Amazon OAuth refresh returned no access_token")

runtime_path = Path("/var/lib/hermes-amazon-ads-control/runtime.env")
runtime_path.parent.mkdir(parents=True, exist_ok=True)
fd, temporary = tempfile.mkstemp(prefix=".runtime.env.", dir=str(runtime_path.parent))
os.close(fd)
os.chmod(temporary, 0o600)
Path(temporary).write_text("AMAZON_ADS_MCP_ACCESS_TOKEN=" + access + "\n", encoding="utf-8")
os.replace(temporary, runtime_path)
os.chmod(runtime_path, 0o600)
print("amazon_token_refresh=ok runtime_credentials_updated=true")
PY
