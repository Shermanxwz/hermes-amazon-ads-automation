#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import json
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control-plane"))

from amazon_ads_control import __version__
from amazon_ads_control.config import Settings
from amazon_ads_control.db import Store
from amazon_ads_control.security import hash_password


def _database_path(value: str | None) -> Path:
    if value:
        return Path(value)
    return Settings.from_env().db_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="amazon-ads-control-cli",
        description="Administrative utilities for Hermes Amazon Ads Control",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("generate-token", help="generate a cryptographically random agent token")
    sub.add_parser("hash-password", help="interactively create a dashboard password hash")

    verify = sub.add_parser("verify-database", help="run SQLite integrity and foreign-key checks")
    verify.add_argument("--database", help="database path; defaults to ADS_CONTROL_DB")
    verify.add_argument("--full", action="store_true", help="use the full integrity_check pragma")

    backup = sub.add_parser("backup", help="create an atomic, integrity-checked SQLite backup")
    backup.add_argument("--database", help="database path; defaults to ADS_CONTROL_DB")
    backup.add_argument("--output", required=True, help="backup output path")

    storage = sub.add_parser("storage-status", help="show database, WAL, free-space and row-count pressure")
    storage.add_argument("--database", help="database path; defaults to ADS_CONTROL_DB")

    maintain = sub.add_parser("maintain-storage", help="run retention, compaction, checkpoint and safe reclaim now")
    maintain.add_argument("--database", help="database path; defaults to ADS_CONTROL_DB")

    doctor = sub.add_parser("doctor", help="validate runtime configuration and database health")
    doctor.add_argument("--full", action="store_true", help="use the full integrity_check pragma")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "generate-token":
        print(secrets.token_urlsafe(48))
        return 0
    if args.command == "hash-password":
        password = getpass.getpass("Control password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("passwords do not match", file=sys.stderr)
            return 2
        try:
            print(hash_password(password))
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        return 0
    if args.command == "verify-database":
        result = Store(_database_path(args.database)).integrity_check(quick=not args.full)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ok"] else 3
    if args.command == "backup":
        result = Store(_database_path(args.database)).backup_to(Path(args.output))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "storage-status":
        result = Store(_database_path(args.database)).storage_status()
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "maintain-storage":
        settings = Settings.from_env()
        result = Store(_database_path(args.database)).maintain_storage(settings)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if not result.get("error") else 3
    if args.command == "doctor":
        try:
            settings = Settings.from_env()
            settings.validate_runtime()
            store = Store(settings.db_path)
            expired = store.reconcile_expired_reservations()
            result = store.integrity_check(quick=not args.full)
        except (ValueError, OSError, RuntimeError) as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 2
        output = {
            "ok": result["ok"], "version": __version__, "database": result,
            "storage": store.storage_status(),
            "expired_reservations_quarantined": len(expired),
            "bind": f"{settings.host}:{settings.port}", "public_origin": settings.public_origin,
        }
        print(json.dumps(output, ensure_ascii=False))
        return 0 if result["ok"] else 3
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
