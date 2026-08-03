from __future__ import annotations

import signal
import sys
import threading

from .api import build_server
from .config import Settings
from .db import Store


def main() -> int:
    try:
        settings = Settings.from_env()
        settings.validate_runtime()
    except ValueError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    store = Store(settings.db_path)
    deleted = store.purge_old(settings.retention_days)
    store.event("info", "controller.started", "controller", None, "Amazon Ads control plane started", {"retention_purge": deleted})
    server = build_server(settings, store)

    def stop(*_args):
        store.event("info", "controller.stopping", "controller", None, "Amazon Ads control plane stopping", {})
        threading.Thread(target=server.shutdown, name="control-plane-shutdown", daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"Hermes Amazon Ads control plane listening on http://{settings.host}:{settings.port}")
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
