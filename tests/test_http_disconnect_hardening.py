from __future__ import annotations

import unittest

from amazon_ads_control.api import Handler


class _BrokenWriter:
    def write(self, _body: bytes) -> None:
        raise BrokenPipeError("client disconnected")


class HttpDisconnectHardeningTests(unittest.TestCase):
    @staticmethod
    def handler() -> Handler:
        handler = object.__new__(Handler)
        handler.close_connection = False
        handler.wfile = _BrokenWriter()
        handler.send_response = lambda _status: None
        handler.send_header = lambda _key, _value: None
        handler.end_headers = lambda: None
        handler._security_headers = lambda: None
        return handler

    def test_json_response_disconnect_is_not_raised(self):
        handler = self.handler()
        handler._respond(200, {"ok": True})
        self.assertTrue(handler.close_connection)

    def test_static_response_disconnect_is_not_raised(self):
        handler = self.handler()
        handler._static("index.html")
        self.assertTrue(handler.close_connection)


if __name__ == "__main__":
    unittest.main()
