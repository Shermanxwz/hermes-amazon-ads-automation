from __future__ import annotations

import json
from pathlib import Path
import tomllib
import unittest

from amazon_ads_control import __version__
from amazon_ads_control.api import Handler
from amazon_ads_control.extension_registry import EXTENSION_ORDER, installed_extensions


ROOT = Path(__file__).resolve().parents[1]


class ExtensionRegistryTests(unittest.TestCase):
    def test_all_extensions_install_once_in_declared_order(self):
        self.assertEqual(installed_extensions(), EXTENSION_ORDER)
        self.assertEqual(len(EXTENSION_ORDER), len(set(EXTENSION_ORDER)))

    def test_safety_sensitive_order_constraints_are_explicit(self):
        self.assertLess(EXTENSION_ORDER.index("regional_mcp"), EXTENSION_ORDER.index("structural_execution"))
        self.assertLess(EXTENSION_ORDER.index("structural_hardening"), EXTENSION_ORDER.index("sealed_autonomy"))
        self.assertLess(EXTENSION_ORDER.index("sealed_autonomy"), EXTENSION_ORDER.index("verification_hardening"))
        self.assertLess(EXTENSION_ORDER.index("verification_hardening"), EXTENSION_ORDER.index("sealed_activation"))
        self.assertLess(EXTENSION_ORDER.index("sealed_activation"), EXTENSION_ORDER.index("sealed_activation_trust"))
        self.assertLess(EXTENSION_ORDER.index("sealed_activation_trust"), EXTENSION_ORDER.index("sealed_activation_outcomes"))
        self.assertLess(EXTENSION_ORDER.index("sealed_activation_outcomes"), EXTENSION_ORDER.index("budget_guard"))
        self.assertLess(EXTENSION_ORDER.index("budget_guard"), EXTENSION_ORDER.index("budget_reservation"))
        self.assertEqual(EXTENSION_ORDER[-1], "budget_reservation")

    def test_http_version_matches_package_generation(self):
        major, minor, *_ = __version__.split(".")
        self.assertEqual(Handler.server_version, f"HermesAdsControl/{major}.{minor}")

    def test_release_identity_is_consistent(self):
        with (ROOT / "pyproject.toml").open("rb") as handle:
            project_version = tomllib.load(handle)["project"]["version"]
        manifest = json.loads((ROOT / "package-manifest.json").read_text(encoding="utf-8"))
        manifest_version = manifest["version"]
        self.assertEqual(project_version, manifest_version)
        self.assertEqual(__version__, manifest_version)
        current_state = (ROOT / "docs" / "current-state.md").read_text(encoding="utf-8")
        self.assertIn(f"Package release {manifest_version}", current_state)
        self.assertIn("sealed-operation/control-policy generation v6.1", current_state)


if __name__ == "__main__":
    unittest.main()
