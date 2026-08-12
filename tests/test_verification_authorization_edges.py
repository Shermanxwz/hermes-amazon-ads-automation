from __future__ import annotations

from datetime import datetime
import json
import unittest

from helpers import Environment, READ_TARGET, WRITE_TARGET


class VerificationAuthorizationEdgeTests(unittest.TestCase):
    def setUp(self):
        self.env = Environment()
        self.store = self.env.store
        self.service = self.env.service

    def tearDown(self):
        self.env.close()

    def _ready_verifier(self):
        _cycle, task, decision = self.env.one_decision_task()
        self.service.bind_worker({
            "task_id": task["id"],
            "worker_session_id": "edge-exec",
            "role": "executor",
            "goal": f"[ads-task:{task['id']}] [ads-role:executor]",
        })
        authorization = self.service.authorize_tool({
            "tool_name": WRITE_TARGET["registered_name"],
            "args": {"targetId": "t1", "bid": 0.8},
            "session_id": "edge-exec",
            "tool_call_id": "edge-write",
        })
        self.assertTrue(authorization["allowed"], authorization)
        self.service.finish_tool({
            "tool_name": WRITE_TARGET["registered_name"],
            "result": {"success": [{"targetId": "t1"}], "error": []},
            "session_id": "edge-exec",
            "decision_id": decision["id"],
            "reservation_token": authorization["reservation_token"],
            "tool_call_id": "edge-write",
        })
        self.store.finish_worker("edge-exec", "completed", "write complete")
        self.service.bind_worker({
            "task_id": task["id"],
            "worker_session_id": "edge-verify",
            "role": "verifier",
            "goal": f"[ads-task:{task['id']}] [ads-role:verifier]",
        })
        read = self.service.finish_tool({
            "tool_name": READ_TARGET["registered_name"],
            "result": {"targetId": "t1", "bid": 0.8},
            "session_id": "edge-verify",
            "task_id": task["id"],
            "tool_call_id": "edge-read",
        })
        return task, decision, read["action_id"]

    def test_decision_and_session_are_required_after_evidence_id_parses(self):
        with self.assertRaisesRegex(ValueError, "decision_id and session_id"):
            self.service.verify_decision({"evidence_action_id": 1})

    def test_unknown_or_executor_session_cannot_verify(self):
        with self.assertRaisesRegex(ValueError, "only a bound verifier"):
            self.service.verify_decision({
                "decision_id": "missing",
                "session_id": "unknown-session",
                "evidence_action_id": 1,
            })
        _cycle, task, decision = self.env.one_decision_task()
        self.service.bind_worker({
            "task_id": task["id"],
            "worker_session_id": "only-executor",
            "role": "executor",
            "goal": f"[ads-task:{task['id']}] [ads-role:executor]",
        })
        with self.assertRaisesRegex(ValueError, "only a bound verifier"):
            self.service.verify_decision({
                "decision_id": decision["id"],
                "session_id": "only-executor",
                "evidence_action_id": 1,
            })

    def test_stale_task_verifier_binding_is_rejected(self):
        task, decision, action_id = self._ready_verifier()
        with self.store.connection() as conn:
            conn.execute(
                "UPDATE tasks SET verifier_session_id=? WHERE id=?",
                ("replacement-verifier", task["id"]),
            )
        with self.assertRaisesRegex(ValueError, "current verifier"):
            self.service.verify_decision({
                "decision_id": decision["id"],
                "session_id": "edge-verify",
                "evidence_action_id": action_id,
            })

    def test_foreign_decision_and_missing_action_are_rejected(self):
        _task, decision, _action_id = self._ready_verifier()
        with self.assertRaisesRegex(ValueError, "does not belong"):
            self.service.verify_decision({
                "decision_id": "foreign-decision",
                "session_id": "edge-verify",
                "evidence_action_id": 1,
            })
        with self.assertRaisesRegex(ValueError, "was not found"):
            self.service.verify_decision({
                "decision_id": decision["id"],
                "session_id": "edge-verify",
                "evidence_action_id": 999999,
            })

    def test_invalid_evidence_timestamp_fails_closed(self):
        _task, decision, action_id = self._ready_verifier()
        with self.store.connection() as conn:
            conn.execute(
                "UPDATE actions SET created_at='not-an-iso-timestamp' WHERE id=?",
                (action_id,),
            )
        with self.assertRaisesRegex(ValueError, "timestamps are invalid"):
            self.service.verify_decision({
                "decision_id": decision["id"],
                "session_id": "edge-verify",
                "evidence_action_id": action_id,
            })

    def test_naive_iso_timestamps_are_normalized_to_utc(self):
        _task, decision, action_id = self._ready_verifier()
        current = self.store.get_decision(decision["id"])
        action = self.store.get_action(action_id)
        executed = datetime.fromisoformat(current["executed_at"]).replace(tzinfo=None).isoformat()
        read_at = datetime.fromisoformat(action["created_at"]).replace(tzinfo=None).isoformat()
        with self.store.connection() as conn:
            conn.execute(
                "UPDATE decisions SET executed_at=? WHERE id=?",
                (executed, decision["id"]),
            )
            conn.execute(
                "UPDATE actions SET created_at=? WHERE id=?",
                (read_at, action_id),
            )
        verified = self.service.verify_decision({
            "decision_id": decision["id"],
            "session_id": "edge-verify",
            "evidence_action_id": action_id,
        })
        self.assertEqual(verified["status"], "verified")

    def test_legacy_field_after_payload_and_custom_message_are_supported(self):
        _task, decision, action_id = self._ready_verifier()
        payload = dict(self.store.get_decision(decision["id"])["payload"])
        payload.pop("expected_state", None)
        payload["field"] = "bid"
        payload["after"] = 0.8
        with self.store.connection() as conn:
            conn.execute(
                "UPDATE decisions SET payload_json=? WHERE id=?",
                (json.dumps(payload, ensure_ascii=False), decision["id"]),
            )
        verified = self.service.verify_decision({
            "decision_id": decision["id"],
            "session_id": "edge-verify",
            "evidence_action_id": action_id,
            "message": "verified through legacy field payload",
        })
        self.assertEqual(verified["status"], "verified")
        record = self.store.list_verifications(decision_id=decision["id"])[0]
        self.assertEqual(record["expected"], {"bid": 0.8})
        self.assertEqual(record["message"], "verified through legacy field payload")


if __name__ == "__main__":
    unittest.main()
