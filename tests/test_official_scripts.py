from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from importlib.util import module_from_spec, spec_from_file_location
import io
import json
from pathlib import Path
import re
import tempfile
from unittest.mock import patch
import unittest

ROOT=Path(__file__).resolve().parents[1]

def load(name,path):
    spec=spec_from_file_location(name,ROOT/path); module=module_from_spec(spec); spec.loader.exec_module(module); return module

sync=load("sync", "scripts/sync_official_contracts.py")
fingerprint=load("fingerprint", "scripts/check_official_fingerprint.py")
secrets=load("secrets", "scripts/verify-no-secrets.py")

class OfficialScriptTests(unittest.TestCase):
    def test_sync_check_and_invalid_inputs(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"collection.json"
            path.write_text(json.dumps({"info":{},"item":[]}))
            with patch("sys.argv",["sync", "--source",str(path),"--check"]), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(sync.main(),1)
            path.write_text("not-json")
            with patch("sys.argv",["sync", "--source",str(path)]), redirect_stderr(io.StringIO()):
                self.assertEqual(sync.main(),2)

    def test_fingerprint_accepts_exact_manifest_and_rejects_drift(self):
        with tempfile.TemporaryDirectory() as td:
            manifest=Path(td)/"manifest.json"; baseline=Path(td)/"baseline.json"
            current={
                "semantic_sha256":"abc","request_count":2,
                "capabilities":{"profiles":True},"extended_capabilities":{"stores":True},
            }
            manifest.write_text(json.dumps(current)); baseline.write_text(json.dumps({"semantic_sha256":"abc","request_count":2}))
            with patch("sys.argv",["fingerprint","--manifest",str(manifest),"--baseline",str(baseline)]), redirect_stdout(io.StringIO()):
                self.assertEqual(fingerprint.main(),0)
            current["semantic_sha256"]="changed"; manifest.write_text(json.dumps(current))
            with patch("sys.argv",["fingerprint","--manifest",str(manifest),"--baseline",str(baseline)]), redirect_stderr(io.StringIO()):
                self.assertEqual(fingerprint.main(),1)
            manifest.write_text("bad")
            with patch("sys.argv",["fingerprint","--manifest",str(manifest),"--baseline",str(baseline)]), redirect_stderr(io.StringIO()):
                self.assertEqual(fingerprint.main(),2)

    def test_secret_scanner_checks_artifacts_binary_and_cli(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); (root/"safe.py").write_text('access_token="example"')
            (root/"binary.bin").write_bytes(b"\xff\xfe")
            external=root/".ci-hermes-agent"; external.mkdir()
            (external/"foreign.env").write_text("authorization: bearer " + "f"*32)
            artifacts=root/"artifacts"; artifacts.mkdir()
            report=artifacts/"report.txt"
            report.write_text("authorization: bearer " + "a"*32)
            hits=secrets.scan(root)
            self.assertEqual(hits[0],(Path("artifacts/report.txt"),1,"credential"))
            report.unlink()
            self.assertEqual(secrets.scan(root),[])
            (root/"bad.env").write_text("authorization: bearer " + "abcdefghijklmnopqrstuvwxyz" + "123456")
            hits=secrets.scan(root); self.assertEqual(hits[0][0],Path("bad.env"))
            with redirect_stderr(io.StringIO()): self.assertEqual(secrets.main(["--root",str(root)]),1)
            (root/"bad.env").unlink()
            with redirect_stdout(io.StringIO()): self.assertEqual(secrets.main(["--root",str(root)]),0)

    def test_full_sandbox_aggregates_failures_and_requires_browser(self):
        script=(ROOT/"scripts/run_full_sandbox.sh").read_text(encoding="utf-8")
        match=re.search(r"run_required\(\) \{(?P<body>.*?)\n\}",script,re.S)
        self.assertIsNotNone(match)
        body=match.group("body")
        self.assertIn("record FAIL",body)
        self.assertIn("return 0",body)
        self.assertNotIn('return "$code"',body)
        self.assertIn('run_required browser "Real Chromium Web and approval E2E"',script)
        self.assertNotIn("run_external_when_missing browser",script)
        self.assertIn('sys.exit(1 if overall == "FAIL" else 0)',script)
        self.assertIn("PYTHONWARNINGS=default",script)

    def test_nginx_preserves_browser_origin(self):
        nginx=(ROOT/"deploy/nginx.conf").read_text(encoding="utf-8")
        self.assertIn("proxy_set_header Origin $http_origin;",nginx)
        self.assertIn("proxy_set_header X-Real-IP $remote_addr;",nginx)
        self.assertNotIn("proxy_set_header Origin $scheme://$host;",nginx)

if __name__ == "__main__": unittest.main()
