from __future__ import annotations

import unittest

from amazon_ads_control.verification_hardening import select_entity_scope
from helpers import Environment, READ_TARGET, WRITE_TARGET


class EntityScopedVerificationTests(unittest.TestCase):
    def setUp(self):
        self.env = Environment()
        self.store = self.env.store
        self.service = self.env.service

    def tearDown(self):
        self.env.close()

    def _executed_decision(self):
        _cycle, task, decision = self.env.one_decision_task()
        self.service.bind_worker({
            "task_id": task["id"],
            "worker_session_id": "exec",
            "role": "executor",
            "goal": f"[ads-task:{task['id']}] [ads-role:executor]",
        })
        authorization = self.service.authorize_tool({
            "tool_name": WRITE_TARGET["registered_name"],
            "args": {"targetId": "t1", "bid": 0.8},
            "session_id": "exec",
            "tool_call_id": "write-t1",
        })
        self.assertTrue(authorization["allowed"], authorization)
        self.service.finish_tool({
            "tool_name": WRITE_TARGET["registered_name"],
            "result": {"success": [{"targetId": "t1"}], "error": []},
            "session_id": "exec",
            "decision_id": decision["id"],
            "reservation_token": authorization["reservation_token"],
            "tool_call_id": "write-t1",
        })
        self.store.finish_worker("exec", "completed", "write complete")
        self.service.bind_worker({
            "task_id": task["id"],
            "worker_session_id": "verify",
            "role": "verifier",
            "goal": f"[ads-task:{task['id']}] [ads-role:verifier]",
        })
        return task, decision

    def test_fields_from_another_entity_cannot_satisfy_verification(self):
        task, decision = self._executed_decision()
        read = self.service.finish_tool({
            "tool_name": READ_TARGET["registered_name"],
            "result": {
                "targets": [
                    {"targetId": "t1", "bid": 0.7},
                    {"targetId": "t2", "bid": 0.8},
                ]
            },
            "session_id": "verify",
            "task_id": task["id"],
            "tool_call_id": "read-two-targets",
        })
        verified = self.service.verify_decision({
            "decision_id": decision["id"],
            "session_id": "verify",
            "evidence_action_id": read["action_id"],
        })
        self.assertEqual(verified["status"], "mismatch")
        record = self.store.list_verifications(decision_id=decision["id"])[0]
        self.assertEqual(record["actual"]["targetId"], "t1")
        self.assertEqual(record["actual"]["bid"], 0.7)
        self.assertEqual(record["differences"]["bid"]["expected"], 0.8)

    def test_duplicate_entity_objects_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "multiple objects"):
            select_entity_scope(
                {
                    "data": {"targetId": "t1", "bid": 0.8},
                    "items": [{"targetId": "t1", "bid": 0.7}],
                },
                "t1",
                "target",
            )

    def test_identifier_in_unrelated_scalar_does_not_define_entity_scope(self):
        with self.assertRaisesRegex(ValueError, "identifiable object"):
            select_entity_scope(
                {"message": "t1", "targets": [{"targetId": "t2", "bid": 0.8}]},
                "t1",
                "target",
            )


if __name__ == "__main__":
    unittest.main()
