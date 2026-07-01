import tests.warnings_filter  # noqa: F401 — suppress pickle warnings

import time
import unittest

from src.api.errors import ApiError
from src.api.rate_limit import FixedWindowRateLimiter


class RateLimitTests(unittest.TestCase):
    def test_allows_within_limit(self):
        limiter = FixedWindowRateLimiter(limit=2, window_seconds=60)
        limiter.check("u1")
        limiter.check("u1")

    def test_rejects_over_limit(self):
        limiter = FixedWindowRateLimiter(limit=1, window_seconds=60)
        limiter.check("u1")
        with self.assertRaises(ApiError) as raised:
            limiter.check("u1")
        self.assertEqual(raised.exception.code, "rate_limited")
        self.assertEqual(raised.exception.status_code, 429)

    def test_window_resets(self):
        limiter = FixedWindowRateLimiter(limit=1, window_seconds=0.01)
        limiter.check("u1")
        time.sleep(0.02)
        limiter.check("u1")


if __name__ == "__main__":
    unittest.main()
