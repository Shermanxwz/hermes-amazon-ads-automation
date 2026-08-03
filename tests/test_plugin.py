from pathlib import Path
import importlib.util
import sys
import unittest
from unittest.mock import patch


def load_plugin():
    package_dir = Path(__file__).resolve().parents[1] / "hermes-plugin" / "amazon_ads_control"
    spec = importlib.util.spec_from_file_location(
        "hermes_ads_plugin",
        package_dir / "__init__.py",
        submodule_search_locations=[str(package_dir)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader
    spec.loader.exec_module(module)
    return module


class PluginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plugin = load_plugin()

    def test_main_write_blocked_from_hook(self):
        with patch.object(self.plugin.client, "request", return_value={"allowed": False, "reason": "worker-only"}):
            result = self.plugin.pre_tool_call("amazon_ads_update_bid", {}, task_id="main")
            self.assertEqual(result["action"], "block")

    def test_read_allowed_from_hook(self):
        with patch.object(self.plugin.client, "request", return_value={"allowed": True}):
            self.assertIsNone(self.plugin.pre_tool_call("amazon_ads_query_campaign", {}, task_id="main"))

    def test_control_plane_failure_allows_ads_read_only(self):
        with patch.object(self.plugin.client, "request", return_value={"error": "down"}):
            self.assertIsNone(self.plugin.pre_tool_call("campaign_management-query_campaign", {}, task_id="main"))
            blocked = self.plugin.pre_tool_call("campaign_management-update_campaign", {}, task_id="worker")
            self.assertEqual(blocked["action"], "block")

    def test_unrelated_tools_do_not_depend_on_control_plane(self):
        with patch.object(self.plugin.client, "request", side_effect=AssertionError("should not call")):
            self.assertIsNone(self.plugin.pre_tool_call("terminal", {}, task_id="main"))

    def test_subagent_marker_binding(self):
        calls = []
        with patch.object(self.plugin.client, "request", side_effect=lambda m, p, d=None: calls.append((m, p, d)) or {"ok": True}):
            self.plugin.subagent_start("parent", "child", "sub", "leaf", "do it [ads-task:abcdef1234567890]")
        self.assertEqual(calls[0][1], "/api/agent/worker-bind")
        self.assertEqual(calls[0][2]["task_id"], "abcdef1234567890")


    def test_register_uses_hermes_handler_contract_and_skill_file(self):
        class FakeContext:
            def __init__(self):
                self.tools = {}
                self.hooks = {}
                self.skills = {}
            def register_tool(self, *, name, toolset, schema, handler):
                self.tools[name] = handler
            def register_hook(self, event, handler):
                self.hooks[event] = handler
            def register_skill(self, *, name, path):
                self.skills[name] = Path(path)

        ctx = FakeContext()
        self.plugin.register(ctx)
        self.assertTrue(ctx.skills["amazon-ads-autopilot"].name == "SKILL.md")
        self.assertTrue(ctx.skills["amazon-ads-autopilot"].is_file())
        with patch.object(self.plugin.client, "request", return_value={"id": "task-1"}) as request:
            result = ctx.tools["ads_control_create_task"](
                {"title": "audit", "kind": "audit", "objective": "inspect"},
                session_id="main-session",
            )
        self.assertIn("task-1", result)
        self.assertEqual(request.call_args.args[2]["parent_session_id"], "main-session")

    def test_handler_rejects_non_object_arguments(self):
        class FakeContext:
            def __init__(self): self.tools = {}
            def register_tool(self, *, name, toolset, schema, handler): self.tools[name] = handler
            def register_hook(self, *args, **kwargs): pass
            def register_skill(self, *args, **kwargs): pass
        ctx = FakeContext()
        self.plugin.register(ctx)
        with self.assertRaises(ValueError):
            ctx.tools["ads_control_status"]([])

    def test_unbound_subagent_logged(self):
        calls = []
        with patch.object(self.plugin.client, "request", side_effect=lambda m, p, d=None: calls.append((m, p, d)) or {"ok": True}):
            self.plugin.subagent_start("parent", "child", "sub", "leaf", "generic task")
        self.assertEqual(calls[0][1], "/api/agent/events")


if __name__ == "__main__":
    unittest.main()
