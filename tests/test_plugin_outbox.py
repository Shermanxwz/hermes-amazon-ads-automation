from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "hermes-plugin" / "amazon_ads_control" / "outbox.py"
UTC = timezone.utc


def load():
    spec = importlib.util.spec_from_file_location("ads_outbox_test", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OutboxTests(unittest.TestCase):
    def test_failed_delivery_is_durable_and_deduplicated(self):
        module = load()
        with tempfile.TemporaryDirectory() as td:
            old = os.environ.get("ADS_CONTROL_OUTBOX_PATH")
            os.environ["ADS_CONTROL_OUTBOX_PATH"] = str(Path(td) / "outbox.jsonl")
            try:
                payload = {
                    "tool_name": "mcp_amazon_ads_update_target", "tool_call_id": "call-1",
                    "decision_id": "d1", "reservation_token": "r1",
                    "result": {"success": [{"targetId": "1"}]},
                }
                first = module.deliver(payload, lambda _item: {"error": "down"})
                second = module.deliver(payload, lambda _item: {"error": "down"})
                self.assertTrue(first["queued"])
                self.assertEqual(first["event_id"], second["event_id"])
                self.assertEqual(module.pending_count(), 1)
                seen = []
                flushed = module.flush(lambda item: seen.append(item) or {"recorded": True})
                self.assertEqual(flushed["delivered"], 1)
                self.assertEqual(module.pending_count(), 0)
                self.assertEqual(seen[0]["event_id"], first["event_id"])
            finally:
                if old is None:
                    os.environ.pop("ADS_CONTROL_OUTBOX_PATH", None)
                else:
                    os.environ["ADS_CONTROL_OUTBOX_PATH"] = old

    def test_corrupt_file_is_quarantined_not_overwritten(self):
        module = load()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "outbox.jsonl"
            path.write_text("{not valid json\n", encoding="utf-8")
            with patch.dict(os.environ, {"ADS_CONTROL_OUTBOX_PATH": str(path)}, clear=False):
                event = module.enqueue({
                    "tool_name": "mcp_amazon_ads_update_target", "tool_call_id": "new",
                    "decision_id": "d", "reservation_token": "r", "result": {"ok": True},
                })
                self.assertIn("quarantined_corrupt_outbox", event)
                quarantined = Path(event["quarantined_corrupt_outbox"])
                self.assertTrue(quarantined.exists())
                self.assertEqual(quarantined.read_text(encoding="utf-8"), "{not valid json\n")
                self.assertEqual(module.pending_count(), 1)
                self.assertEqual(len(module.status()["corrupt_files"]), 1)

    def test_multiprocess_enqueues_do_not_lose_events(self):
        module = load()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "outbox.jsonl"
            script = (
                "import importlib.util,os,sys;"
                "spec=importlib.util.spec_from_file_location('worker_outbox',sys.argv[1]);"
                "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
                "os.environ['ADS_CONTROL_OUTBOX_PATH']=sys.argv[2];"
                "i=sys.argv[3];"
                "m.enqueue({'tool_name':'mcp_amazon_ads_update_target','tool_call_id':'c'+i,'decision_id':'d'+i,'reservation_token':'r'+i,'result':{'ok':True}})"
            )
            processes = [subprocess.Popen([sys.executable, "-c", script, str(MODULE), str(path), str(index)]) for index in range(8)]
            for process in processes:
                self.assertEqual(process.wait(timeout=20), 0)
            with patch.dict(os.environ, {"ADS_CONTROL_OUTBOX_PATH": str(path)}, clear=False):
                self.assertEqual(module.pending_count(), 8)
                rows = module._read(path)
                self.assertEqual(len({row["event_id"] for row in rows}), 8)

    def test_outbox_pressure_is_visible_before_more_amazon_operations(self):
        module = load()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "outbox.jsonl"
            with patch.dict(os.environ, {
                "ADS_CONTROL_OUTBOX_PATH": str(path),
                "ADS_CONTROL_OUTBOX_MAX_BYTES": "65536",
            }, clear=False):
                module.enqueue({
                    "tool_name": "mcp_amazon_ads_update_target", "tool_call_id": "large",
                    "decision_id": "d", "reservation_token": "r", "result": {"payload": "x" * 70000},
                })
                state = module.status()
                self.assertTrue(state["over_limit"])
                self.assertGreater(state["bytes"], state["max_bytes"])

    def test_corrupt_artifacts_are_bounded_and_old_files_are_removed(self):
        module = load()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "outbox.jsonl"
            old_timestamp = (datetime.now(UTC) - timedelta(days=10)).timestamp()
            for index in range(5):
                artifact = Path(str(path) + f".corrupt.{index}")
                artifact.write_text("broken", encoding="utf-8")
                os.utime(artifact, (old_timestamp, old_timestamp))
            with patch.dict(os.environ, {
                "ADS_CONTROL_OUTBOX_PATH": str(path),
                "ADS_CONTROL_OUTBOX_CORRUPT_KEEP": "2",
                "ADS_CONTROL_OUTBOX_CORRUPT_RETENTION_DAYS": "1",
            }, clear=False):
                result = module.maintenance()
                self.assertEqual(result["corrupt_files"], [])
                self.assertEqual(len(result["removed_corrupt_files"]), 5)


if __name__ == "__main__":
    unittest.main()
