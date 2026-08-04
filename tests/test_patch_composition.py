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
        self.assertEqual(
            ControlService._guardrail_check.__module__,
            "amazon_ads_control.regional_mcp",
        )
        self.assertEqual(
            ControlService.verify_decision.__module__,
            "amazon_ads_control.verification_hardening",
        )

    def test_store_catalog_drift_owner_is_regional_layer(self):
        self.assertEqual(
            Store.sync_catalog.__module__,
            "amazon_ads_control.regional_mcp",
        )


if __name__ == "__main__":
    unittest.main()
