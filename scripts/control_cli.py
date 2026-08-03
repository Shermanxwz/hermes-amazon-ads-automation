#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control-plane"))
from amazon_ads_control.security import hash_password


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("generate-token")
    sub.add_parser("hash-password")
    args = parser.parse_args()
    if args.command == "generate-token":
        print(secrets.token_urlsafe(48))
    else:
        password = getpass.getpass("Control password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            raise SystemExit("passwords do not match")
        print(hash_password(password))


if __name__ == "__main__":
    main()
