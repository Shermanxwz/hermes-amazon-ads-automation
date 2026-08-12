from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from amazon_ads_control.db import Store

UTC = timezone.utc


def policy():
    return SimpleNamespace(
        retention_days=7,
        payload_retention_days=7,
        metric_retention_days=7,
        snapshot_retention_days=7,
        storage_soft_limit_mb=512,
        storage_hard_limit_mb=1024,
        min_free_disk_mb=128,
        vacuum_min_reclaim_mb=64,
    )


class StorageAlertRollupTests(unittest.TestCase):
    def test_old_orphaned_open_alerts_become_bounded_rollups(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "state.db")
            old = (datetime.now(UTC) - timedelta(days=30)).isoformat(timespec="seconds")
            for index in range(3):
                alert_id = store.alert(
                    "critical", "WRITE_VERIFICATION_MISMATCH", "p1",
                    f"deleted-task-{index}", f"deleted-decision-{index}",
                    f"historical mismatch {index}", {"index": index},
                )
                with store.connection() as conn:
                    conn.execute("UPDATE alerts SET created_at=? WHERE id=?", (old, alert_id))
            keep = store.alert("critical", "GLOBAL_KEEP", None, None, None, "global unresolved")
            result = store.maintain_storage(policy())
            self.assertEqual(result["archived_orphan_alerts"], 3)
            open_alerts = store.list_alerts(100)
            self.assertEqual([item["id"] for item in open_alerts], [keep])
            dashboard = store.dashboard()
            self.assertEqual(dashboard["alert_rollups"][0]["alert_count"], 3)
            self.assertEqual(dashboard["storage"]["row_counts"]["alert_rollups"], 1)


if __name__ == "__main__":
    unittest.main()
