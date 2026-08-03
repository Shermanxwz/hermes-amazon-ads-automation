from pathlib import Path
import tempfile
import unittest
from amazon_ads_control.db import Store
from amazon_ads_control.service import ControlService

class StoreServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "state.db")
        self.service = ControlService(self.store)
    def tearDown(self): self.tmp.cleanup()

    def test_main_write_blocked_worker_allowed(self):
        task = self.service.create_task({"title":"lower waste","kind":"optimization","objective":"lower bad bid","write_allowed":True,"expected_actions":[{"idempotency_key":"kw-1","tool_contains":"update_campaign","entity_id":"cmp-1","field":"budget","before":100,"after":90,"reason":"waste"}]}, "main")
        denied = self.service.authorize_tool({"tool_name":"campaign_management-update_campaign","args":{"campaign_id":"cmp-1","budget":90},"session_id":"main-session"})
        self.assertFalse(denied["allowed"])
        self.store.bind_worker(task["id"], "main-session", "worker-session", "sub-1", f"[ads-task:{task['id']}] execute")
        allowed = self.service.authorize_tool({"tool_name":"campaign_management-update_campaign","args":{"campaign_id":"cmp-1","budget":90},"session_id":"worker-session"})
        self.assertTrue(allowed["allowed"])
        self.assertEqual(allowed["task_id"], task["id"])

    def test_read_allowed_for_main(self):
        result = self.service.authorize_tool({"tool_name":"campaign_management-query_campaign","args":{},"session_id":"main"})
        self.assertTrue(result["allowed"])
        self.assertEqual(result["actor_role"], "main")

    def test_observe_mode_blocks_worker_writes(self):
        task = self.service.create_task({"title":"x","kind":"optimization","objective":"y","write_allowed":True,"expected_actions":[{"idempotency_key":"bid-1","tool_contains":"update_bid","entity_id":"kw-1","field":"bid","before":1.0,"after":0.95,"reason":"test"}]}, "main")
        self.store.bind_worker(task["id"], "main", "worker", "sub", f"[ads-task:{task['id']}] y")
        self.store.update_settings({"mode":"observe"})
        result = self.service.authorize_tool({"tool_name":"amazon_ads_update_bid","args":{"keyword_id":"kw-1","bid":0.95},"session_id":"worker"})
        self.assertFalse(result["allowed"])

    def test_successful_plan_is_idempotent(self):
        task = self.service.create_task({"title":"x","kind":"optimization","objective":"y","write_allowed":True,"expected_actions":[{"idempotency_key":"bid-once","tool_contains":"update_bid","entity_id":"kw-1","field":"bid","before":1.0,"after":0.9,"reason":"test"}]}, "main")
        self.store.bind_worker(task["id"], "main", "worker", "sub", f"[ads-task:{task['id']}] y")
        payload = {"tool_name":"amazon_ads_update_bid","args":{"keyword_id":"kw-1","bid":0.9},"session_id":"worker"}
        first = self.service.authorize_tool(payload)
        self.assertTrue(first["allowed"])
        self.service.finish_tool({**payload, "result":"{\"ok\":true}", "duration_ms":10})
        second = self.service.authorize_tool(payload)
        self.assertFalse(second["allowed"])
        self.assertIn("already completed", second["reason"])

    def test_worker_completion_updates_task(self):
        task = self.service.create_task({"title":"x","kind":"audit","objective":"y","write_allowed":False}, "main")
        self.store.bind_worker(task["id"], "main", "worker", "sub", f"[ads-task:{task['id']}] y")
        self.store.finish_worker("worker", "completed", "done", 123, {"read_back": "ok"})
        done = self.store.get_task(task["id"])
        self.assertEqual(done["status"], "completed")
        self.assertEqual(done["result"]["summary"], "done")
        self.assertEqual(done["result"]["verification"]["read_back"], "ok")

    def test_dashboard_has_counts(self):
        self.service.create_task({"title":"audit","kind":"audit","objective":"read"}, "main")
        d = self.store.dashboard()
        self.assertEqual(d["counts"]["planned"], 1)
        self.assertIn("settings", d)

if __name__ == '__main__': unittest.main()
