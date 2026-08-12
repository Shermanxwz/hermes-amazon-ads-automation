from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "us-only-daily-orchestrator.py"
SPEC = importlib.util.spec_from_file_location("us_only_daily_orchestrator", SCRIPT)
assert SPEC and SPEC.loader
orchestrator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(orchestrator)


class OrchestratorV421Tests(unittest.TestCase):
    def test_source_has_no_direct_amazon_or_database_authority(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in (
            "advertising-ai.amazon.com",
            "AMAZON_ADS_MCP_ACCESS_TOKEN",
            "import sqlite3",
            "record_action(",
            "normalized_snapshot_gzip",
            "component': 'hermes-plugin",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn("ads_control_status", orchestrator.PROMPT)
        self.assertIn("different read-only Verifier", orchestrator.PROMPT)
        self.assertIn("HERMES-SP-EXP-*", orchestrator.PROMPT)

    def test_command_selects_configured_hermes_profile_without_account_ids(self):
        with mock.patch.dict(os.environ, {"HERMES_BIN": sys.executable, "HERMES_PROFILE": "seller-us"}, clear=False):
            command = orchestrator._hermes_command()
        self.assertEqual(command[0], sys.executable)
        self.assertIn("--profile", command)
        self.assertIn("seller-us", command)
        self.assertIn("-z", command)
        prompt = command[-1]
        self.assertNotRegex(prompt, r"\b\d{14,20}\b")
        self.assertNotIn("amzn1.ads-account", prompt)

    def test_success_returns_zero_and_suppresses_model_output(self):
        completed = subprocess.CompletedProcess([sys.executable], 0, stdout="private", stderr="private")
        with mock.patch.dict(os.environ, {"HERMES_BIN": sys.executable, "HERMES_PROFILE": ""}, clear=False), \
             mock.patch.object(orchestrator.subprocess, "run", return_value=completed) as run:
            self.assertEqual(orchestrator.main(), 0)
        kwargs = run.call_args.kwargs
        self.assertIs(kwargs["stdout"], subprocess.DEVNULL)
        self.assertIs(kwargs["stderr"], subprocess.DEVNULL)
        self.assertTrue(kwargs["check"] is False)

    def test_nonzero_hermes_exit_fails_closed(self):
        completed = subprocess.CompletedProcess([sys.executable], 17)
        with mock.patch.dict(os.environ, {"HERMES_BIN": sys.executable}, clear=False), \
             mock.patch.object(orchestrator.subprocess, "run", return_value=completed):
            self.assertEqual(orchestrator.main(), 17)

    def test_timeout_fails_closed(self):
        with mock.patch.dict(os.environ, {"HERMES_BIN": sys.executable}, clear=False), \
             mock.patch.object(orchestrator.subprocess, "run", side_effect=subprocess.TimeoutExpired("hermes", 1)):
            self.assertEqual(orchestrator.main(), 124)

    def test_missing_cli_fails_closed(self):
        with mock.patch.dict(os.environ, {"HERMES_BIN": "/definitely/missing/hermes"}, clear=False), \
             mock.patch.object(orchestrator.shutil, "which", return_value=None):
            self.assertEqual(orchestrator.main(), 1)


if __name__ == "__main__":
    unittest.main()
