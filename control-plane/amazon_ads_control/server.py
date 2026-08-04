from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from typing import Sequence

from . import __version__
from .api import build_server
from .api_extension import install as install_api_extension
from .config import Settings
from .db import Store

install_api_extension()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="amazon-ads-control",
        description="Hermes-native deterministic Amazon Ads closed-loop control plane",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--check", action="store_true", help="validate configuration and database integrity, then exit")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = Settings.from_env()
        settings.validate_runtime()
    except ValueError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    store = Store(settings.db_path)
    expired = store.reconcile_expired_reservations()
    integrity = store.integrity_check(quick=False if args.check else True)
    if not integrity["ok"]:
        print(json.dumps({"ok": False, "database": integrity}, ensure_ascii=False), file=sys.stderr)
        return 3
    if args.check:
        print(json.dumps({
            "ok": True,
            "version": __version__,
            "database": integrity,
            "expired_reservations_quarantined": len(expired),
            "report_counts": store.dashboard().get("reports", {}).get("counts", {}),
            "host": settings.host,
            "port": settings.port,
        }, ensure_ascii=False))
        return 0

    deleted = store.purge_old(settings.retention_days)
    store.event(
        "info", "controller.started", "controller", None,
        "Amazon Ads closed-loop control plane started",
        {"retention_purge": deleted, "expired_reservations_quarantined": len(expired)},
    )
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
