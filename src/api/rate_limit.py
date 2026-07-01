import time

from src.api.errors import ApiError


class FixedWindowRateLimiter:
    def __init__(self, limit: int, window_seconds: float):
        self.limit = limit
        self.window_seconds = window_seconds
        self._buckets: dict[str, tuple[float, int]] = {}

    def check(self, key: str) -> None:
        now = time.time()
        window_start, count = self._buckets.get(key, (now, 0))
        if now - window_start >= self.window_seconds:
            window_start, count = now, 0
        count += 1
        self._buckets[key] = (window_start, count)
        if count > self.limit:
            raise ApiError("rate_limited", "Too many requests", status_code=429)
