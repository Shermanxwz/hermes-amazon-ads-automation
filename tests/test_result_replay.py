from __future__ import annotations

import unittest

from helpers import Environment, WRITE_TARGET


class ResultReplayTests(unittest.TestCase):
    def setUp(self):
        self.env = Environment()

    def tearDown(self):
        self.env.close()

    def test_same_callback_is_idempotent_but_conflict_is_rejected(self):
        _cycle, task, decision = self.env.one_decision_task()
        service = self.env.service
        service.bind_worker({
            "task_id": task["id"],
            "worker_session_id": "exec",
            "role": "executor",
            "goal": f"[ads-task:{task['id']}] [ads-role:executor]",
        })
        auth = service.authorize_tool({
            "tool_name": WRITE_TARGET["registered_name"],
            "args": {"targetId": "t1", "bid": 0.8},
            "session_id": "exec",
            "tool_call_id": "call-1",
        })
        payload = {
            "event_id": "event-1",
            "tool_name": WRITE_TARGET["registered_name"],
            "args": {"targetId": "t1", "bid": 0.8},
            "result": {"success": [{"targetId": "t1"}], "error": []},
            "session_id": "exec",
            "decision_id": decision["id"],
            "reservation_token": auth["reservation_token"],
            "tool_call_id": "call-1",
        }
        first = service.finish_tool(payload)
        second = service.finish_tool(payload)
        self.assertFalse(first.get("duplicate", False))
        self.assertTrue(second["duplicate"])
        self.assertEqual(self.env.store.get_decision(decision["id"])["status"], "executed")

        conflicting = dict(payload)
        conflicting["result"] = {"error": [{"code": "rejected"}]}
        with self.assertRaisesRegex(ValueError, "conflicts"):
            service.finish_tool(conflicting)


if __name__ == "__main__":
    unittest.main()
