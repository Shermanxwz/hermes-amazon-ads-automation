from __future__ import annotations

import hashlib
import unittest

from helpers import Environment, one_target_snapshot


class ReportingV3Tests(unittest.TestCase):
    def setUp(self):
        self.env = Environment(strict_writes=True)

    def tearDown(self):
        self.env.close()

    def spec(self):
        snapshot = one_target_snapshot()
        return snapshot, {
            "profile_id": "p1", "report_type": "retry-test",
            "start_date": snapshot["window"]["start"], "end_date": snapshot["window"]["end"],
            "timezone": "UTC", "ad_product": "SPONSORED_PRODUCTS",
        }

    def test_failed_report_requires_explicit_audited_retry(self):
        _snapshot, spec = self.spec()
        job = self.env.store.create_report_job(spec, "test")
        failed = self.env.store.transition_report(job["id"], "FAILED", {"error": "throttling exhausted"}, "test")
        self.assertEqual(failed["status"], "FAILED")
        same = self.env.store.create_report_job(spec, "test")
        self.assertEqual(same["status"], "FAILED")
        retried = self.env.store.create_report_job({**spec, "retry_failed": True}, "test")
        self.assertEqual(retried["id"], job["id"])
        self.assertEqual(retried["status"], "REQUESTED")
        self.assertEqual(retried["attempt_count"], 2)
        with self.env.store.connection() as conn:
            rows = conn.execute(
                "SELECT from_status,to_status FROM report_transitions WHERE report_job_id=? ORDER BY id",
                (job["id"],),
            ).fetchall()
        self.assertEqual((rows[-1]["from_status"], rows[-1]["to_status"]), ("FAILED", "REQUESTED"))

    def test_ingested_report_requires_all_content_evidence(self):
        _snapshot, spec = self.spec()
        job = self.env.store.create_report_job(spec, "test")
        report_id = "r-" + job["id"]
        self.env.store.transition_report(job["id"], "SUBMITTED", {"report_id": report_id}, "test")
        self.env.store.transition_report(job["id"], "SUCCEEDED", {"report_id": report_id}, "test")
        self.env.store.transition_report(job["id"], "DOWNLOADED", {"content_hash": hashlib.sha256(b"c").hexdigest()}, "test")
        self.env.store.transition_report(job["id"], "VALIDATED", {"schema_hash": hashlib.sha256(b"s").hexdigest()}, "test")
        with self.assertRaisesRegex(ValueError, "normalized_hash"):
            self.env.store.transition_report(job["id"], "INGESTED", {
                "content_hash": hashlib.sha256(b"c").hexdigest(),
                "schema_hash": hashlib.sha256(b"s").hexdigest(),
                "row_count": 1,
            }, "test")


if __name__ == "__main__":
    unittest.main()
