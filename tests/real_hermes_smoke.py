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
        os.environ["ADS_CONTROL_AGENT_TOKEN"] = "agent-" + "x" * 48
        os.environ.pop("ADS_CONTROL_OPERATOR_TOKEN", None)
        os.environ.pop("ADS_CONTROL_ENABLE_COMMAND_APPROVAL", None)
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
            "ads_control_sync_catalog",
            "ads_control_create_report_job",
            "ads_control_report_evidence",
            "ads_control_transition_report",
            "ads_control_plan_cycle",
            "ads_control_create_task",
            "ads_control_create_managed_plan",
            "ads_control_request_approval",
            "ads_control_status",
            "ads_control_record_note",
            "ads_control_prepare_write",
            "ads_control_read_evidence",
            "ads_control_verify_decision",
            "ads_control_finalize_task",
            "ads_control_ingest_stream_events",
        }
        expected_hooks = {
            "pre_llm_call", "post_llm_call", "pre_tool_call", "post_tool_call",
            "on_session_start", "on_session_end", "on_session_finalize", "on_session_reset",
            "subagent_start", "subagent_stop",
        }
        expected_commands = {"ads-approvals", "ads-approve", "ads-reject"}
        assert expected_tools.issubset(set(loaded.tools_registered)), loaded.tools_registered
        assert expected_hooks.issubset(set(loaded.hooks_registered)), loaded.hooks_registered
        assert expected_commands.issubset(set(loaded.commands_registered)), loaded.commands_registered
        assert "ADS_CONTROL_OPERATOR_TOKEN" not in os.environ
        assert "ADS_CONTROL_ENABLE_COMMAND_APPROVAL" not in os.environ
        for name in expected_tools:
            assert registry.get_schema(name), f"missing schema for {name}"
            entry = registry.get_entry(name)
            assert entry is not None, f"missing handler for {name}"
        skill = destination / "skill" / "SKILL.md"
        assert skill.is_file()
        text = skill.read_text(encoding="utf-8")
        assert "Approval-Gated Full Autopilot" in text
        assert "authenticated Amazon Ads Control Web" in text
        print(
            f"real-hermes-smoke: OK ({len(expected_tools)} tools, "
            f"{len(loaded.hooks_registered)} hooks, {len(loaded.commands_registered)} commands, "
            "Web-only approval default)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
