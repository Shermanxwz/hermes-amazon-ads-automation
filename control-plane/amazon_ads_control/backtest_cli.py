"""Installed CLI for deterministic historical/shadow replay."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .backtest import replay_cases
from .strategy import StrategyPolicy


def load_cases(path: Path):
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    value = json.loads(text)
    if isinstance(value, dict) and isinstance(value.get("cases"), list):
        return value["cases"]
    if isinstance(value, list):
        return value
    raise ValueError("input must be a JSON array, {cases:[...]}, or JSONL")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay historical/shadow Amazon Ads snapshots through the deterministic strategy engine")
    parser.add_argument("input", type=Path)
    parser.add_argument("--policy", type=Path, help="optional JSON StrategyPolicy overrides")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fail-on-label-mismatch", action="store_true")
    args = parser.parse_args(argv)
    try:
        policy = StrategyPolicy.from_mapping(json.loads(args.policy.read_text())) if args.policy else StrategyPolicy()
        report = replay_cases(load_cases(args.input), policy)
    except Exception as exc:
        print(f"backtest: {exc}", file=sys.stderr)
        return 2
    rendered = json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 1 if args.fail_on_label_mismatch and report["summary"]["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
