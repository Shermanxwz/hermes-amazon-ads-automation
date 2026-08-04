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

from amazon_ads_control.reporting import snapshot_hash
from amazon_ads_control.security import hash_password
from helpers import READ_CAMPAIGN, READ_TARGET, REPORT_CREATE, WRITE_TARGET, one_target_snapshot

ROOT=Path(__file__).resolve().parents[1]


class ProcessE2EV2Tests(unittest.TestCase):
    def request(self,base,path,method="GET",data=None):
        body=None if data is None else json.dumps(data).encode()
        req=Request(base+path,data=body,method=method,headers={"Content-Type":"application/json","Authorization":"Bearer "+"z"*48})
        try:
            with urlopen(req,timeout=5) as response:
                return response.status,json.loads(response.read().decode())
        except HTTPError as error:
            return error.code,json.loads(error.read().decode())

    def report_lineage(self,base,snapshot):
        window=snapshot["window"]
        status,job=self.request(base,"/api/agent/reports","POST",{"spec":{
            "profile_id":"p1","report_type":"process-e2e","start_date":window["start"],
            "end_date":window["end"],"timezone":"UTC","ad_product":"SPONSORED_PRODUCTS"
        }})
        self.assertEqual(status,201)
        report_id="report-"+job["id"]
        status,allowed=self.request(base,"/api/agent/tool-check","POST",{
            "tool_name":REPORT_CREATE["registered_name"],"args":{"reportTypeId":"process-e2e"},
            "session_id":"main","tool_call_id":"report-action"
        })
        self.assertEqual(status,200); self.assertTrue(allowed["allowed"])
        status,recorded=self.request(base,"/api/agent/tool-result","POST",{
            "tool_name":REPORT_CREATE["registered_name"],"args":{"reportTypeId":"process-e2e"},
            "result":{"reportId":report_id,"status":"SUCCESS","rows":[{"targetId":"t1","clicks":15}]},
            "session_id":"main","tool_call_id":"report-action"
        })
        self.assertEqual(status,200)
        evidence_id=recorded["action_id"]
        transitions=(
            ("SUBMITTED",{"report_id":report_id}),
            ("SUCCEEDED",{}),
            ("DOWNLOADED",{}),
            ("VALIDATED",{"snapshot":snapshot}),
            ("INGESTED",{}),
        )
        for state,data in transitions:
            payload={"report_job_id":job["id"],"status":state,"data":data,"session_id":"main"}
            if state in {"SUBMITTED","SUCCEEDED","DOWNLOADED"}:
                payload["evidence_action_id"]=evidence_id
            status,job=self.request(base,"/api/agent/reports/transition","POST",payload)
            self.assertEqual(status,200)
        return {"report_job_ids":[job["id"]],"normalized_hash":snapshot_hash(snapshot),"action_ids":[evidence_id]}

    def test_full_main_executor_verifier_process_flow(self):
        with tempfile.TemporaryDirectory() as directory:
            sock=socket.socket(); sock.bind(("127.0.0.1",0)); port=sock.getsockname()[1]; sock.close()
            env=os.environ.copy(); env.update({
                "PYTHONPATH":str(ROOT/"control-plane"),"ADS_CONTROL_PORT":str(port),
                "ADS_CONTROL_DB":str(Path(directory)/"state.db"),"ADS_CONTROL_AGENT_TOKEN":"z"*48,
                "ADS_CONTROL_PASSWORD_HASH":hash_password("correct horse battery staple"),
            })
            proc=subprocess.Popen([sys.executable,"-m","amazon_ads_control.server"],cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            base=f"http://127.0.0.1:{port}"
            try:
                for _ in range(80):
                    try:
                        if self.request(base,"/health/live")[0]==200:
                            break
                    except Exception:
                        time.sleep(.05)
                else:
                    self.fail("server did not start")
                self.assertEqual(self.request(base,"/api/agent/catalog-sync","POST",{"tools":[READ_CAMPAIGN,READ_TARGET,WRITE_TARGET,REPORT_CREATE]})[0],200)
                jar=CookieJar(); browser=build_opener(HTTPCookieProcessor(jar))
                login=Request(base+"/api/login",data=json.dumps({"password":"correct horse battery staple"}).encode(),method="POST",headers={"Content-Type":"application/json"})
                with browser.open(login) as response:
                    csrf=json.loads(response.read().decode())["csrf"]
                enable=Request(base+"/api/settings",data=json.dumps({"mode":"autopilot","execution_enabled":True}).encode(),method="PUT",headers={"Content-Type":"application/json","X-CSRF-Token":csrf})
                with browser.open(enable) as response:
                    self.assertEqual(response.status,200)

                snapshot=one_target_snapshot()
                lineage=self.report_lineage(base,snapshot)
                status,report_evidence=self.request(base,"/api/agent/report-evidence","POST",{"session_id":"main"})
                self.assertEqual(status,200); self.assertEqual(report_evidence["evidence"][0]["id"],lineage["action_ids"][0])
                status,cycle=self.request(base,"/api/agent/cycles/plan","POST",{"snapshot":snapshot,"lineage":lineage})
                self.assertEqual(status,201)
                status,task=self.request(base,"/api/agent/tasks","POST",{"cycle_id":cycle["id"]})
                self.assertEqual(status,201)
                decision=cycle["decisions"][0]

                status,_blocked=self.request(base,"/api/agent/tool-check","POST",{
                    "tool_name":WRITE_TARGET["registered_name"],"args":{"targetId":"t1","bid":.8},"session_id":"main"
                })
                self.assertEqual(status,403)

                self.request(base,"/api/agent/worker-bind","POST",{
                    "task_id":task["id"],"worker_session_id":"exec","role":"executor","goal":"executor"
                })
                self.assertEqual(self.request(base,"/api/agent/tool-check","POST",{
                    "tool_name":READ_TARGET["registered_name"],"args":{"targetId":"t1"},
                    "session_id":"exec","tool_call_id":"pre-read"
                })[0],200)
                status,pre_read=self.request(base,"/api/agent/tool-result","POST",{
                    "tool_name":READ_TARGET["registered_name"],"args":{"targetId":"t1"},
                    "result":{"targetId":"t1","bid":1.0},"session_id":"exec",
                    "task_id":task["id"],"tool_call_id":"pre-read"
                })
                self.assertEqual(status,200)
                status,_prepared=self.request(base,"/api/agent/prepare-write","POST",{
                    "decision_id":decision["id"],"evidence_action_id":pre_read["action_id"],"session_id":"exec"
                })
                self.assertEqual(status,200)
                status,auth=self.request(base,"/api/agent/tool-check","POST",{
                    "tool_name":WRITE_TARGET["registered_name"],"args":{"targetId":"t1","bid":.8},
                    "session_id":"exec","tool_call_id":"write-1"
                })
                self.assertEqual(status,200)
                self.assertEqual(self.request(base,"/api/agent/tool-check","POST",{
                    "tool_name":WRITE_TARGET["registered_name"],"args":{"targetId":"t1","bid":.8},"session_id":"exec2"
                })[0],403)
                status,outcome=self.request(base,"/api/agent/tool-result","POST",{
                    "event_id":"process-write-event-1","tool_name":WRITE_TARGET["registered_name"],
                    "args":{"targetId":"t1","bid":.8},"result":{"success":[{"targetId":"t1"}],"error":[]},
                    "session_id":"exec","decision_id":decision["id"],"reservation_token":auth["reservation_token"]
                })
                self.assertEqual(status,200); self.assertEqual(outcome["outcome"]["status"],"success")
                self.request(base,"/api/agent/worker-stop","POST",{"worker_session_id":"exec","status":"completed"})

                self.assertEqual(self.request(base,"/api/agent/worker-bind","POST",{
                    "task_id":task["id"],"worker_session_id":"verify","role":"verifier","goal":"verifier"
                })[0],200)
                self.assertEqual(self.request(base,"/api/agent/tool-check","POST",{
                    "tool_name":READ_TARGET["registered_name"],"args":{"targetId":"t1"},
                    "session_id":"verify","tool_call_id":"verify-read"
                })[0],200)
                status,read=self.request(base,"/api/agent/tool-result","POST",{
                    "tool_name":READ_TARGET["registered_name"],"args":{"targetId":"t1"},
                    "result":{"targetId":"t1","bid":.8},"session_id":"verify",
                    "task_id":task["id"],"tool_call_id":"verify-read"
                })
                self.assertEqual(status,200)
                status,evidence=self.request(base,"/api/agent/read-evidence","POST",{
                    "decision_id":decision["id"],"session_id":"verify"
                })
                self.assertEqual(status,200); self.assertEqual(evidence["evidence"][0]["action_id"],read["action_id"])
                status,verified=self.request(base,"/api/agent/verify","POST",{
                    "decision_id":decision["id"],"session_id":"verify","evidence_action_id":read["action_id"]
                })
                self.assertEqual(status,200); self.assertEqual(verified["status"],"verified")
                status,final=self.request(base,"/api/agent/task-finalize","POST",{
                    "task_id":task["id"],"summary":"verified"
                })
                self.assertEqual(status,200); self.assertEqual(final["status"],"completed")
            finally:
                proc.terminate(); proc.wait(timeout=10)
                stderr=proc.stderr.read() if proc.stderr else ""
                if proc.stdout: proc.stdout.close()
                if proc.stderr: proc.stderr.close()
                if proc.returncode not in (0,-15):
                    self.fail(stderr)
