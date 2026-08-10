import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]; PLUGIN=ROOT/"hermes-plugin"/"amazon_ads_control"


def load_plugin():
    name="amazon_ads_plugin_v3"
    for key in [k for k in sys.modules if k==name or k.startswith(name+".")]: sys.modules.pop(key,None)
    spec=importlib.util.spec_from_file_location(name,PLUGIN/"__init__.py",submodule_search_locations=[str(PLUGIN)])
    mod=importlib.util.module_from_spec(spec); sys.modules[name]=mod; spec.loader.exec_module(mod); return mod


class FakeRegistry:
    def get_tool_names_for_toolset(self,name): return ["mcp_amazon_ads_campaign_management_query_campaign","mcp_amazon_ads_campaign_management_update_target"] if name=="mcp-amazon-ads" else []
    def get_schema(self,name): return {"description":"Update target" if "update" in name else "Query campaign","parameters":{"type":"object"}}


class FakeCtx:
    def __init__(self): self.tools={}; self.hooks={}; self.skills={}; self.commands={}
    def register_tool(self,name,toolset,schema,handler,description=None): self.tools[name]=handler
    def register_hook(self,name,hook): self.hooks[name]=hook
    def register_skill(self,name,path): self.skills[name]=path
    def register_command(self,name,handler,description=None): self.commands[name]=handler


