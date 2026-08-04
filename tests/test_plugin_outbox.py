from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "hermes-plugin" / "amazon_ads_control" / "outbox.py"


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
                    "tool_name": "mcp_amazon_ads_update_target",
                    "tool_call_id": "call-1",
                    "decision_id": "d1",
                    "reservation_token": "r1",
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


if __name__ == "__main__":
    unittest.main()
