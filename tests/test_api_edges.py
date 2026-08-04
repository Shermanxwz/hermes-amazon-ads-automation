from __future__ import annotations

from http.cookiejar import CookieJar
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

from amazon_ads_control.api import MAX_BODY, build_server
from amazon_ads_control.config import Settings
from amazon_ads_control.security import hash_password


class ApiEdgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.password = "correct horse battery staple"
        self.settings = Settings(host="127.0.0.1", port=0, db_path=Path(self.temp.name)/"state.db", public_origin="http://127.0.0.1", control_password_hash=hash_password(self.password), agent_token="a"*48, session_ttl_seconds=3600, max_sessions=4, retention_days=30, allow_remote_bind=False)
        self.server = build_server(self.settings)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.port = self.server.server_address[1]; self.base = f"http://127.0.0.1:{self.port}"
        self.jar = CookieJar(); self.browser = build_opener(HTTPCookieProcessor(self.jar)); self.csrf = ""

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.temp.cleanup()

    def request(self, path, method="GET", data=None, headers=None, opener=None):
        body = None if data is None else json.dumps(data).encode()
        req = Request(self.base+path, data=body, method=method, headers={"Content-Type":"application/json", **(headers or {})})
        try:
            with (opener or build_opener()).open(req) as r:
                return r.status, json.loads(r.read().decode()), r.headers
        except HTTPError as e:
            return e.code, json.loads(e.read().decode()), e.headers

    def login(self):
        status, data, _ = self.request("/api/login", "POST", {"password": self.password}, opener=self.browser)
        self.assertEqual(status, 200); self.csrf = data["csrf"]

    def raw(self, request: bytes) -> bytes:
        with socket.create_connection(("127.0.0.1", self.port), timeout=3) as sock:
            sock.sendall(request); sock.shutdown(socket.SHUT_WR)
            chunks=[]
            while True:
                part=sock.recv(65536)
                if not part: break
                chunks.append(part)
            return b"".join(chunks)

    def test_security_headers_static_and_ready(self):
        with build_opener().open(self.base+"/") as r:
            self.assertEqual(r.headers["X-Frame-Options"], "DENY")
            self.assertIn("object-src 'none'", r.headers["Content-Security-Policy"])
            self.assertEqual(r.headers["Cross-Origin-Opener-Policy"], "same-origin")
        status, data, headers = self.request("/health/ready")
        self.assertEqual(status, 200); self.assertTrue(data["database"]["ok"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        status, data, _ = self.request("/static/../README.md")
        self.assertEqual(status, 404); self.assertEqual(data["error"], "not_found")

    def test_malformed_http_bodies_fail_closed(self):
        cases = [
            b"POST /api/login HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: nope\r\n\r\n{}",
            b"POST /api/login HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}",
            b"POST /api/login HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: 1\r\n\r\n[",
            b"POST /api/login HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n[]",
            f"POST /api/login HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: {MAX_BODY+1}\r\n\r\n".encode(),
        ]
        for raw in cases:
            response = self.raw(raw)
            self.assertIn(b" 400 ", response.split(b"\r\n",1)[0])

    def test_auth_origin_logout_and_limits(self):
        self.login()
        status, _, _ = self.request("/api/settings", "PUT", {"mode":"paused"}, {"Origin":"http://evil", "X-CSRF-Token":self.csrf}, self.browser)
        self.assertEqual(status, 403)
        status, data, _ = self.request("/api/cycles?limit=garbage", opener=self.browser)
        self.assertEqual(status, 200); self.assertIn("cycles", data)
        status, _, headers = self.request("/api/logout", "POST", {}, {"Origin":"http://127.0.0.1", "X-CSRF-Token":self.csrf}, self.browser)
        self.assertEqual(status, 200); self.assertIn("Max-Age=0", headers["Set-Cookie"])
        self.assertEqual(self.request("/api/dashboard", opener=self.browser)[0], 401)

    def test_unknown_agent_and_browser_routes(self):
        self.assertEqual(self.request("/api/agent/missing", "POST", {}, {"Authorization":"Bearer "+"a"*48})[0], 404)
        self.login()
        self.assertEqual(self.request("/api/missing", opener=self.browser)[0], 404)
        self.assertEqual(self.request("/api/missing", "PUT", {}, {"Origin":"http://127.0.0.1", "X-CSRF-Token":self.csrf}, self.browser)[0], 404)

    def test_internal_errors_are_redacted(self):
        self.login()
        original = self.server.RequestHandlerClass.app.store.update_settings
        self.server.RequestHandlerClass.app.store.update_settings = lambda _data: (_ for _ in ()).throw(RuntimeError("sensitive stack detail"))
        try:
            status, data, _ = self.request("/api/settings", "PUT", {"mode":"observe"}, {"Origin":"http://127.0.0.1", "X-CSRF-Token":self.csrf}, self.browser)
            self.assertEqual(status, 500); self.assertEqual(data, {"error":"internal_error"})
        finally:
            self.server.RequestHandlerClass.app.store.update_settings = original
        self.assertTrue(any(e["type"] == "api.put_error" for e in self.server.RequestHandlerClass.app.store.list_events()))


if __name__ == "__main__":
    unittest.main()
