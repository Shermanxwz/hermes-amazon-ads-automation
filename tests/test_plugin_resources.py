from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "hermes-plugin" / "amazon_ads_control" / "resources.py"


def load():
    spec = importlib.util.spec_from_file_location("ads_resources_test", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ResourceProfileTests(unittest.TestCase):
    def test_small_and_upgraded_hosts_keep_features(self):
        module = load()
        with patch.dict(os.environ, {
            "ADS_CONTROL_CPU_COUNT": "2",
            "ADS_CONTROL_MEMORY_MB": "2048",
        }, clear=False), patch.object(module.os, "getloadavg", return_value=(0.5, 0.5, 0.5)):
            small = module.snapshot()
        self.assertEqual(small["tier"], "constrained")
        self.assertEqual(small["max_concurrent_profiles"], 1)
        self.assertFalse(small["feature_reduction"])

        with patch.dict(os.environ, {
            "ADS_CONTROL_CPU_COUNT": "2",
            "ADS_CONTROL_MEMORY_MB": "4096",
        }, clear=False), patch.object(module.os, "getloadavg", return_value=(0.5, 0.5, 0.5)):
            upgraded = module.snapshot()
        self.assertEqual(upgraded["tier"], "balanced")
        self.assertEqual(upgraded["max_concurrent_profiles"], 2)
        self.assertFalse(upgraded["feature_reduction"])

    def test_transient_pressure_serializes_nonurgent_work(self):
        module = load()
        with patch.dict(os.environ, {
            "ADS_CONTROL_CPU_COUNT": "4",
            "ADS_CONTROL_MEMORY_MB": "8192",
        }, clear=False), patch.object(module.os, "getloadavg", return_value=(6.0, 3.0, 2.0)):
            profile = module.snapshot()
        self.assertEqual(profile["max_concurrent_children"], 1)
        self.assertTrue(profile["defer_nonurgent_collection"])
        self.assertFalse(profile["feature_reduction"])


if __name__ == "__main__":
    unittest.main()
