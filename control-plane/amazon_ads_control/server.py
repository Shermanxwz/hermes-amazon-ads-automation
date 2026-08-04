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
            "storage": store.storage_status(),
            "expired_reservations_quarantined": len(expired),
            "report_counts": store.dashboard().get("reports", {}).get("counts", {}),
            "host": settings.host,
            "port": settings.port,
        }, ensure_ascii=False))
        return 0

    try:
        maintenance = store.maintain_storage(settings)
    except Exception as exc:
        maintenance = {"error": str(exc)}
        store.alert_once(
            "critical", "STORAGE_MAINTENANCE_FAILED", None, None, None,
            "Initial storage maintenance failed; autonomous execution remains fail-closed on hard pressure",
            {"error": str(exc)}, window_seconds=86400,
        )
    store.event(
        "info", "controller.started", "controller", None,
        "Amazon Ads closed-loop control plane started",
        {"storage_maintenance": maintenance, "expired_reservations_quarantined": len(expired)},
    )
    server = build_server(settings, store)
    maintenance_stop = threading.Event()

    def maintenance_loop() -> None:
        while not maintenance_stop.wait(settings.maintenance_interval_seconds):
            try:
                store.maintain_storage(settings)
            except Exception as exc:
                store.alert_once(
                    "critical", "STORAGE_MAINTENANCE_FAILED", None, None, None,
                    "Periodic storage maintenance failed",
                    {"error": str(exc)}, window_seconds=86400,
                )

    maintenance_thread = threading.Thread(
        target=maintenance_loop,
        name="storage-maintenance",
        daemon=True,
    )
    maintenance_thread.start()

    def stop(*_args):
        maintenance_stop.set()
        store.event("info", "controller.stopping", "controller", None, "Amazon Ads control plane stopping", {})
        threading.Thread(target=server.shutdown, name="control-plane-shutdown", daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"Hermes Amazon Ads control plane listening on http://{settings.host}:{settings.port}")
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        maintenance_stop.set()
        maintenance_thread.join(timeout=2)
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
