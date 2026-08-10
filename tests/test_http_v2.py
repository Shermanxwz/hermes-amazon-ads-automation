from http.cookiejar import CookieJar
from pathlib import Path
import json
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPCookieProcessor

from amazon_ads_control.api import build_server
from amazon_ads_control.config import Settings
from amazon_ads_control.reporting import snapshot_hash
from amazon_ads_control.security import hash_password
from helpers import REPORT_CREATE, WRITE_TARGET, one_target_snapshot


class HttpV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.password="correct horse battery staple"
        settings=Settings(host="127.0.0.1",port=0,db_path=Path(self.temp.name)/"state.db",public_origin="http://127.0.0.1",
            control_password_hash=hash_password(self.password),agent_token="a"*48,session_ttl_seconds=3600,max_sessions=4,retention_days=30,allow_remote_bind=False)
        self.server=build_server(settings); self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.base=f"http://127.0.0.1:{self.server.server_address[1]}"; self.jar=CookieJar(); self.browser=build_opener(HTTPCookieProcessor(self.jar)); self.csrf=""
    def tearDown(self): self.server.shutdown(); self.server.server_close(); self.thread.join(); self.temp.cleanup()

    def request(self,path,method="GET",data=None,headers=None,opener=None):
        body=None if data is None else json.dumps(data).encode(); req=Request(self.base+path,data=body,method=method,headers={"Content-Type":"application/json",**(headers or {})})
        try:
            with (opener or build_opener()).open(req) as r: return r.status,json.loads(r.read().decode()),r.headers
        except HTTPError as e: return e.code,json.loads(e.read().decode()),e.headers

    def login(self):
        status,data,_=self.request("/api/login","POST",{"password":self.password},opener=self.browser); self.assertEqual(status,200); self.csrf=data["csrf"]

    def lineage(self, snapshot, auth):
        window=snapshot["window"]
        status,job,_=self.request("/api/agent/reports","POST",{"spec":{
            "profile_id":snapshot["profile"]["profile_id"],"report_type":"http-test","start_date":window["start"],"end_date":window["end"],"timezone":"UTC","ad_product":"SPONSORED_PRODUCTS"
        }},auth); self.assertEqual(status,201)
        report_id="r-"+job["id"]
        status,allowed,_=self.request("/api/agent/tool-check","POST",{
            "tool_name":REPORT_CREATE["registered_name"],"args":{"reportTypeId":"http-test"},"session_id":"main","tool_call_id":"report-action"
        },auth); self.assertEqual(status,200); self.assertTrue(allowed["allowed"])
        status,recorded,_=self.request("/api/agent/tool-result","POST",{
            "tool_name":REPORT_CREATE["registered_name"],"args":{"reportTypeId":"http-test"},
            "result":{"reportId":report_id,"status":"SUCCESS","rows":[{"targetId":"t1"}]},
            "session_id":"main","tool_call_id":"report-action"
        },auth); self.assertEqual(status,200)
        evidence_id=recorded["action_id"]
        for state,data in (
            ("SUBMITTED",{"report_id":report_id}),
            ("SUCCEEDED",{}),
            ("DOWNLOADED",{}),
            ("VALIDATED",{"snapshot":snapshot}),
            ("INGESTED",{}),
        ):
            payload={"report_job_id":job["id"],"status":state,"data":data,"session_id":"main"}
            if state in {"SUBMITTED","SUCCEEDED","DOWNLOADED"}: payload["evidence_action_id"]=evidence_id
            status,job,_=self.request("/api/agent/reports/transition","POST",payload,auth); self.assertEqual(status,200)
        return {"report_job_ids":[job["id"]],"normalized_hash":snapshot_hash(snapshot),"action_ids":[evidence_id]}

    def test_health_and_static(self):
        self.assertEqual(self.request("/health/live")[0],200)
        with build_opener().open(self.base+"/") as response: html=response.read().decode()
        self.assertIn("确定性策略",html); self.assertIn("独立验证",html)
        self.assertIn("本页怎么理解",html); self.assertIn("Main 主控",html); self.assertIn("仅观察",html)
        self.assertIn("报告生命周期",html); self.assertIn("写前必须重新读取并匹配原值",html)
        self.assertIn("只在同一实体对象内核对全部预期字段",html)
        self.assertIn("后台编排器",html)
        self.assertIn("orchestrator-status",html)
        with build_opener().open(self.base+"/static/app.js") as response:
            script=response.read().decode(); self.assertEqual(response.status,200)
        self.assertIn("已隔离",script)
        self.assertNotIn('const failed = Number(reports.FAILED || 0) + Number(reports.QUARANTINED || 0);',script)
        with build_opener().open(self.base+"/static/app_v3.js") as response:
            script=response.read().decode(); self.assertEqual(response.status,200)
        self.assertIn("result_outbox_pending",script); self.assertIn("runtime_status",script)

    def test_browser_auth_csrf_and_dashboard(self):
        self.assertEqual(self.request("/api/dashboard")[0],401); self.login()
        status,data,_=self.request("/api/dashboard",opener=self.browser); self.assertEqual(status,200); self.assertIn("catalog",data); self.assertIn("reports",data)
        status,_,_=self.request("/api/settings","PUT",{"mode":"paused"},{"Origin":"http://127.0.0.1"},self.browser); self.assertEqual(status,403)
        status,data,_=self.request("/api/settings","PUT",{"mode":"paused"},{"Origin":"http://127.0.0.1","X-CSRF-Token":self.csrf},self.browser); self.assertEqual(status,200); self.assertEqual(data["mode"],"paused")

    def test_agent_catalog_plan_and_views(self):
        auth={"Authorization":"Bearer "+"a"*48}
        self.assertEqual(self.request("/api/agent/catalog-sync","POST",{"tools":[WRITE_TARGET,REPORT_CREATE]},auth)[0],200)
        snapshot=one_target_snapshot(); lineage=self.lineage(snapshot,auth)
        status,evidence,_=self.request("/api/agent/report-evidence","POST",{"session_id":"main"},auth); self.assertEqual(status,200); self.assertEqual(evidence["evidence"][0]["id"],lineage["action_ids"][0])
        status,cycle,_=self.request("/api/agent/cycles/plan","POST",{"snapshot":snapshot,"lineage":lineage},auth); self.assertEqual(status,201); self.assertEqual(len(cycle["decisions"]),1)
        status,task,_=self.request("/api/agent/tasks","POST",{"cycle_id":cycle["id"]},auth); self.assertEqual(status,201); self.assertIn("id",task)
        self.login(); self.assertEqual(self.request("/api/cycles",opener=self.browser)[0],200); self.assertEqual(self.request("/api/decisions",opener=self.browser)[0],200); self.assertEqual(self.request("/api/reports",opener=self.browser)[0],200)

    def test_login_rate_limit(self):
        for _ in range(4): self.assertEqual(self.request("/api/login","POST",{"password":"wrong"})[0],401)
        status,data,headers=self.request("/api/login","POST",{"password":"wrong"})
        self.assertEqual(status,429); self.assertEqual(data["error"],"login_rate_limited"); self.assertIn("Retry-After",headers)
        self.assertEqual(self.request("/api/login","POST",{"password":self.password})[0],429)

    def test_one_proxy_client_cannot_lock_out_another_client(self):
        blocked_headers={"X-Forwarded-For":"203.0.113.10"}
        for _ in range(4):
            self.assertEqual(self.request("/api/login","POST",{"password":"wrong"},blocked_headers)[0],401)
        self.assertEqual(self.request("/api/login","POST",{"password":"wrong"},blocked_headers)[0],429)
        status,data,_=self.request(
            "/api/login","POST",{"password":self.password},
            {"X-Forwarded-For":"203.0.113.11"},self.browser,
        )
        self.assertEqual(status,200)
        self.assertIn("csrf",data)

    def test_agent_token_and_size(self):
        self.assertEqual(self.request("/api/agent/context")[0],401)
        status,data,_=self.request("/api/agent/context?session_id=x",headers={"Authorization":"Bearer "+"a"*48}); self.assertEqual(status,200); self.assertEqual(data["role"],"main")
