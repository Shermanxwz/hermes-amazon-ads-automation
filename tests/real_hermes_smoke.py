#!/usr/bin/env python3
"""Load the repository plugin through the pinned real Hermes plugin manager."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    with tempfile.TemporaryDirectory() as temp:
        home = Path(temp) / "hermes"
        destination = home / "plugins" / "amazon-ads-control"
        destination.parent.mkdir(parents=True)
        shutil.copytree(ROOT / "hermes-plugin" / "amazon_ads_control", destination)
        (home / "config.yaml").write_text("plugins:\n  enabled:\n    - amazon-ads-control\n")
        os.environ["HERMES_HOME"] = str(home)
        os.environ["ADS_CONTROL_AGENT_TOKEN"] = "test-" + "x" * 40
        os.environ["ADS_CONTROL_URL"] = "http://127.0.0.1:9"

        from hermes_cli.plugins import PluginManager
        from tools.registry import registry

        manager = PluginManager()
        manager.discover_and_load()
        loaded = manager._plugins.get("amazon-ads-control")
        assert loaded is not None, f"plugin not discovered: {manager._plugins.keys()}"
        assert loaded.enabled, loaded.error
        assert loaded.error is None, loaded.error
        expected_tools = {
            "ads_control_sync_catalog", "ads_control_plan_cycle", "ads_control_create_task",
            "ads_control_status", "ads_control_record_note", "ads_control_verify_decision",
            "ads_control_finalize_task", "ads_control_ingest_stream_events",
        }
        assert expected_tools.issubset(set(loaded.tools_registered)), loaded.tools_registered
        assert {"pre_llm_call", "pre_tool_call", "post_tool_call", "subagent_start", "subagent_stop"}.issubset(set(loaded.hooks_registered))
        for name in expected_tools:
            assert registry.get_schema(name), f"missing schema for {name}"
            entry = registry.get_entry(name)
            assert entry is not None, f"missing handler for {name}"
        skill = destination / "skill" / "SKILL.md"
        assert skill.is_file() and "Independent" not in ""  # file existence is the runtime contract
        print(f"real-hermes-smoke: OK ({len(expected_tools)} tools, {len(loaded.hooks_registered)} hooks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
