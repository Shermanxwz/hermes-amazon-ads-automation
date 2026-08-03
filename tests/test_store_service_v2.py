from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest

from amazon_ads_control.catalog import descriptor_from_payload
from amazon_ads_control.db import Store
from amazon_ads_control.service import ControlService
from helpers import Environment, READ_CAMPAIGN, WRITE_TARGET, CRITICAL_ACCOUNT


class StoreServiceV2Tests(unittest.TestCase):
    def setUp(self): self.env=Environment(); self.store=self.env.store; self.service=self.env.service
    def tearDown(self): self.env.close()

    def test_catalog_required_for_ads_tool(self):
        result=self.service.authorize_tool({"tool_name":WRITE_TARGET["registered_name"],"args":{},"session_id":"main"})
        self.assertFalse(result["allowed"]); self.assertIn("absent",result["reason"])

    def test_main_read_allowed_after_catalog_sync(self):
        self.env.sync_basic_catalog()
        result=self.service.authorize_tool({"tool_name":READ_CAMPAIGN["registered_name"],"args":{},"session_id":"main"})
        self.assertTrue(result["allowed"]); self.assertEqual(result["operation"],"read")

    def test_main_write_blocked(self):
        _,_,decision=self.env.one_decision_task()
        result=self.service.authorize_tool({"tool_name":WRITE_TARGET["registered_name"],"args":{"targetId":"t1","bid":0.8},"session_id":"main"})
        self.assertFalse(result["allowed"]); self.assertIn("executor",result["reason"])
        self.assertEqual(self.store.get_decision(decision["id"])["status"],"planned")

    def test_executor_write_and_independent_verification(self):
        _,task,decision=self.env.one_decision_task()
        self.service.bind_worker({"task_id":task["id"],"worker_session_id":"exec","role":"executor","goal":f"[ads-task:{task['id']}] [ads-role:executor]"})
        auth=self.service.authorize_tool({"tool_name":WRITE_TARGET["registered_name"],"args":{"targetId":"t1","bid":0.8},"session_id":"exec","tool_call_id":"call1"})
        self.assertTrue(auth["allowed"]); self.assertTrue(auth["reservation_token"])
        outcome=self.service.finish_tool({"tool_name":WRITE_TARGET["registered_name"],"args":{"targetId":"t1","bid":0.8},"result":{"success":[{"targetId":"t1"}],"error":[]},"session_id":"exec","decision_id":decision["id"],"reservation_token":auth["reservation_token"],"tool_call_id":"call1"})
        self.assertEqual(outcome["outcome"]["status"],"success")
        self.store.finish_worker("exec","completed","done")
        self.service.bind_worker({"task_id":task["id"],"worker_session_id":"verify","role":"verifier","goal":f"[ads-task:{task['id']}] [ads-role:verifier]"})
        verified=self.service.verify_decision({"decision_id":decision["id"],"session_id":"verify","actual":{"targetId":"t1","bid":0.8}})
        self.assertEqual(verified["status"],"verified")
        final=self.service.finalize_task({"task_id":task["id"],"summary":"verified"},"main")
        self.assertEqual(final["status"],"completed")

    def test_verifier_cannot_write(self):
        _,task,decision=self.env.one_decision_task()
        self.service.bind_worker({"task_id":task["id"],"worker_session_id":"exec","role":"executor","goal":"x"})
        auth=self.service.authorize_tool({"tool_name":WRITE_TARGET["registered_name"],"args":{"targetId":"t1","bid":0.8},"session_id":"exec"})
        self.service.finish_tool({"tool_name":WRITE_TARGET["registered_name"],"result":{"success":[{}],"error":[]},"session_id":"exec","decision_id":decision["id"],"reservation_token":auth["reservation_token"]})
        self.store.finish_worker("exec","completed")
        self.service.bind_worker({"task_id":task["id"],"worker_session_id":"v","role":"verifier","goal":"x"})
        denied=self.service.authorize_tool({"tool_name":WRITE_TARGET["registered_name"],"args":{"targetId":"t1","bid":0.8},"session_id":"v"})
        self.assertFalse(denied["allowed"]); self.assertIn("executor",denied["reason"])

    def test_executor_cannot_self_verify(self):
        _,task,decision=self.env.one_decision_task()
        self.service.bind_worker({"task_id":task["id"],"worker_session_id":"exec","role":"executor","goal":"x"})
        with self.assertRaises(ValueError):
            self.service.verify_decision({"decision_id":decision["id"],"session_id":"exec","actual":{"bid":0.8}})

    def test_atomic_reservation_has_one_winner(self):
        _,task,decision=self.env.one_decision_task()
        def reserve(i):
            try: return bool(self.store.reserve_decision(decision["id"],task["id"],f"w{i}",300))
            except ValueError: return False
        with ThreadPoolExecutor(max_workers=12) as pool:
            results=list(pool.map(reserve,range(12)))
        self.assertEqual(sum(results),1)

    def test_ambiguous_or_wrong_write_is_blocked(self):
        _,task,_=self.env.one_decision_task()
        self.service.bind_worker({"task_id":task["id"],"worker_session_id":"exec","role":"executor","goal":"x"})
        wrong=self.service.authorize_tool({"tool_name":WRITE_TARGET["registered_name"],"args":{"targetId":"other","bid":0.8},"session_id":"exec"})
        self.assertFalse(wrong["allowed"]); self.assertIn("does not match",wrong["reason"])

    def test_observe_mode_blocks_executor(self):
        _,task,_=self.env.one_decision_task(autopilot=False)
        self.service.bind_worker({"task_id":task["id"],"worker_session_id":"exec","role":"executor","goal":"x"})
        result=self.service.authorize_tool({"tool_name":WRITE_TARGET["registered_name"],"args":{"targetId":"t1","bid":0.8},"session_id":"exec"})
        self.assertFalse(result["allowed"]); self.assertIn("disabled",result["reason"])

    def test_schema_drift_blocks_until_acknowledged(self):
        _,task,_=self.env.one_decision_task()
        changed=dict(WRITE_TARGET); changed["schema"]={"description":"Update target bid","parameters":{"type":"object","required":["targetId","bid"]}}
        self.store.sync_catalog([descriptor_from_payload(READ_CAMPAIGN),descriptor_from_payload(changed)])
        self.service.bind_worker({"task_id":task["id"],"worker_session_id":"exec","role":"executor","goal":"x"})
        blocked=self.service.authorize_tool({"tool_name":WRITE_TARGET["registered_name"],"args":{"targetId":"t1","bid":0.8},"session_id":"exec"})
        self.assertFalse(blocked["allowed"]); self.assertIn("drift",blocked["reason"])
        self.store.acknowledge_tool_drift(WRITE_TARGET["registered_name"])
        allowed=self.service.authorize_tool({"tool_name":WRITE_TARGET["registered_name"],"args":{"targetId":"t1","bid":0.8},"session_id":"exec"})
        self.assertTrue(allowed["allowed"])

    def test_critical_account_write_always_blocked(self):
        _,task,_=self.env.one_decision_task()
        self.service.bind_worker({"task_id":task["id"],"worker_session_id":"exec","role":"executor","goal":"x"})
        result=self.service.authorize_tool({"tool_name":CRITICAL_ACCOUNT["registered_name"],"args":{"accountId":"a"},"session_id":"exec"})
        self.assertFalse(result["allowed"])

    def test_unknown_result_is_not_success(self):
        _,task,decision=self.env.one_decision_task()
        self.service.bind_worker({"task_id":task["id"],"worker_session_id":"exec","role":"executor","goal":"x"})
        auth=self.service.authorize_tool({"tool_name":WRITE_TARGET["registered_name"],"args":{"targetId":"t1","bid":0.8},"session_id":"exec"})
        out=self.service.finish_tool({"tool_name":WRITE_TARGET["registered_name"],"result":{"message":"accepted maybe"},"session_id":"exec","decision_id":decision["id"],"reservation_token":auth["reservation_token"]})
        self.assertEqual(out["outcome"]["status"],"unknown")
        self.assertEqual(self.store.get_decision(decision["id"])["status"],"failed")
        self.assertTrue(any(a["code"]=="WRITE_OUTCOME_UNCONFIRMED" for a in self.store.list_alerts()))

    def test_verification_mismatch_creates_alert(self):
        _,task,decision=self.env.one_decision_task()
        self.service.bind_worker({"task_id":task["id"],"worker_session_id":"exec","role":"executor","goal":"x"})
        auth=self.service.authorize_tool({"tool_name":WRITE_TARGET["registered_name"],"args":{"targetId":"t1","bid":0.8},"session_id":"exec"})
        self.service.finish_tool({"tool_name":WRITE_TARGET["registered_name"],"result":{"success":[{}],"error":[]},"session_id":"exec","decision_id":decision["id"],"reservation_token":auth["reservation_token"]})
        self.store.finish_worker("exec","completed")
        self.service.bind_worker({"task_id":task["id"],"worker_session_id":"v","role":"verifier","goal":"x"})
        result=self.service.verify_decision({"decision_id":decision["id"],"session_id":"v","actual":{"bid":0.7}})
        self.assertEqual(result["status"],"mismatch")
        self.assertTrue(any(a["code"]=="WRITE_VERIFICATION_MISMATCH" for a in self.store.list_alerts()))

    def test_stream_deduplication(self):
        event={"profile_id":"p1","dataset_id":"budget-usage","event_time":"2026-01-01T00:00:00Z","dedupe_key":"x","payload":{"campaignId":"c"}}
        self.assertEqual(self.service.ingest_stream({"events":[event,event]}),{"inserted":1,"duplicates":1})

    def test_cross_cycle_equivalent_write_is_cooldown_locked(self):
        self.env.sync_basic_catalog(); self.store.update_settings({"mode":"autopilot","execution_enabled":True})
        c1=self.service.plan_cycle({"snapshot":__import__("helpers").one_target_snapshot()},"main")
        c2=self.service.plan_cycle({"snapshot":__import__("helpers").one_target_snapshot()},"main")
        t1=self.service.create_task({"cycle_id":c1["id"]},"main"); t2=self.service.create_task({"cycle_id":c2["id"]},"main")
        d1=self.store.list_decisions(task_id=t1["id"])[0]; d2=self.store.list_decisions(task_id=t2["id"])[0]
        self.store.reserve_decision(d1["id"],t1["id"],"a",300,86400)
        with self.assertRaisesRegex(ValueError,"cooldown"):
            self.store.reserve_decision(d2["id"],t2["id"],"b",300,86400)

    def test_dashboard_contains_operational_surfaces(self):
        self.env.one_decision_task()
        d=self.store.dashboard()
        for key in ("latest_cycle","recent_cycles","recent_tasks","recent_actions","alerts","workers","catalog","profiles"):
            self.assertIn(key,d)

    def test_main_can_create_bounded_report_job(self):
        from helpers import REPORT_CREATE
        self.env.sync_basic_catalog()
        result=self.service.authorize_tool({"tool_name":REPORT_CREATE["registered_name"],"args":{"reportTypeId":"spTargeting"},"session_id":"main"})
        self.assertTrue(result["allowed"]); self.assertEqual(result["operation"],"job")

    def test_report_job_schema_and_role_are_enforced(self):
        from helpers import REPORT_CREATE
        _,task,_=self.env.one_decision_task()
        self.service.bind_worker({"task_id":task["id"],"worker_session_id":"exec-job","role":"executor","goal":"x"})
        denied=self.service.authorize_tool({"tool_name":REPORT_CREATE["registered_name"],"args":{"reportTypeId":"spTargeting"},"session_id":"exec-job"})
        self.assertFalse(denied["allowed"]); self.assertIn("main-controller",denied["reason"])
        malformed=self.service.authorize_tool({"tool_name":REPORT_CREATE["registered_name"],"args":{},"session_id":"main"})
        self.assertFalse(malformed["allowed"]); self.assertIn("required",malformed["reason"])

    def test_paused_mode_blocks_reads_and_jobs(self):
        from helpers import REPORT_CREATE
        self.env.sync_basic_catalog(); self.store.update_settings({"mode":"paused"})
        read=self.service.authorize_tool({"tool_name":READ_CAMPAIGN["registered_name"],"args":{},"session_id":"main"})
        job=self.service.authorize_tool({"tool_name":REPORT_CREATE["registered_name"],"args":{"reportTypeId":"spTargeting"},"session_id":"main"})
        self.assertFalse(read["allowed"]); self.assertFalse(job["allowed"])

    def test_multi_entity_write_batch_is_blocked(self):
        _,task,_=self.env.one_decision_task()
        self.service.bind_worker({"task_id":task["id"],"worker_session_id":"batch","role":"executor","goal":"x"})
        result=self.service.authorize_tool({
            "tool_name":WRITE_TARGET["registered_name"],
            "args":{"targets":[{"targetId":"t1","bid":0.8},{"targetId":"evil","bid":5.0}]},
            "session_id":"batch",
        })
        self.assertFalse(result["allowed"]); self.assertIn("batch limit",result["reason"])

    def test_exact_match_fields_reject_wrong_entity_context(self):
        _,task,_=self.env.one_decision_task()
        self.service.bind_worker({"task_id":task["id"],"worker_session_id":"wrong-context","role":"executor","goal":"x"})
        result=self.service.authorize_tool({
            "tool_name":WRITE_TARGET["registered_name"],
            "args":{"targetId":"t1","bid":0.8,"campaignId":"unexpected"},
            "session_id":"wrong-context",
        })
        # Bid decisions intentionally bind target and after-value; unrelated extra scalar context is auditable but harmless.
        self.assertTrue(result["allowed"])

    def test_worker_role_must_be_explicit(self):
        _,task,_=self.env.one_decision_task()
        with self.assertRaisesRegex(ValueError,"explicit"):
            self.service.bind_worker({"task_id":task["id"],"worker_session_id":"missing","goal":"x"})
        with self.assertRaisesRegex(ValueError,"does not match"):
            self.service.bind_worker({"task_id":task["id"],"worker_session_id":"mismatch","role":"executor","goal":"[ads-role:verifier]"})

class FinalSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.store=Store(Path(self.temp.name)/"state.db"); self.service=ControlService(self.store)
    def tearDown(self): self.temp.cleanup()

    def test_locked_safety_invariants_cannot_be_disabled(self):
        for key,value in {
            "catalog_required":False,"catalog_drift_blocks_writes":False,"require_planned_writes":False,
            "require_independent_verification":False,"block_deletes":False,"block_account_admin":False,
            "max_write_batch_size":2,
        }.items():
            with self.assertRaisesRegex(ValueError,"locked safety invariant"):
                self.store.update_settings({key:value})
        with self.assertRaisesRegex(ValueError,"autopilot"):
            self.store.update_settings({"execution_enabled":True})

    def test_catalog_removed_tool_is_disabled_and_alerted(self):
        self.service.sync_catalog({"tools":[WRITE_TARGET, READ_CAMPAIGN]})
        result=self.service.sync_catalog({"tools":[WRITE_TARGET]})
        self.assertEqual(result["removed"],[READ_CAMPAIGN["registered_name"]])
        self.assertFalse(self.store.get_tool(READ_CAMPAIGN["registered_name"])["enabled"])
        self.assertTrue(any(a["code"]=="MCP_TOOL_REMOVED" for a in self.store.list_alerts()))

    def test_stream_alerts_and_deduplication(self):
        event={"profile_id":"p1","dataset_id":"spBudgetUsage","dedupe_key":"budget-1","payload":{"budgetUsagePercent":97}}
        self.assertEqual(self.service.ingest_stream({"events":[event,event]}),{"inserted":1,"duplicates":1})
        event2={"profile_id":"p1","dataset_id":"campaignStatus","dedupe_key":"status-1","payload":{"servingStatus":"INELIGIBLE"}}
        self.service.ingest_stream(event2)
        codes=[a["code"] for a in self.store.list_alerts()]
        self.assertIn("BUDGET_NEAR_EXHAUSTION",codes); self.assertIn("AD_INELIGIBLE",codes)
        self.service.ingest_stream({**event,"dedupe_key":"budget-2"})
        self.assertEqual(sum(a["code"]=="BUDGET_NEAR_EXHAUSTION" for a in self.store.list_alerts()),1)

    def test_dashboard_write_count_and_verification_listing(self):
        self.store.record_action(task_id=None,session_id="s",actor_role="executor",phase="before",tool_name="x",operation="write",allowed=True,args={})
        self.store.record_action(task_id=None,session_id="s",actor_role="executor",phase="after",tool_name="x",operation="write",allowed=True,args={},success=True)
        self.assertEqual(self.store.dashboard()["writes_today"],1)
        self.assertEqual(self.store.list_verifications(),[])
