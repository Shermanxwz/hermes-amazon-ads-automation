import unittest
from amazon_ads_control.policy import redact, redact_text
from amazon_ads_control.security import SessionStore, hash_password, verify_password


class SecurityV2Tests(unittest.TestCase):
    def test_password_roundtrip(self):
        encoded=hash_password("correct horse battery staple")
        self.assertTrue(verify_password("correct horse battery staple",encoded)); self.assertFalse(verify_password("wrong",encoded))

    def test_short_password_rejected(self):
        with self.assertRaises(ValueError): hash_password("short")

    def test_session_bound(self):
        s=SessionStore(max_sessions=2); a,_=s.create(); b,_=s.create(); c,_=s.create()
        self.assertIsNone(s.validate(a)); self.assertIsNotNone(s.validate(b)); self.assertIsNotNone(s.validate(c))

    def test_recursive_redaction(self):
        value={"Authorization":"Bearer abc","nested":{"refresh_token":"secret"},"safe":"ok"}
        red=redact(value); self.assertEqual(red["Authorization"],"[redacted]"); self.assertEqual(red["nested"]["refresh_token"],"[redacted]")
        self.assertNotIn("abc",redact_text("Authorization: Bearer abc"))

class LoginLimiterTests(unittest.TestCase):
    def test_failure_block_and_success_reset(self):
        from amazon_ads_control.security import LoginRateLimiter
        limiter = LoginRateLimiter(max_failures=2, window_seconds=60, block_seconds=30)
        self.assertEqual(limiter.allowed("dashboard"),(True,0))
        self.assertEqual(limiter.failure("dashboard"),(True,0))
        allowed,retry = limiter.failure("dashboard")
        self.assertFalse(allowed); self.assertGreaterEqual(retry,1)
        self.assertFalse(limiter.allowed("dashboard")[0])
        limiter.success("dashboard")
        self.assertEqual(limiter.allowed("dashboard"),(True,0))

    def test_session_store_is_thread_safe(self):
        from concurrent.futures import ThreadPoolExecutor
        store = SessionStore(max_sessions=16)
        with ThreadPoolExecutor(max_workers=8) as pool:
            sessions = list(pool.map(lambda _: store.create()[0], range(64)))
        self.assertLessEqual(sum(store.validate(value) is not None for value in sessions),16)
