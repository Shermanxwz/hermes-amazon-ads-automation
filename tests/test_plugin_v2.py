import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]; PLUGIN=ROOT/"hermes-plugin"/"amazon_ads_control"


def load_plugin():
    name="amazon_ads_plugin_v2"
    for key in [k for k in sys.modules if k==name or k.startswith(name+".")]: sys.modules.pop(key,None)
    spec=importlib.util.spec_from_file_location(name,PLUGIN/"__init__.py",submodule_search_locations=[str(PLUGIN)])
    mod=importlib.util.module_from_spec(spec); sys.modules[name]=mod; spec.loader.exec_module(mod); return mod


class FakeRegistry:
    def get_tool_names_for_toolset(self,name):
        return ["mcp_amazon_ads_campaign_management_query_campaign","mcp_amazon_ads_campaign_management_update_target"] if name=="mcp-amazon-ads" else []
    def get_schema(self,name): return {"description":"Update target" if "update" in name else "Query campaign","parameters":{"type":"object"}}


class FakeCtx:
    def __init__(self): self.tools={}; self.hooks={}; self.skills={}
    def register_tool(self,name,toolset,schema,handler): self.tools[name]=handler
    def register_hook(self,name,hook): self.hooks[name]=hook
    def register_skill(self,name,path): self.skills[name]=path


class PluginV2Tests(unittest.TestCase):
    def setUp(self):
        tools_pkg=types.ModuleType("tools"); registry_mod=types.ModuleType("tools.registry"); registry_mod.registry=FakeRegistry(); tools_pkg.registry=registry_mod
        self.modules=patch.dict(sys.modules,{"tools":tools_pkg,"tools.registry":registry_mod}); self.modules.start(); self.plugin=load_plugin()
    def tearDown(self): self.modules.stop()

    def test_sync_uses_exact_hermes_registry(self):
        calls=[]
        with patch.object(self.plugin.client,"request",side_effect=lambda m,p,payload=None,timeout=4: calls.append((m,p,payload)) or {"tool_count":2}):
            result=self.plugin.sync_live_catalog(force=True)
        self.assertEqual(result["tool_count"],2); self.assertEqual(calls[0][1],"/api/agent/catalog-sync")
        names=[x["registered_name"] for x in calls[0][2]["tools"]]; self.assertTrue(all(n.startswith("mcp_amazon_ads_") for n in names))

    def test_pre_and_post_preserve_authorization(self):
        responses=[]
        def request(method,path,payload=None,timeout=4):
            if path=="/api/agent/catalog-sync": return {"tool_count":2}
            if path=="/api/agent/tool-check": return {"allowed":True,"task_id":"t","decision_id":"d","plan_key":"p","reservation_token":"r"}
            if path=="/api/agent/tool-result": responses.append(payload); return {"recorded":True}
            return {}
        with patch.object(self.plugin.client,"request",side_effect=request):
            self.assertIsNone(self.plugin.pre_tool_call("mcp_amazon_ads_campaign_management_update_target",{"targetId":"1"},session_id="s",tool_call_id="call"))
            self.plugin.post_tool_call("mcp_amazon_ads_campaign_management_update_target",{"targetId":"1"},{"success":[{}]},session_id="s",tool_call_id="call")
        self.assertEqual(responses[0]["decision_id"],"d"); self.assertEqual(responses[0]["reservation_token"],"r")

    def test_denied_and_unavailable_fail_closed(self):
        def denied(method,path,payload=None,timeout=4):
            if path=="/api/agent/catalog-sync": return {"tool_count":2}
            return {"allowed":False,"reason":"main blocked"}
        with patch.object(self.plugin.client,"request",side_effect=denied):
            result=self.plugin.pre_tool_call("mcp_amazon_ads_campaign_management_update_target",{},session_id="s")
        self.assertEqual(result["action"],"block")
        self.plugin._CATALOG={}; self.plugin._CATALOG_SYNCED_AT=0
        with patch.object(self.plugin,"_registry_catalog",side_effect=RuntimeError("no registry")):
            result=self.plugin.pre_tool_call("mcp_amazon_ads_campaign_management_query_campaign",{},session_id="s")
        self.assertEqual(result["action"],"block")

    def test_unrelated_tools_untouched(self):
        with patch.object(self.plugin.client,"request") as req:
            self.assertIsNone(self.plugin.pre_tool_call("web_search",{},session_id="s")); req.assert_not_called()

    def test_subagent_requires_task_and_role(self):
        calls=[]
        with patch.object(self.plugin.client,"request",side_effect=lambda m,p,payload=None,timeout=4: calls.append((p,payload)) or {}):
            self.plugin.subagent_start("parent","child","sub","leaf","[ads-task:abcdef12] [ads-role:verifier]")
        self.assertEqual(calls[0][0],"/api/agent/worker-bind"); self.assertEqual(calls[0][1]["role"],"verifier")

    def test_register_contract(self):
        ctx=FakeCtx(); self.plugin.register(ctx)
        self.assertEqual(len(ctx.tools),8); self.assertIn("pre_tool_call",ctx.hooks); self.assertTrue(ctx.skills["amazon-ads-autopilot"].name=="SKILL.md")
        with self.assertRaises(ValueError): ctx.tools["ads_control_status"]("bad")