class PluginV3Tests(unittest.TestCase):
    def setUp(self):
        tools_pkg=types.ModuleType("tools"); registry_mod=types.ModuleType("tools.registry"); registry_mod.registry=FakeRegistry(); tools_pkg.registry=registry_mod
        self.modules=patch.dict(sys.modules,{"tools":tools_pkg,"tools.registry":registry_mod}); self.modules.start(); self.plugin=load_plugin()
    def tearDown(self): self.modules.stop()

    def test_sync_sends_only_raw_registry_contract(self):
        calls=[]
        with patch.object(self.plugin.client,"request",side_effect=lambda m,p,payload=None,timeout=4: calls.append((m,p,payload)) or {"tool_count":2}): result=self.plugin.sync_live_catalog(force=True)
        self.assertEqual(result["tool_count"],2); self.assertEqual(calls[0][1],"/api/agent/catalog-sync")
        for item in calls[0][2]["tools"]:
            self.assertTrue(item["registered_name"].startswith("mcp_amazon_ads_")); self.assertNotIn("risk",item); self.assertNotIn("semantic",item)

    def test_sync_falls_back_to_live_registry_map_when_toolset_index_is_empty(self):
        class MapOnlyRegistry:
            def get_registered_toolset_names(self): return ["mcp-amazon-ads"]
            def get_tool_names_for_toolset(self, name): return []
            def get_tool_to_toolset_map(self):
                return {"mcp_amazon_ads_campaign_management_query_campaign": "mcp-amazon-ads"}
            def get_schema(self, name): return {"description": "Query", "parameters": {"type": "object"}}
        sys.modules["tools.registry"].registry = MapOnlyRegistry()
        with patch.object(self.plugin.client,"request",return_value={"tool_count":1}) as request:
            result=self.plugin.sync_live_catalog(force=True)
        self.assertEqual(result["tool_count"],1)
        self.assertEqual(request.call_args.args[1],"/api/agent/catalog-sync")

        responses=[]
        def request(method,path,payload=None,timeout=4):
            if path=="/api/agent/catalog-sync": return {"tool_count":2}
            if path=="/api/agent/tool-check": return {"allowed":True,"task_id":"t","decision_id":"d","plan_key":"p","reservation_token":"r"}
            if path=="/api/agent/tool-result": responses.append(payload); return {"recorded":True}
            return {}
        with patch.object(self.plugin.client,"request",side_effect=request):
            self.assertIsNone(self.plugin.pre_tool_call("mcp_amazon_ads_campaign_management_update_target",{"targetId":"1"},session_id="s",tool_call_id="call"))
            delivered=self.plugin.post_tool_call("mcp_amazon_ads_campaign_management_update_target",{"targetId":"1"},{"success":[{}]},session_id="s",tool_call_id="call")
        self.assertEqual(responses[0]["decision_id"],"d"); self.assertEqual(responses[0]["reservation_token"],"r"); self.assertTrue(responses[0]["event_id"]); self.assertFalse(delivered["queued"])

    def test_denied_and_unavailable_fail_closed(self):
        def denied(method,path,payload=None,timeout=4): return {"tool_count":2} if path=="/api/agent/catalog-sync" else {"allowed":False,"reason":"main blocked"}
        with patch.object(self.plugin.client,"request",side_effect=denied): result=self.plugin.pre_tool_call("mcp_amazon_ads_campaign_management_update_target",{},session_id="s")
        self.assertEqual(result["action"],"block")
        self.plugin._CATALOG={}; self.plugin._CATALOG_SYNCED_AT=0
        with patch.object(self.plugin,"_registry_catalog",side_effect=RuntimeError("no registry")): result=self.plugin.pre_tool_call("mcp_amazon_ads_campaign_management_query_campaign",{},session_id="s")
        self.assertEqual(result["action"],"block")

    def test_unrelated_tools_untouched(self):
        with patch.object(self.plugin.client,"request") as req: self.assertIsNone(self.plugin.pre_tool_call("web_search",{},session_id="s")); req.assert_not_called()

    def test_subagent_requires_task_and_role(self):
        calls=[]
        with patch.object(self.plugin.client,"request",side_effect=lambda m,p,payload=None,timeout=4: calls.append((p,payload)) or {}): self.plugin.subagent_start("parent","child","sub","leaf","[ads-task:abcdef12] [ads-role:verifier]")
        self.assertEqual(calls[0][0],"/api/agent/worker-bind"); self.assertEqual(calls[0][1]["role"],"verifier")

    def test_register_contract(self):
        ctx=FakeCtx(); self.plugin.register(ctx)
        self.assertEqual(len(ctx.tools),15)
        self.assertEqual(len(ctx.hooks),10)
        self.assertEqual(set(ctx.commands),{"ads-approvals","ads-approve","ads-reject"})
        self.assertIn("ads_control_create_managed_plan",ctx.tools); self.assertIn("ads_control_request_approval",ctx.tools)
        self.assertIn("ads_control_create_report_job",ctx.tools); self.assertIn("ads_control_prepare_write",ctx.tools)
        self.assertIn("pre_tool_call",ctx.hooks); self.assertIn("subagent_start",ctx.hooks); self.assertEqual(ctx.skills["amazon-ads-autopilot"].name,"SKILL.md")
        with self.assertRaises(ValueError): ctx.tools["ads_control_status"]("bad")

    def test_pre_llm_context_and_web_only_approval_message(self):
        state={
            "role":"main","mode":"observe","execution_enabled":False,"catalog":{"tools":2},
            "reports":{"counts":{}},"task":None,"decisions":[],"instructions":"read only",
            "approvals":{"pending":[{"id":"ap1","summary":"create campaign","risk":"high","decision_ids":["d1"],"payload_hash":"a"*64,"expires_at":"2030-01-01T00:00:00+00:00"}]},
        }
        with patch.object(self.plugin,"sync_live_catalog",return_value={"tool_count":2}), patch.object(self.plugin.client,"request",return_value={"ok":True}), patch.object(self.plugin.client,"context",return_value=state), patch.object(self.plugin.client,"COMMAND_APPROVAL_ENABLED",False):
            result=self.plugin.pre_llm_call(session_id="main")
        self.assertIn("Amazon Ads Control v3.2",result["context"]); self.assertIn("read only",result["context"]); self.assertIn("authenticated-control-web-only",result["context"]); self.assertNotIn("/ads-approve ap1",result["context"])
        with patch.object(self.plugin,"sync_live_catalog",return_value={"error":"x"}), patch.object(self.plugin.client,"request",return_value={"error":"down"}), patch.object(self.plugin.client,"context",return_value={"error":"down"}): result=self.plugin.pre_llm_call(session_id="main")
        self.assertIn("禁止调用",result["context"])

    def test_commands_are_registered_but_mutations_disabled_by_default(self):
        pending=[{"id":"ap1","risk":"high","summary":"campaign","decision_ids":["d"],"payload_hash":"b"*64,"expires_at":"x"}]
        with patch.object(self.plugin,"_pending_approvals",return_value=pending), patch.object(self.plugin.client,"COMMAND_APPROVAL_ENABLED",False):
            listing=self.plugin._approvals_command(); approval=self.plugin._approve_command("ap1 "+"b"*12); rejection=self.plugin._reject_command("ap1 no")
        self.assertIn("Web",listing); self.assertIn("默认关闭",approval); self.assertIn("默认关闭",rejection)

    def test_session_fallback_telemetry_is_forwarded(self):
        calls=[]
        with patch.object(self.plugin.client,"request",side_effect=lambda m,p,payload=None,timeout=4: calls.append((p,payload)) or {}):
            self.plugin.post_llm_call(session_id="s",model="m",provider="p",used_fallback=True)
        self.assertEqual(calls[0][0],"/api/agent/session-event"); self.assertTrue(calls[0][1]["fallback"])

    def test_unbound_and_stopped_subagents_are_audited(self):
        calls=[]
        with patch.object(self.plugin.client,"request",side_effect=lambda m,p,payload=None,timeout=4: calls.append((p,payload)) or {}):
            self.plugin.subagent_start("parent","child","sub","leaf","missing markers"); self.plugin.subagent_stop("parent","completed","done",10,child_session_id="child")
        self.assertEqual(calls[0][0],"/api/agent/events"); self.assertEqual(calls[1][0],"/api/agent/worker-stop")

    def test_tool_wrappers_preserve_lineage_sessions_and_payloads(self):
        calls=[]
        def request(method,path,payload=None,timeout=4): calls.append((method,path,payload,timeout)); return {"ok":True}
        with patch.object(self.plugin.client,"request",side_effect=request), patch.object(self.plugin.client,"context",return_value={"role":"main"}):
            self.plugin.tools.create_report_job({"profile_id":"p"}); self.plugin.tools.report_evidence(10,session_id="s"); self.plugin.tools.transition_report("job","SUBMITTED",11,{"report_id":"r"},session_id="s")
            self.assertIn('"ok": true',self.plugin.tools.plan_cycle({"profile":{}},{"report_job_ids":["j"]},{"target_acos":30},session_id="s"))
            self.plugin.tools.create_task("cycle",7,turn_id="turn")
            self.plugin.tools.create_managed_plan("plan",{"profile_id":"p"},[{"tool_name":"x","action_type":"x","arguments":{},"expected_state":{"id":"1"}}],session_id="s")
            self.plugin.tools.request_approval("task","summary",30,session_id="s")
            self.assertIn("main",self.plugin.tools.status(session_id="s")); self.plugin.tools.record_note("note",task_id="task",session_id="s")
            self.plugin.tools.prepare_write("d",11,session_id="e"); self.plugin.tools.read_evidence("d",5,session_id="v"); self.plugin.tools.verify_decision("d",12,"ok",session_id="v"); self.plugin.tools.finalize_task("task","done",session_id="s"); self.plugin.tools.ingest_stream_events([{"id":1}],session_id="s")
        paths=[item[1] for item in calls]
        self.assertEqual(paths,["/api/agent/reports","/api/agent/report-evidence","/api/agent/reports/transition","/api/agent/cycles/plan","/api/agent/tasks","/api/agent/managed-plans","/api/agent/approvals/request","/api/agent/events","/api/agent/prepare-write","/api/agent/read-evidence","/api/agent/verify","/api/agent/task-finalize","/api/agent/stream-events"])
        self.assertEqual(calls[2][2]["evidence_action_id"],11); self.assertEqual(calls[2][2]["session_id"],"s")
        self.assertEqual(calls[3][2]["parent_session_id"],"s"); self.assertEqual(calls[4][2]["parent_session_id"],"turn")
        self.assertEqual(calls[5][2]["parent_session_id"],"s"); self.assertEqual(calls[6][2]["session_id"],"s")


