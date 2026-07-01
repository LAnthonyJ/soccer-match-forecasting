import tests.warnings_filter  # noqa: F401 — suppress pickle warnings

import time
import unittest

from src.auth.session_tokens import create_session_token, verify_session_token


class SessionTokenTests(unittest.TestCase):
    def test_roundtrip(self):
        token = create_session_token("user_1", "secret", ttl_seconds=3600, now=1000)
        payload = verify_session_token(token, "secret", now=1001)
        self.assertEqual(payload["user_id"], "user_1")

    def test_tampered_token_rejected(self):
        token = create_session_token("user_1", "secret", ttl_seconds=3600, now=1000)
        bad = token[:-2] + "xx"
        with self.assertRaises(ValueError):
            verify_session_token(bad, "secret", now=1001)

    def test_expired_token_rejected(self):
        token = create_session_token("user_1", "secret", ttl_seconds=10, now=1000)
        with self.assertRaises(ValueError):
            verify_session_token(token, "secret", now=1011)
