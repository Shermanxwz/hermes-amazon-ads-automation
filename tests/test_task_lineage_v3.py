from __future__ import annotations

import unittest

from amazon_ads_control.strategy import OptimizationEngine, StrategyPolicy
from helpers import Environment, one_target_snapshot


class TaskLineageV3Tests(unittest.TestCase):
    def setUp(self):
        self.env = Environment(strict_writes=True)

    def tearDown(self):
        self.env.close()

    def test_cycle_committed_before_lineage_attachment_cannot_create_task(self):
        snapshot = one_target_snapshot()
        plan = OptimizationEngine().plan(snapshot, StrategyPolicy())
        orphan = self.env.store.create_cycle(
            profile=plan.profile,
            source=snapshot["source"],
            window=plan.window,
            data_quality=plan.data_quality,
            kpis=plan.kpis,
            snapshot=snapshot,
            decisions=[item.as_dict() for item in plan.decisions],
            created_by="crash-simulation",
        )
        self.assertEqual(orphan.get("lineage"), {})
        with self.assertRaisesRegex(ValueError, "persistent report lineage"):
            self.env.service.create_task({"cycle_id": orphan["id"]}, "main")


if __name__ == "__main__":
    unittest.main()
