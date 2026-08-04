from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
import io

from amazon_ads_control.backtest import replay_cases
from helpers import one_target_snapshot

from amazon_ads_control import backtest_cli as cli


class BacktestTests(unittest.TestCase):
    def test_replay_labels_and_no_causal_claim(self):
        report=replay_cases([
            {"id":"waste","snapshot":one_target_snapshot(),"expected_rule_ids":["ADS-TARGET-WASTE"]},
            {"id":"bad-label","snapshot":one_target_snapshot(),"forbidden_rule_ids":["ADS-TARGET-WASTE"]},
        ])
        self.assertEqual(report["summary"]["cases"],2)
        self.assertEqual(report["summary"]["failed"],1)
        self.assertFalse(report["causal_claim"])
    def test_invalid_case(self):
        with self.assertRaisesRegex(ValueError,"snapshot"):
            replay_cases([{}])
    def test_cli_json_and_jsonl(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"cases.json"
            path.write_text(json.dumps([{"snapshot":one_target_snapshot(),"expected_rule_ids":["ADS-TARGET-WASTE"]}],default=str))
            out=io.StringIO()
            with redirect_stdout(out): self.assertEqual(cli.main([str(path),"--fail-on-label-mismatch"]),0)
            self.assertEqual(json.loads(out.getvalue())["summary"]["passed"],1)
            path.write_text("{}")
            with redirect_stderr(io.StringIO()): self.assertEqual(cli.main([str(path)]),2)

if __name__ == "__main__": unittest.main()
