from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import unittest

from amazon_ads_control.db import Store
from helpers import Environment, one_target_snapshot

UTC = timezone.utc


def policy(**overrides):
    values = {
        "retention_days": 180,
        "payload_retention_days": 30,
        "metric_retention_days": 60,
        "snapshot_retention_days": 45,
        "storage_soft_limit_mb": 512,
        "storage_hard_limit_mb": 1024,
        "min_free_disk_mb": 128,
        "vacuum_min_reclaim_mb": 64,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class StorageMaintenanceTests(unittest.TestCase):
    def test_large_action_payload_is_bounded_but_keeps_report_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "state.db")
            action_id = store.record_action(
                decision_id=None,
                task_id=None,
                session_id="main",
                actor_role="main",
                phase="after",
                tool_name="mcp_amazon_ads_reporting_create_report",
                operation="job",
                allowed=True,
                args={"reportTypeId": "spTargeting"},
                success=True,
                outcome_status="success",
                structured_result=True,
                result={"reportId": "report-123", "rows": [{"value": "x" * 300000}]},
            )
            action = store.get_action(action_id)
            self.assertTrue(action["result"]["_compacted"])
            self.assertEqual(action["result"]["report_id"], "report-123")
            self.assertTrue(action["stored_result_hash"])
            self.assertLess(len(json.dumps(action["result"])), 10000)

    def test_report_snapshot_is_compressed_once_and_lineage_still_works(self):
        env = Environment(strict_writes=True)
        try:
            snapshot = one_target_snapshot()
            lineage = env.lineage_for(snapshot)
            with env.store.connection() as conn:
                row = conn.execute(
                    "SELECT normalized_snapshot_json,normalized_snapshot_gzip FROM report_jobs WHERE id=?",
                    (lineage["report_job_ids"][0],),
                ).fetchone()
                transition = conn.execute(
                    "SELECT data_json FROM report_transitions WHERE report_job_id=? AND to_status='VALIDATED'",
                    (lineage["report_job_ids"][0],),
                ).fetchone()
            self.assertIsNone(row["normalized_snapshot_json"])
            self.assertGreater(len(row["normalized_snapshot_gzip"]), 0)
            self.assertNotIn('"snapshot"', transition["data_json"])
            cycle = env.service.plan_cycle({"snapshot": snapshot, "lineage": lineage}, "main")
            self.assertEqual(cycle["lineage"]["report_job_ids"], lineage["report_job_ids"])
            public = env.store.get_report_job(lineage["report_job_ids"][0])
            self.assertNotIn("normalized_snapshot_gzip", public)
            self.assertTrue(public["normalized_snapshot_stored"])
        finally:
            env.close()

    def test_periodic_maintenance_compacts_old_payloads_and_metric_rows(self):
        env = Environment(strict_writes=True)
        try:
            snapshot = one_target_snapshot()
            cycle = env.service.plan_cycle({"snapshot": snapshot, "lineage": env.lineage_for(snapshot)}, "main")
            action_id = env.store.record_action(
                decision_id=None,
                task_id=None,
                session_id="old",
                actor_role="main",
                phase="after",
                tool_name="mcp_amazon_ads_reporting_query_report",
                operation="read",
                allowed=True,
                args={"reportId": "r"},
                structured_result=True,
                result={"reportId": "r", "payload": "y" * 10000},
            )
            old = (datetime.now(UTC) - timedelta(days=20)).isoformat(timespec="seconds")
            with env.store.connection() as conn:
                conn.execute("UPDATE actions SET created_at=? WHERE id=?", (old, action_id))
                conn.execute("UPDATE cycles SET completed_at=? WHERE id=?", (old, cycle["id"]))
            result = env.store.maintain_storage(policy(payload_retention_days=7, metric_retention_days=7))
            action = env.store.get_action(action_id)
            with env.store.connection() as conn:
                metric_count = conn.execute("SELECT COUNT(*) FROM metric_rows WHERE cycle_id=?", (cycle["id"],)).fetchone()[0]
            self.assertTrue(action["compacted_at"])
            self.assertIsNone(action["result"])
            self.assertTrue(action["stored_result_hash"])
            self.assertEqual(metric_count, 0)
            self.assertGreaterEqual(result["compacted"]["action_payloads"], 1)
            self.assertGreaterEqual(result["compacted"]["metric_rows"], 1)
            self.assertIn("storage", env.store.dashboard())
        finally:
            env.close()

    def test_hard_storage_pressure_pauses_new_writes_without_deleting_open_alert(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "state.db")
            store.update_settings({"mode": "autopilot", "execution_enabled": True})
            alert_id = store.alert("critical", "KEEP_ME", None, None, None, "unresolved")
            result = store.maintain_storage(policy(
                storage_soft_limit_mb=0.000001,
                storage_hard_limit_mb=0.000002,
                min_free_disk_mb=1,
                vacuum_min_reclaim_mb=999999,
            ))
            settings = store.get_settings()
            self.assertEqual(result["pressure_after"], "hard")
            self.assertEqual(settings["mode"], "paused")
            self.assertFalse(settings["execution_enabled"])
            self.assertTrue(any(item["id"] == alert_id for item in store.list_alerts(100)))
            self.assertTrue(any(item["code"] == "STORAGE_HARD_LIMIT" for item in store.list_alerts(100)))


if __name__ == "__main__":
    unittest.main()
