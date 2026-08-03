from pathlib import Path
import json
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPCookieProcessor
import http.cookiejar

from amazon_ads_control.api import build_server
from amazon_ads_control.config import Settings
from amazon_ads_control.db import Store
from amazon_ads_control.security import hash_password

class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.settings = Settings(host="127.0.0.1",port=0,db_path=Path(cls.tmp.name)/"db.sqlite",public_origin="http://test.local",control_password_hash=hash_password("this-is-a-long-test-password"),agent_token="a"*48,session_ttl_seconds=3600,max_sessions=4,retention_days=30,allow_remote_bind=False)
        cls.server = build_server(cls.settings, Store(cls.settings.db_path))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True); cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()

    def request(self,path,method="GET",data=None,headers=None,opener=None):
        body=None if data is None else json.dumps(data).encode()
        req=Request(self.base+path,data=body,method=method,headers={"Content-Type":"application/json",**(headers or {})})
        with (opener or build_opener()).open(req) as r: return r.status,json.loads(r.read().decode()),r.headers

    def test_health(self):
        status,data,_=self.request("/health/live"); self.assertEqual(status,200); self.assertTrue(data["ok"])


    def test_static_dashboard(self):
        req=Request(self.base+"/")
        with build_opener().open(req) as response:
            html=response.read().decode()
        self.assertEqual(response.status, 200)
        self.assertIn("Hermes Amazon Ads 主控台", html)

    def test_agent_auth_and_task(self):
        with self.assertRaises(HTTPError): self.request("/api/agent/context")
        status,data,_=self.request("/api/agent/tasks","POST",{"title":"t","kind":"audit","objective":"o"},{"Authorization":"Bearer "+"a"*48})
        self.assertEqual(status,201); self.assertEqual(data["status"],"planned")

    def test_browser_login_csrf(self):
        jar=http.cookiejar.CookieJar(); opener=build_opener(HTTPCookieProcessor(jar))
        status,data,_=self.request("/api/login","POST",{"password":"this-is-a-long-test-password"},opener=opener)
        self.assertEqual(status,200); csrf=data["csrf"]
        status,dash,_=self.request("/api/dashboard",opener=opener); self.assertEqual(status,200); self.assertIn("settings",dash)
        with self.assertRaises(HTTPError): self.request("/api/settings","PUT",{"mode":"observe"},opener=opener)
        status,settings,_=self.request("/api/settings","PUT",{"mode":"observe"},{"Origin":"http://test.local","X-CSRF-Token":csrf},opener)
        self.assertEqual(settings["mode"],"observe")

if __name__ == '__main__': unittest.main()
