from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import BytesIO
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

ROOT=Path(__file__).resolve().parents[1]

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

reach=load("reachability_test",ROOT/"scripts/check_amazon_mcp_reachability.py")
sync=load("official_sync_test",ROOT/"scripts/sync_official_contracts.py")
fingerprint=load("official_fingerprint_test",ROOT/"scripts/check_official_fingerprint.py")
secrets=load("secret_scan_test",ROOT/"scripts/verify-no-secrets.py")

class Response:
    def __init__(self,status,body=b""): self.status=status; self.body=body
    def __enter__(self): return self
    def __exit__(self,*_): return False
    def read(self,_limit=None): return self.body

class OfficialScriptTests(unittest.TestCase):
    def test_reachability_expected_and_unexpected_status(self):
        with patch.object(reach,"urlopen",side_effect=HTTPError("u",401,"unauthorized",{},BytesIO(b"no"))), redirect_stdout(io.StringIO()):
            self.assertEqual(reach.main(),0)
        with patch.object(reach,"urlopen",return_value=Response(200,b"ok")), redirect_stderr(io.StringIO()):
            self.assertEqual(reach.main(),1)
        with patch.object(reach,"urlopen",side_effect=URLError("offline")), redirect_stderr(io.StringIO()):
            self.assertEqual(reach.main(),2)

    def test_official_sync_main_success_missing_and_invalid(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"collection.json"; out=Path(td)/"manifest.json"
            folders=[]
            for name in ["Authentication OAuth","Profiles","Sponsored Products SP v3","Sponsored Brands SB v4","Sponsored Display","Reporting","Marketing Stream","Recommendations","Budget","Test Account","Exports"]:
                folders.append({"name":name,"item":[{"name":"call","request":{"method":"GET","url":"https://example/"+name}}]})
            path.write_text(json.dumps({"info":{"name":"Amazon Ads"},"item":folders}))
            with patch("sys.argv",["sync", "--source",str(path),"--output",str(out),"--check"]), redirect_stdout(io.StringIO()):
                self.assertEqual(sync.main(),0)
            self.assertTrue(out.exists())
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

    def test_secret_scanner_safe_hit_binary_and_cli(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); (root/"safe.py").write_text('access_token="example"')
            (root/"binary.bin").write_bytes(b"\xff\xfe")
            self.assertEqual(secrets.scan(root),[])
            (root/"bad.env").write_text("authorization: bearer " + "abcdefghijklmnopqrstuvwxyz" + "123456")
            hits=secrets.scan(root); self.assertEqual(hits[0][0],Path("bad.env"))
            with redirect_stderr(io.StringIO()): self.assertEqual(secrets.main(["--root",str(root)]),1)
            (root/"bad.env").unlink()
            with redirect_stdout(io.StringIO()): self.assertEqual(secrets.main(["--root",str(root)]),0)

if __name__ == "__main__": unittest.main()
