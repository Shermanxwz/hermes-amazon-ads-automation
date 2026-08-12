from __future__ import annotations

from copy import deepcopy
import unittest

from amazon_ads_control.reporting import snapshot_hash
from helpers import Environment, REPORT_CREATE, one_target_snapshot


class ReportEvidenceV3Tests(unittest.TestCase):
    def setUp(self):
        self.env = Environment(strict_writes=True)
        self.env.sync_basic_catalog()

    def tearDown(self):
        self.env.close()

    def test_report_states_require_same_session_recorded_action(self):
        snapshot = one_target_snapshot()
        window = snapshot["window"]
        job = self.env.service.create_report({"spec": {
            "profile_id": "p1", "report_type": "evidence-test",
            "start_date": window["start"], "end_date": window["end"],
            "timezone": "UTC", "ad_product": "SPONSORED_PRODUCTS",
        }})
        with self.assertRaisesRegex(ValueError, "evidence_action_id"):
            self.env.service.transition_report({"report_job_id": job["id"], "status": "SUBMITTED", "data": {"report_id": "report-1"}, "session_id": "main"})

        allowed = self.env.service.authorize_tool({
            "tool_name": REPORT_CREATE["registered_name"],
            "args": {"reportTypeId": "evidence-test"},
            "session_id": "main", "tool_call_id": "report-call",
        })
        self.assertTrue(allowed["allowed"])
        recorded = self.env.service.finish_tool({
            "tool_name": REPORT_CREATE["registered_name"],
            "args": {"reportTypeId": "evidence-test"},
            "result": {"reportId": "report-1", "status": "SUCCESS", "rows": [{"targetId": "t1"}]},
            "session_id": "main", "tool_call_id": "report-call",
        })
        action_id = recorded["action_id"]
        with self.assertRaisesRegex(ValueError, "current Hermes session"):
            self.env.service.transition_report({
                "report_job_id": job["id"], "status": "SUBMITTED", "evidence_action_id": action_id,
                "session_id": "other", "data": {"report_id": "report-1"},
            })

        for status, data in (
            ("SUBMITTED", {"report_id": "report-1"}),
            ("SUCCEEDED", {}),
            ("DOWNLOADED", {}),
        ):
            job = self.env.service.transition_report({
                "report_job_id": job["id"], "status": status,
                "evidence_action_id": action_id, "session_id": "main", "data": data,
            })
        evidence = self.env.service.report_evidence({"session_id": "main"})
        self.assertEqual(evidence["evidence"][0]["id"], action_id)
        self.assertTrue(job["content_hash"])

        with self.assertRaisesRegex(ValueError, "normalized snapshot"):
            self.env.service.transition_report({"report_job_id": job["id"], "status": "VALIDATED", "session_id": "main", "data": {}})
        job = self.env.service.transition_report({
            "report_job_id": job["id"], "status": "VALIDATED", "session_id": "main",
            "data": {"snapshot": snapshot},
        })
        self.assertEqual(job["normalized_hash"], snapshot_hash(snapshot))
        job = self.env.service.transition_report({
            "report_job_id": job["id"], "status": "INGESTED", "session_id": "main", "data": {},
        })
        self.assertEqual(job["status"], "INGESTED")
        lineage = {"report_job_ids": [job["id"]], "action_ids": [action_id], "normalized_hash": snapshot_hash(snapshot)}
        cycle = self.env.service.plan_cycle({"snapshot": snapshot, "lineage": lineage}, "main")
        self.assertEqual(cycle["lineage"]["action_ids"], [action_id])

        modified = deepcopy(snapshot); modified["targets"][0]["spend"] = 999
        modified_lineage = dict(lineage); modified_lineage["normalized_hash"] = snapshot_hash(modified)
        with self.assertRaisesRegex(ValueError, "hash|differs"):
            self.env.service.plan_cycle({"snapshot": modified, "lineage": modified_lineage}, "main")


if __name__ == "__main__":
    unittest.main()
