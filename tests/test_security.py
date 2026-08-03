import time
import unittest
from amazon_ads_control.security import SessionStore, hash_password, verify_password

class SecurityTests(unittest.TestCase):
    def test_password_roundtrip(self):
        encoded = hash_password("this-is-a-long-test-password")
        self.assertTrue(verify_password("this-is-a-long-test-password", encoded))
        self.assertFalse(verify_password("wrong-password", encoded))

    def test_short_password_rejected(self):
        with self.assertRaises(ValueError): hash_password("short")

    def test_session_limit_and_revoke(self):
        store = SessionStore(ttl_seconds=300, max_sessions=2)
        a, _ = store.create(); b, _ = store.create(); c, _ = store.create()
        self.assertIsNone(store.validate(a))
        self.assertIsNotNone(store.validate(b)); self.assertIsNotNone(store.validate(c))
        store.revoke(b); self.assertIsNone(store.validate(b))

if __name__ == '__main__': unittest.main()
