from __future__ import annotations

import unittest

from amazon_ads_control.db import Store
from amazon_ads_control.service import ControlService


class PatchCompositionTests(unittest.TestCase):
    """Lock critical runtime owners so import-order changes cannot weaken policy."""

    def test_final_control_service_safety_owners(self):
        self.assertEqual(
            ControlService.sync_catalog.__module__,
            "amazon_ads_control.catalog_region_hardening",
        )
        # The budget guard is intentionally the outermost service write guard.
        # It first delegates to the previously sealed autonomy guard and can
        # only narrow permission further when financial exposure would increase.
        self.assertEqual(
            ControlService._guardrail_check.__module__,
            "amazon_ads_control.budget_guard",
        )
        self.assertEqual(
            ControlService.verify_decision.__module__,
            "amazon_ads_control.verification_hardening",
        )

    def test_store_safety_owners(self):
        self.assertEqual(
            Store.sync_catalog.__module__,
            "amazon_ads_control.regional_mcp",
        )
        # The compatibility hardening layer is intentionally the outermost
        # reservation owner. It serializes the financial precheck, then
        # delegates to the already-sealed approval/CAS/entity-cooldown chain.
        # The legacy budget extension must never replace that chain again.
        self.assertEqual(
            Store.reserve_decision.__module__,
            "amazon_ads_control.budget_reservation_compat",
        )


if __name__ == "__main__":
    unittest.main()
