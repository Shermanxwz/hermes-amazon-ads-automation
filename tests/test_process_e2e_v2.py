from pathlib import Path
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError
from http.cookiejar import CookieJar
from urllib.request import Request, urlopen, build_opener, HTTPCookieProcessor

from amazon_ads_control.security import hash_password
from helpers import READ_CAMPAIGN, WRITE_TARGET, one_target_snapshot

ROOT=Path(__file__).resolve().parents[1]


class ProcessE2EV2Tests(unittest.TestCase):
    def request(self,base,path,method="GET",data=None):
        body=None if data is None else json.dumps(data).encode(); req=Request(base+path,data=body,method=method,headers={"Content-Type":"application/json","Authorization":"Bearer "+"z"*48})
        try:
            with urlopen(req,timeout=5) as r: return r.status,json.loads(r.read().decode())
        except HTTPError as e: return e.code,json.loads(e.read().decode())

    def test_full_main_executor_verifier_process_flow(self):
        with tempfile.TemporaryDirectory() as d:
            sock=socket.socket(); sock.bind(("127.0.0.1",0)); port=sock.getsockname()[1]; sock.close()
            env=os.environ.copy(); env.update({"PYTHONPATH":str(ROOT/"control-plane"),"ADS_CONTROL_PORT":str(port),"ADS_CONTROL_DB":str(Path(d)/"state.db"),"ADS_CONTROL_AGENT_TOKEN":"z"*48,"ADS_CONTROL_PASSWORD_HASH":hash_password("correct horse battery staple")})
            proc=subprocess.Popen([sys.executable,"-m","amazon_ads_control.server"],cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            base=f"http://127.0.0.1:{port}"
            try:
                for _ in range(80):
                    try:
                        if self.request(base,"/health/live")[0]==200: break
                    except Exception: time.sleep(.05)
                else: self.fail("server did not start")
                self.assertEqual(self.request(base,"/api/agent/catalog-sync","POST",{"tools":[READ_CAMPAIGN,WRITE_TARGET]})[0],200)
                jar=CookieJar(); browser=build_opener(HTTPCookieProcessor(jar))
                login=Request(base+"/api/login",data=json.dumps({"password":"correct horse battery staple"}).encode(),method="POST",headers={"Content-Type":"application/json"})
                with browser.open(login) as response: csrf=json.loads(response.read().decode())["csrf"]
                enable=Request(base+"/api/settings",data=json.dumps({"mode":"autopilot","execution_enabled":True}).encode(),method="PUT",headers={"Content-Type":"application/json","X-CSRF-Token":csrf})
                with browser.open(enable) as response: self.assertEqual(response.status,200)
                status,cycle=self.request(base,"/api/agent/cycles/plan","POST",{"snapshot":one_target_snapshot()}); self.assertEqual(status,201)
                status,task=self.request(base,"/api/agent/tasks","POST",{"cycle_id":cycle["id"]}); self.assertEqual(status,201)
                decision=cycle["decisions"][0]
                # Main is denied.
                status,blocked=self.request(base,"/api/agent/tool-check","POST",{"tool_name":WRITE_TARGET["registered_name"],"args":{"targetId":"t1","bid":.8},"session_id":"main"}); self.assertEqual(status,403)
                # Executor binds and receives an atomic reservation.
                self.request(base,"/api/agent/worker-bind","POST",{"task_id":task["id"],"worker_session_id":"exec","role":"executor","goal":"executor"})
                status,auth=self.request(base,"/api/agent/tool-check","POST",{"tool_name":WRITE_TARGET["registered_name"],"args":{"targetId":"t1","bid":.8},"session_id":"exec","tool_call_id":"x"}); self.assertEqual(status,200)
                # Duplicate concurrent write loses the reservation.
                self.assertEqual(self.request(base,"/api/agent/tool-check","POST",{"tool_name":WRITE_TARGET["registered_name"],"args":{"targetId":"t1","bid":.8},"session_id":"exec2"})[0],403)
                status,out=self.request(base,"/api/agent/tool-result","POST",{"tool_name":WRITE_TARGET["registered_name"],"args":{"targetId":"t1","bid":.8},"result":{"success":[{"targetId":"t1"}],"error":[]},"session_id":"exec","decision_id":decision["id"],"reservation_token":auth["reservation_token"]}); self.assertEqual(status,200); self.assertEqual(out["outcome"]["status"],"success")
                self.request(base,"/api/agent/worker-stop","POST",{"worker_session_id":"exec","status":"completed"})
                # Independent verifier reads and commits expected state.
                self.assertEqual(self.request(base,"/api/agent/worker-bind","POST",{"task_id":task["id"],"worker_session_id":"verify","role":"verifier","goal":"verifier"})[0],200)
                status,verified=self.request(base,"/api/agent/verify","POST",{"decision_id":decision["id"],"session_id":"verify","actual":{"targetId":"t1","bid":.8}}); self.assertEqual(status,200); self.assertEqual(verified["status"],"verified")
                status,final=self.request(base,"/api/agent/task-finalize","POST",{"task_id":task["id"],"summary":"verified"}); self.assertEqual(status,200); self.assertEqual(final["status"],"completed")
            finally:
                proc.terminate(); proc.wait(timeout=10)
                stderr = proc.stderr.read() if proc.stderr else ""
                if proc.stdout: proc.stdout.close()
                if proc.stderr: proc.stderr.close()
                if proc.returncode not in (0,-15):
                    self.fail(stderr)