class PluginClientTests(unittest.TestCase):
    def setUp(self):
        tools_pkg=types.ModuleType("tools"); registry_mod=types.ModuleType("tools.registry"); registry_mod.registry=FakeRegistry(); tools_pkg.registry=registry_mod
        self.modules=patch.dict(sys.modules,{"tools":tools_pkg,"tools.registry":registry_mod}); self.modules.start(); self.plugin=load_plugin()
    def tearDown(self): self.modules.stop()
    def test_decode_request_and_context_failures(self):
        from io import BytesIO
        from urllib.error import HTTPError, URLError
        class Response:
            status=200
            def __init__(self,body): self.body=body
            def __enter__(self): return self
            def __exit__(self,*_): return False
            def read(self): return self.body
        client=self.plugin.client
        self.assertEqual(client._decode(b"[]")["error"],"invalid_control_response"); self.assertEqual(client._decode(b"bad")["error"],"invalid_control_response")
        with patch.object(client,"urlopen",return_value=Response(b'{"ok":true}')): self.assertTrue(client.request("GET","/ok")["ok"])
        with patch.object(client,"urlopen",side_effect=HTTPError("u",403,"bad",{},BytesIO(b'{"error":"blocked"}'))): self.assertEqual(client.request("GET","/x")["http_status"],403)
        with patch.object(client,"urlopen",side_effect=URLError("down")): self.assertEqual(client.context("session with space")["error"],"control_plane_unavailable")
        with patch.object(client,"COMMAND_APPROVAL_ENABLED",False): self.assertEqual(client.operator_request("POST","/x")["error"],"command_approval_disabled")
