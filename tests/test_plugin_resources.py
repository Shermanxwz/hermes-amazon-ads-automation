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
        with patch.dict(os.environ, {"ADS_CONTROL_CPU_COUNT": "2", "ADS_CONTROL_MEMORY_MB": "2048"}, clear=False), patch.object(module.os, "getloadavg", return_value=(0.5, 0.5, 0.5)):
            small = module.snapshot()
        self.assertEqual(small["tier"], "constrained")
        self.assertEqual(small["max_concurrent_profiles"], 1)
        self.assertFalse(small["feature_reduction"])

        with patch.dict(os.environ, {"ADS_CONTROL_CPU_COUNT": "2", "ADS_CONTROL_MEMORY_MB": "4096"}, clear=False), patch.object(module.os, "getloadavg", return_value=(0.5, 0.5, 0.5)):
            upgraded = module.snapshot()
        self.assertEqual(upgraded["tier"], "balanced")
        self.assertEqual(upgraded["max_concurrent_profiles"], 2)
        self.assertFalse(upgraded["feature_reduction"])

    def test_transient_pressure_serializes_nonurgent_work(self):
        module = load()
        with patch.dict(os.environ, {"ADS_CONTROL_CPU_COUNT": "4", "ADS_CONTROL_MEMORY_MB": "8192"}, clear=False), patch.object(module.os, "getloadavg", return_value=(6.0, 3.0, 2.0)):
            profile = module.snapshot()
        self.assertEqual(profile["max_concurrent_children"], 1)
        self.assertTrue(profile["defer_nonurgent_collection"])
        self.assertFalse(profile["feature_reduction"])

    def test_cgroup_limits_override_larger_host(self):
        module = load()
        def read(path):
            return {
                "/sys/fs/cgroup/memory.max": str(2 * 1024 * 1024 * 1024),
                "/sys/fs/cgroup/cpu.max": "100000 100000",
            }.get(path, "")
        with patch.dict(os.environ, {}, clear=True), patch.object(module, "_read_text", side_effect=read), patch.object(module, "_host_memory_mb", return_value=8192), patch.object(module.os, "cpu_count", return_value=8), patch.object(module.os, "getloadavg", return_value=(0.25, 0.2, 0.1)):
            profile = module.snapshot()
        self.assertEqual(profile["memory_total_mb"], 2048)
        self.assertEqual(profile["memory_limit_source"], "cgroup")
        self.assertEqual(profile["cpu_count"], 1)
        self.assertEqual(profile["cpu_limit_source"], "cgroup")
        self.assertEqual(profile["tier"], "constrained")


if __name__ == "__main__":
    unittest.main()
