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
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

env_path = Path(sys.argv[1])
backup_dir = Path(sys.argv[2])
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

updated = []
replaced = False
for line in lines:
    if line.startswith("AMAZON_ADS_MCP_ACCESS_TOKEN="):
        updated.append("AMAZON_ADS_MCP_ACCESS_TOKEN=" + access + "\n")
        replaced = True
    else:
        updated.append(line)
if not replaced:
    updated.append("AMAZON_ADS_MCP_ACCESS_TOKEN=" + access + "\n")

backup_dir.mkdir(parents=True, exist_ok=True)
runtime_path = Path("/var/lib/hermes-amazon-ads-control/runtime.env")
fd, temporary = tempfile.mkstemp(prefix=".runtime.env.", dir=str(runtime_path.parent))
os.close(fd)
os.chmod(temporary, 0o600)
Path(temporary).write_text("AMAZON_ADS_MCP_ACCESS_TOKEN=" + access + "\n", encoding="utf-8")
os.replace(temporary, runtime_path)
os.chmod(runtime_path, 0o600)

stamp = time.strftime("%Y%m%dT%H%M%S%z")
backup = backup_dir / (env_path.name + ".pre-refresh-" + stamp)
print("amazon_token_refresh=ok", "access_len=" + str(len(access)), "access_sha256_prefix=" + hashlib.sha256(access.encode()).hexdigest()[:16], "runtime_file=" + str(runtime_path), "credentials_backup_not_needed=true")
PY
