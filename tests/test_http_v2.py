from http.cookiejar import CookieJar
from pathlib import Path
import hashlib
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
from helpers import WRITE_TARGET, one_target_snapshot


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
        for state,data in (
            ("SUBMITTED",{"report_id":report_id}),
            ("SUCCEEDED",{"report_id":report_id}),
            ("DOWNLOADED",{"content_hash":hashlib.sha256(b"content").hexdigest()}),
            ("VALIDATED",{"schema_hash":hashlib.sha256(b"schema").hexdigest()}),
            ("INGESTED",{"content_hash":hashlib.sha256(b"content").hexdigest(),"schema_hash":hashlib.sha256(b"schema").hexdigest(),"normalized_hash":snapshot_hash(snapshot),"row_count":1}),
        ):
            status,job,_=self.request("/api/agent/reports/transition","POST",{"report_job_id":job["id"],"status":state,"data":data},auth); self.assertEqual(status,200)
        return {"report_job_ids":[job["id"]],"normalized_hash":snapshot_hash(snapshot),"action_ids":[]}

    def test_health_and_static(self):
        self.assertEqual(self.request("/health/live")[0],200)
        with build_opener().open(self.base+"/") as r: html=r.read().decode()
        self.assertIn("确定性策略",html); self.assertIn("独立验证",html)
        self.assertIn("本页怎么理解",html); self.assertIn("Main 主控",html); self.assertIn("仅观察",html)
        self.assertIn("确认新 Schema",html); self.assertIn("Verifier 必须是不同的只读子代理",html)

    def test_browser_auth_csrf_and_dashboard(self):
        self.assertEqual(self.request("/api/dashboard")[0],401); self.login()
        status,data,_=self.request("/api/dashboard",opener=self.browser); self.assertEqual(status,200); self.assertIn("catalog",data); self.assertIn("reports",data)
        status,_,_=self.request("/api/settings","PUT",{"mode":"paused"},{"Origin":"http://127.0.0.1"},self.browser); self.assertEqual(status,403)
        status,data,_=self.request("/api/settings","PUT",{"mode":"paused"},{"Origin":"http://127.0.0.1","X-CSRF-Token":self.csrf},self.browser); self.assertEqual(status,200); self.assertEqual(data["mode"],"paused")

    def test_agent_catalog_plan_and_views(self):
        auth={"Authorization":"Bearer "+"a"*48}
        self.assertEqual(self.request("/api/agent/catalog-sync","POST",{"tools":[WRITE_TARGET]},auth)[0],200)
        snapshot=one_target_snapshot(); lineage=self.lineage(snapshot,auth)
        status,cycle,_=self.request("/api/agent/cycles/plan","POST",{"snapshot":snapshot,"lineage":lineage},auth); self.assertEqual(status,201); self.assertEqual(len(cycle["decisions"]),1)
        status,task,_=self.request("/api/agent/tasks","POST",{"cycle_id":cycle["id"]},auth); self.assertEqual(status,201); self.assertIn("id",task)
        self.login(); self.assertEqual(self.request("/api/cycles",opener=self.browser)[0],200); self.assertEqual(self.request("/api/decisions",opener=self.browser)[0],200); self.assertEqual(self.request("/api/reports",opener=self.browser)[0],200)

    def test_login_rate_limit(self):
        for _ in range(4): self.assertEqual(self.request("/api/login","POST",{"password":"wrong"})[0],401)
        status,data,headers=self.request("/api/login","POST",{"password":"wrong"})
        self.assertEqual(status,429); self.assertEqual(data["error"],"login_rate_limited"); self.assertIn("Retry-After",headers)
        self.assertEqual(self.request("/api/login","POST",{"password":self.password})[0],429)

    def test_agent_token_and_size(self):
        self.assertEqual(self.request("/api/agent/context")[0],401)
        status,data,_=self.request("/api/agent/context?session_id=x",headers={"Authorization":"Bearer "+"a"*48}); self.assertEqual(status,200); self.assertEqual(data["role"],"main")
