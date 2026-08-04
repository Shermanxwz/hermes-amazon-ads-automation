from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from amazon_ads_control import __version__
from amazon_ads_control.client import ControlClient
from amazon_ads_control.config import Settings, _bool, _int
from amazon_ads_control.db import Store
from amazon_ads_control.security import hash_password
from amazon_ads_control import server as server_module

ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


control_cli = load_script("control_cli_test", ROOT / "scripts" / "control_cli.py")


class ConfigTests(unittest.TestCase):
    def test_strict_bool_and_int(self):
        with patch.dict(os.environ, {"X_BOOL": "yes", "X_INT": "42"}, clear=False):
            self.assertTrue(_bool("X_BOOL", False)); self.assertEqual(_int("X_INT", 1, 1, 100), 42)
        with patch.dict(os.environ, {"X_BOOL": "maybe"}, clear=False):
            with self.assertRaisesRegex(ValueError, "boolean"): _bool("X_BOOL", False)
        with patch.dict(os.environ, {"X_INT": "x"}, clear=False):
            with self.assertRaisesRegex(ValueError, "integer"): _int("X_INT", 1, 1, 100)
        with patch.dict(os.environ, {"X_INT": "101"}, clear=False):
            with self.assertRaisesRegex(ValueError, "between"): _int("X_INT", 1, 1, 100)

    def test_environment_validation(self):
        base = {"ADS_CONTROL_HOST": "127.0.0.1", "ADS_CONTROL_PORT": "8790", "ADS_CONTROL_PUBLIC_ORIGIN": "https://ads.example.com", "ADS_CONTROL_AGENT_TOKEN": "x" * 48, "ADS_CONTROL_PASSWORD_HASH": hash_password("correct horse battery staple")}
        with patch.dict(os.environ, base, clear=True):
            settings = Settings.from_env(); settings.validate_runtime(); self.assertEqual(settings.public_origin, "https://ads.example.com")
        with patch.dict(os.environ, {**base, "ADS_CONTROL_HOST": "0.0.0.0"}, clear=True):
            with self.assertRaisesRegex(ValueError, "non-loopback"): Settings.from_env()
        with patch.dict(os.environ, {**base, "ADS_CONTROL_PUBLIC_ORIGIN": "https://ads.example.com/path"}, clear=True):
            with self.assertRaisesRegex(ValueError, "origin"): Settings.from_env()
        with patch.dict(os.environ, {**base, "ADS_CONTROL_AGENT_TOKEN": " short "}, clear=True):
            with self.assertRaisesRegex(ValueError, "AGENT_TOKEN"): Settings.from_env().validate_runtime()

    def test_server_help_version_and_check(self):
        self.assertEqual(__version__, "3.0.0")
        with self.assertRaises(SystemExit) as ctx, redirect_stdout(io.StringIO()): server_module.main(["--help"])
        self.assertEqual(ctx.exception.code, 0)
        with tempfile.TemporaryDirectory() as td:
            env = {"ADS_CONTROL_HOST": "127.0.0.1", "ADS_CONTROL_PORT": "8790", "ADS_CONTROL_DB": str(Path(td) / "state.db"), "ADS_CONTROL_AGENT_TOKEN": "x" * 48, "ADS_CONTROL_PASSWORD_HASH": hash_password("correct horse battery staple")}
            output = io.StringIO()
            with patch.dict(os.environ, env, clear=True), redirect_stdout(output): self.assertEqual(server_module.main(["--check"]), 0)
            self.assertTrue(json.loads(output.getvalue())["ok"])


class ControlCliTests(unittest.TestCase):
    def test_generate_token_and_password_errors(self):
        output = io.StringIO()
        with redirect_stdout(output): self.assertEqual(control_cli.main(["generate-token"]), 0)
        self.assertGreaterEqual(len(output.getvalue().strip()), 48)
        with patch("getpass.getpass", side_effect=["a" * 14, "b" * 14]), redirect_stderr(io.StringIO()): self.assertEqual(control_cli.main(["hash-password"]), 2)
        with patch("getpass.getpass", side_effect=["short", "short"]), redirect_stderr(io.StringIO()): self.assertEqual(control_cli.main(["hash-password"]), 2)

    def test_verify_and_backup_database(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "state.db"; backup = Path(td) / "backup.db"; Store(db).event("info", "test", "test", None, "hello", {})
            out = io.StringIO()
            with redirect_stdout(out): self.assertEqual(control_cli.main(["verify-database", "--database", str(db), "--full"]), 0)
            self.assertTrue(json.loads(out.getvalue())["ok"])
            out = io.StringIO()
            with redirect_stdout(out): self.assertEqual(control_cli.main(["backup", "--database", str(db), "--output", str(backup)]), 0)
            result = json.loads(out.getvalue()); self.assertEqual(Path(result["path"]), backup); self.assertEqual(backup.stat().st_mode & 0o777, 0o600); self.assertEqual(len(Store(backup).list_events()), 1)


class _FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args): return
    def do_GET(self):
        if self.path == "/ok": body, status = b'{"ok":true}', 200
        elif self.path == "/list": body, status = b'[]', 200
        elif self.path == "/invalid": body, status = b'not-json', 200
        elif self.path == "/error-json": body, status = b'{"error":"bad"}', 400
        else: body, status = b'bad gateway', 502
        self.send_response(status); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)


class ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler); cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True); cls.thread.start(); cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
    @classmethod
    def tearDownClass(cls): cls.server.shutdown(); cls.server.server_close(); cls.thread.join()
    def test_control_client_response_handling(self):
        client = ControlClient(self.base, "x" * 48)
        self.assertTrue(client.request("GET", "/ok")["ok"]); self.assertEqual(client.request("GET", "/list")["error"], "invalid_control_response"); self.assertEqual(client.request("GET", "/invalid")["error"], "invalid_control_response"); self.assertEqual(client.request("GET", "/error-json")["http_status"], 400); self.assertEqual(client.request("GET", "/error-text")["http_status"], 502)
    def test_control_client_unavailable(self): self.assertEqual(ControlClient("http://127.0.0.1:1", "x" * 48, timeout=0.1).request("GET", "/")["error"], "control_plane_unavailable")


class WorkerCliTests(unittest.TestCase):
    def test_help_version_and_missing_token(self):
        from amazon_ads_control import worker
        with self.assertRaises(SystemExit) as ctx, redirect_stdout(io.StringIO()): worker.main(["--help"])
        self.assertEqual(ctx.exception.code, 0)
        with self.assertRaises(SystemExit) as ctx, redirect_stdout(io.StringIO()): worker.main(["--version"])
        self.assertEqual(ctx.exception.code, 0)
        with patch.dict(os.environ, {}, clear=True), redirect_stderr(io.StringIO()): self.assertEqual(worker.main(["--once"]), 2)
    def test_once_propagates_heartbeat_failure(self):
        from amazon_ads_control import worker
        with patch.dict(os.environ, {"ADS_CONTROL_AGENT_TOKEN":"x"*48}, clear=True), patch.object(worker.ControlClient,"request",return_value={"error":"down"}), redirect_stderr(io.StringIO()): self.assertEqual(worker.main(["--once"]), 2)


if __name__ == "__main__": unittest.main()
