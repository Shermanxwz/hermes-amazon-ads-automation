from __future__ import annotations

import unittest

from amazon_ads_control.api import Handler
from amazon_ads_control.extension_registry import EXTENSION_ORDER, installed_extensions


class ExtensionRegistryTests(unittest.TestCase):
    def test_all_extensions_install_once_in_declared_order(self):
        self.assertEqual(installed_extensions(), EXTENSION_ORDER)
        self.assertEqual(len(EXTENSION_ORDER), len(set(EXTENSION_ORDER)))

    def test_safety_sensitive_order_constraints_are_explicit(self):
        self.assertLess(
            EXTENSION_ORDER.index("regional_mcp"),
            EXTENSION_ORDER.index("structural_execution"),
        )
        self.assertLess(
            EXTENSION_ORDER.index("structural_hardening"),
            EXTENSION_ORDER.index("sealed_autonomy"),
        )
        self.assertLess(
            EXTENSION_ORDER.index("sealed_autonomy"),
            EXTENSION_ORDER.index("verification_hardening"),
        )
        self.assertLess(
            EXTENSION_ORDER.index("verification_hardening"),
            EXTENSION_ORDER.index("sealed_activation"),
        )
        self.assertEqual(EXTENSION_ORDER[-1], "sealed_activation")

    def test_http_version_matches_package_generation(self):
        self.assertEqual(Handler.server_version, "HermesAdsControl/4.0")


if __name__ == "__main__":
    unittest.main()
