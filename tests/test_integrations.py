import importlib.util
import json
from pathlib import Path
import unittest
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


postman = load("postman_sync", ROOT / "scripts/sync_official_contracts.py")
stream = load("stream_relay", ROOT / "integrations/marketing_stream_relay.py")


class IntegrationUtilityTests(unittest.TestCase):
    def test_postman_summary_detects_required_capabilities(self):
        folders = []
        for name in ["Authentication OAuth", "Profiles", "Sponsored Products SP v3", "Sponsored Brands SB v4", "Sponsored Display", "Reporting", "Amazon Marketing Stream", "Recommendations", "Budget rules", "Test accounts", "Exports"]:
            folders.append({"name": name, "item": [{"name": "call", "request": {"method": "GET", "url": {"raw": "https://example/" + name}}}]})
        raw = json.dumps({"info": {"name": "Amazon Ads API", "schema": "x"}, "variable": [{"key": "accessToken"}, {"key": "clientId"}], "item": folders}).encode()
        summary = postman.summarize(raw, "fixture")
        self.assertTrue(all(summary["capabilities"].values())); self.assertEqual(summary["request_count"], 11)

    def test_postman_authentication_detected_from_request_contract(self):
        raw = json.dumps({"info": {"name": "Amazon Ads API"}, "item": [{"name": "Profiles", "item": [{"name": "List", "request": {"method": "GET", "url": "https://example/profiles", "auth": {"type": "bearer", "bearer": [{"key": "token", "value": "{{accessToken}}"}]}, "header": [{"key": "Amazon-Advertising-API-ClientId", "value": "{{clientId}}"}]}}]}]}).encode()
        summary = postman.summarize(raw, "fixture")
        self.assertTrue(summary["capabilities"]["authentication"]); self.assertNotIn("accessToken", json.dumps(summary.get("examples", [])))

    def test_postman_walk_nested(self):
        raw = json.dumps({"info": {}, "item": [{"name": "A", "item": [{"name": "B", "item": [{"name": "C", "request": {"method": "POST", "url": "u"}}]}]}]}).encode()
        row = list(postman.walk_items(json.loads(raw)["item"]))[0]
        self.assertEqual(row["path"], "A / B / C"); self.assertEqual(row["method"], "POST")

    def test_marketing_stream_normalize_and_unwrap(self):
        event = {"Records": [{"body": json.dumps({"detail": {"profileId": "p", "datasetId": "budget-usage", "campaignId": "c", "idempotency_id": "idem-1"}, "id": "e1", "time": "2026-08-11T01:00:00+00:00"})}]}
        rows = stream._unwrap(event); normalized = stream.normalize(rows[0])
        self.assertEqual(normalized["profile_id"], "p"); self.assertEqual(normalized["dataset_id"], "budget-usage"); self.assertEqual(normalized["dedupe_key"], "idem-1")

    def test_marketing_stream_explicit_profile_fallback_for_advertiser_only_event(self):
        previous = stream.PROFILE_ID; stream.PROFILE_ID = "p-fallback"
        try:
            normalized = stream.normalize({"detail": {"advertiserId": "advertiser-redacted", "datasetId": "sp-traffic", "date": "2026-08-11", "hour": 9, "cost": 1.25}})
        finally:
            stream.PROFILE_ID = previous
        self.assertEqual(normalized["profile_id"], "p-fallback"); self.assertEqual(normalized["event_time"], "2026-08-11T09:00:00")

    def test_marketing_stream_missing_profile_fails_closed(self):
        previous = stream.PROFILE_ID; stream.PROFILE_ID = ""
        try:
            with self.assertRaisesRegex(RuntimeError, "ADS_STREAM_PROFILE_ID"):
                stream.normalize({"detail": {"datasetId": "sp-traffic", "cost": 1}})
        finally:
            stream.PROFILE_ID = previous


class _Response:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def read(self): return self.body


class MarketingRelayFailureTests(unittest.TestCase):
    def setUp(self):
        self.old_token = stream.TOKEN; self.old_attempts = stream.MAX_ATTEMPTS; self.old_profile = stream.PROFILE_ID
        stream.TOKEN = "x" * 48; stream.MAX_ATTEMPTS = 3; stream.PROFILE_ID = "p1"

    def tearDown(self):
        stream.TOKEN = self.old_token; stream.MAX_ATTEMPTS = self.old_attempts; stream.PROFILE_ID = self.old_profile

    def test_empty_and_missing_token(self):
        self.assertEqual(stream.relay(None), {"inserted": 0, "duplicates": 0})
        stream.TOKEN = "short"
        with self.assertRaisesRegex(RuntimeError, "missing or too short"):
            stream.relay({"id": "x", "detail": {"datasetId": "x"}})

    def test_transient_errors_retry_then_succeed(self):
        calls = [URLError("down"), HTTPError("u", 429, "rate", {}, BytesIO(b"rate")), _Response(b'{"inserted":1,"duplicates":0}')]
        with patch.object(stream, "urlopen", side_effect=calls) as request, patch.object(stream.time, "sleep"):
            result = stream.relay({"id": "e", "detail": {"datasetId": "x"}})
        self.assertEqual(result["inserted"], 1); self.assertEqual(request.call_count, 3)

    def test_permanent_http_error_does_not_retry(self):
        error = HTTPError("u", 400, "bad", {}, BytesIO(b'{"error":"bad"}'))
        with patch.object(stream, "urlopen", side_effect=error) as request:
            with self.assertRaisesRegex(RuntimeError, "HTTP 400"):
                stream.relay({"id": "e", "detail": {"datasetId": "x"}})
        self.assertEqual(request.call_count, 1)

    def test_invalid_response_and_retry_exhaustion(self):
        with patch.object(stream, "urlopen", return_value=_Response(b"[]")):
            with self.assertRaisesRegex(RuntimeError, "JSON object"):
                stream.relay({"id": "e", "detail": {"datasetId": "x"}})
        with patch.object(stream, "urlopen", side_effect=URLError("down")), patch.object(stream.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "after 3 attempts"):
                stream.relay({"id": "e", "detail": {"datasetId": "x"}})


if __name__ == "__main__":
    unittest.main()
