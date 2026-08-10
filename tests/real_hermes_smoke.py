#!/usr/bin/env python3
"""Load the repository plugin through real Hermes and a live control plane."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
import threading

from amazon_ads_control.api import build_server
from amazon_ads_control.config import Settings
from amazon_ads_control.db import Store
from amazon_ads_control.security import hash_password

ROOT = Path(__file__).resolve().parents[1]


def _entry_handler(entry):
    candidates = [entry]
    if isinstance(entry, dict):
        candidates.extend(entry.get(key) for key in ("function", "func", "handler", "callable"))
    else:
        candidates.extend(getattr(entry, key, None) for key in ("function", "func", "handler", "callable"))
    for candidate in candidates:
        if callable(candidate):
            return candidate
    raise AssertionError(f"Hermes registry entry has no callable handler: {entry!r}")


def main() -> int:
    with tempfile.TemporaryDirectory() as temp:
        temp_path = Path(temp)
        settings = Settings(
            host="127.0.0.1", port=0, db_path=temp_path / "state.db", public_origin="",
            control_password_hash=hash_password("correct horse battery staple"),
            agent_token="agent-" + "x" * 48,
            session_ttl_seconds=3600, max_sessions=8, retention_days=30,
            allow_remote_bind=False,
        )
        store = Store(settings.db_path)
        server = build_server(settings, store)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        home = temp_path / "hermes"
        destination = home / "plugins" / "amazon-ads-control"
        destination.parent.mkdir(parents=True)
        shutil.copytree(ROOT / "hermes-plugin" / "amazon_ads_control", destination)
        (home / "config.yaml").write_text("plugins:\n  enabled:\n    - amazon-ads-control\n")
        os.environ["HERMES_HOME"] = str(home)
        os.environ["ADS_CONTROL_AGENT_TOKEN"] = settings.agent_token
        os.environ.pop("ADS_CONTROL_OPERATOR_TOKEN", None)
        os.environ.pop("ADS_CONTROL_ENABLE_COMMAND_APPROVAL", None)
        os.environ["ADS_CONTROL_URL"] = f"http://127.0.0.1:{server.server_address[1]}"
        os.environ["ADS_MCP_DEFAULT_REGION"] = "na"
        os.environ["ADS_MCP_TOOLSETS"] = "mcp-amazon-ads"

        try:
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

            status_handler = _entry_handler(registry.get_entry("ads_control_status"))
            status = json.loads(status_handler({"session_id":"real-hermes-control"}))
            assert status.get("error") is None, status
            assert status["role"] == "main", status
            assert status["mode"] == "observe", status
            assert "budget_guard" in status, status

            note_handler = _entry_handler(registry.get_entry("ads_control_record_note"))
            note = json.loads(note_handler({
                "message":"real Hermes to live control plane smoke",
                "level":"info",
                "session_id":"real-hermes-control",
            }))
            assert note.get("id"), note
            assert any(
                event.get("message") == "real Hermes to live control plane smoke"
                for event in store.list_events(20)
            )

            fake_tool = "mcp_amazon_ads_campaign_management_query_campaigns"
            original_toolsets = registry.get_registered_toolset_names
            original_names = registry.get_tool_names_for_toolset
            original_schema = registry.get_schema
            registry.get_registered_toolset_names = lambda: sorted(set(original_toolsets()) | {"mcp-amazon-ads"})
            registry.get_tool_names_for_toolset = lambda name: [fake_tool] if name == "mcp-amazon-ads" else original_names(name)
            registry.get_schema = lambda name: ({
                "description": "Query campaigns",
                "parameters": {"type": "object", "properties": {}},
            } if name == fake_tool else original_schema(name))
            try:
                sync_handler = _entry_handler(registry.get_entry("ads_control_sync_catalog"))
                synced = json.loads(sync_handler({"session_id":"real-hermes-control"}))
            finally:
                registry.get_registered_toolset_names = original_toolsets
                registry.get_tool_names_for_toolset = original_names
                registry.get_schema = original_schema
            assert synced.get("error") is None, synced
            assert synced.get("tool_count") == 1, synced
            assert store.get_tool(fake_tool) is not None

            skill = destination / "skill" / "SKILL.md"
            assert skill.is_file()
            text = skill.read_text(encoding="utf-8")
            assert "Budget-Bounded Full-Managed ACOS Autopilot v6.1" in text
            assert "budget-bounded autonomy" in text.lower()
            assert "HERMES-SP-EXP-*" in text
            assert "different read-only Verifier" in text
            assert "authenticated Amazon Ads Control Web" in text
            print(
                f"real-hermes-smoke: OK ({len(expected_tools)} tools, "
                f"{len(loaded.hooks_registered)} hooks, {len(loaded.commands_registered)} commands, "
                "live control context/note/catalog, budget-bounded sealed autonomy)"
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
