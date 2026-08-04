from __future__ import annotations

import unittest

from amazon_ads_control.verification_hardening import (
    scoped_expected_differences,
    select_entity_scope,
)
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

    def test_empty_planned_identifier_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "no entity_id"):
            select_entity_scope({"targetId": "t1"}, "", "target")

    def test_numeric_generic_identifier_is_supported(self):
        entity, path = select_entity_scope(
            {"id": 42, "state": "ENABLED"},
            "42",
            None,
        )
        self.assertEqual(entity["state"], "ENABLED")
        self.assertEqual(path, "$")

    def test_generated_entity_type_alias_is_supported(self):
        entity, path = select_entity_scope(
            {"payload": [{"customThingId": "thing-1", "value": 3}]},
            "thing-1",
            "custom-thing",
        )
        self.assertEqual(entity["value"], 3)
        self.assertEqual(path, "$.payload[0]")

    def test_keyword_alias_can_identify_target_scope(self):
        entity, path = select_entity_scope(
            {"response": {"items": [{"keywordId": "kw-7", "bid": 0.55}]}},
            "kw-7",
            "target",
        )
        self.assertEqual(entity["bid"], 0.55)
        self.assertEqual(path, "$.response.items[0]")

    def test_non_container_values_are_ignored_during_recursive_search(self):
        entity, _path = select_entity_scope(
            {
                "metadata": [None, True, 17, "target-8"],
                "targets": [{"targetId": "target-8", "bid": 0.61}],
            },
            "target-8",
            "target",
        )
        self.assertEqual(entity["bid"], 0.61)

    def test_direct_entity_field_wins_over_nested_same_name(self):
        differences = scoped_expected_differences(
            {"bid": 0.8},
            {"targetId": "t1", "bid": 0.7, "recommendation": {"bid": 0.8}},
        )
        self.assertEqual(differences["bid"]["actual"], 0.7)
        self.assertEqual(differences["bid"]["path"], "$.bid")

    def test_multiple_nested_field_candidates_are_ambiguous(self):
        differences = scoped_expected_differences(
            {"bid": 0.8},
            {
                "targetId": "t1",
                "current": {"bid": 0.7},
                "recommendation": {"bid": 0.8},
            },
        )
        self.assertEqual(differences["bid"]["actual"], "[ambiguous]")
        self.assertEqual(len(differences["bid"]["paths"]), 2)

    def test_one_nested_field_is_accepted_when_direct_field_is_absent(self):
        differences = scoped_expected_differences(
            {"bid": 0.8},
            {"targetId": "t1", "current": {"bid": 0.8}},
        )
        self.assertEqual(differences, {})

    def test_nested_expected_object_is_compared_recursively(self):
        differences = scoped_expected_differences(
            {"settings": {"state": "ENABLED", "bid": 0.8}},
            {"settings": {"state": "ENABLED", "bid": 0.7}},
        )
        nested = differences["settings"]["differences"]
        self.assertEqual(nested["bid"]["actual"], 0.7)
        self.assertNotIn("state", nested)

    def test_invalid_or_missing_expected_field_fails_closed(self):
        differences = scoped_expected_differences(
            {"": 1, "budget": 10},
            {"targetId": "t1"},
        )
        self.assertEqual(differences[""]["actual"], "[invalid field]")
        self.assertEqual(differences["budget"]["actual"], "[missing]")

    def test_structured_values_require_exact_equality(self):
        self.assertEqual(
            scoped_expected_differences(
                {"rules": [{"type": "A", "value": 1}]},
                {"rules": [{"type": "A", "value": 1}]},
            ),
            {},
        )
        differences = scoped_expected_differences(
            {"rules": [{"type": "A", "value": 1}]},
            {"rules": [{"type": "A", "value": 2}]},
        )
        self.assertIn("rules", differences)


if __name__ == "__main__":
    unittest.main()
