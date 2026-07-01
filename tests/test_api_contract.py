import tests.warnings_filter  # noqa: F401 — suppress pickle warnings

import unittest
from unittest.mock import MagicMock, patch

from src.api.app import app
from src.api.cache_headers import apply_no_store, apply_prediction_cache_headers
from src.api.errors import ApiError
from src.api.pagination import parse_limit


class ApiHelperContractTests(unittest.TestCase):
    def test_parse_limit_defaults(self):
        self.assertEqual(parse_limit(None), 20)

    def test_parse_limit_caps_max(self):
        self.assertEqual(parse_limit("500", max_limit=100), 100)

    def test_parse_limit_rejects_bad_value(self):
        with self.assertRaises(ValueError):
            parse_limit("bad")

    def test_cache_header_helpers(self):
        headers = {}
        apply_no_store(headers)
        self.assertEqual(headers["Cache-Control"], "no-store")
        headers = {}
        apply_prediction_cache_headers(headers)
        self.assertIn("max-age", headers["Cache-Control"])


class ApiEnvelopeRouteTests(unittest.TestCase):
    def _cached(self):
        return {
            "match_id": "sportmonks-1",
            "home_team": "A", "away_team": "B",
            "probabilities": {"home": 0.4, "draw": 0.3, "away": 0.3},
            "top_scorelines": [], "confidence": 0.7,
        }

    def _mock_billing(self, tier):
        fake = MagicMock()
        fake.resolve_tier.return_value = tier
        return fake

    def test_prediction_success_uses_envelope(self):
        fake_billing = self._mock_billing("free")
        with patch("src.api.app._billing_service", fake_billing):
            with patch("src.api.app.load_latest_prediction", return_value=self._cached()):
                with app.test_client() as c:
                    r = c.get(
                        "/api/predictions/1",
                        headers={"X-Request-Id": "req_test", "X-User-Id": "u1"},
                    )
                    body = r.get_json()
                    self.assertTrue(body["ok"])
                    self.assertIn("data", body)
                    self.assertEqual(body["meta"]["request_id"], "req_test")

    def test_prediction_bad_id_uses_error_envelope(self):
        with app.test_client() as c:
            r = c.get(
                "/api/predictions/bad",
                headers={"X-Request-Id": "req_test", "X-User-Id": "u1"},
            )
            body = r.get_json()
            self.assertEqual(r.status_code, 400)
            self.assertFalse(body["ok"])
            self.assertEqual(body["error"]["code"], "bad_request")

    def test_vip_endpoint_forbidden_uses_error_envelope(self):
        fake_billing = self._mock_billing("free")
        with patch("src.api.app._billing_service", fake_billing):
            with app.test_client() as c:
                r = c.get("/api/predictions/1/vip")
                body = r.get_json()
                self.assertEqual(r.status_code, 403)
                self.assertEqual(body["error"]["code"], "vip_required")

    def test_checkout_missing_user_uses_error_envelope(self):
        with app.test_client() as c:
            r = c.post("/api/billing/checkout", json={"plan_id": "vip_day"})
            body = r.get_json()
            self.assertEqual(r.status_code, 401)
            self.assertEqual(body["error"]["code"], "unauthorized")

    def test_subscription_anonymous_returns_401(self):
        with app.test_client() as c:
            r = c.get("/api/me/subscription")
            body = r.get_json()
            self.assertEqual(r.status_code, 401)
            self.assertEqual(body["error"]["code"], "unauthorized")

    def test_subscription_authenticated_uses_envelope(self):
        fake_billing = MagicMock()
        fake_billing.resolve_tier.return_value = "vip"
        fake_billing.get_subscription.return_value = None
        with patch("src.api.app._billing_service", fake_billing):
            with app.test_client() as c:
                r = c.get("/api/me/subscription", headers={"X-User-Id": "u1"})
                body = r.get_json()
                self.assertTrue(body["ok"])
                self.assertIn("data", body)
                self.assertEqual(body["data"]["tier"], "vip")

    def test_matches_today_uses_envelope(self):
        with patch("src.data.sportmonks_client.get_fixtures_between", return_value=[]):
            with app.test_client() as c:
                r = c.get("/api/matches/today")
                body = r.get_json()
                self.assertTrue(body["ok"])
                self.assertIn("data", body)
                self.assertIn("matches", body["data"])
                self.assertIn("count", body["data"])

    def test_matches_today_upstream_failure_returns_warning(self):
        with patch("src.data.sportmonks_client.get_fixtures_between", side_effect=Exception("down")):
            with app.test_client() as c:
                r = c.get("/api/matches/today")
                body = r.get_json()
                self.assertTrue(body["ok"])
                self.assertEqual(body["meta"].get("warning"), "upstream_unavailable")


class ApiRateLimitRouteTests(unittest.TestCase):
    def test_checkout_rate_limit_returns_429(self):
        fake_limiter = MagicMock()
        fake_limiter.check.side_effect = ApiError("rate_limited", "Too many requests", status_code=429)
        fake_billing = MagicMock()
        fake_billing.resolve_tier.return_value = "free"
        with patch("src.api.app._billing_limiter", fake_limiter):
            with patch("src.api.app._billing_service", fake_billing):
                with app.test_client() as c:
                    r = c.post("/api/billing/checkout",
                               headers={"X-User-Id": "u1"},
                               json={"plan_id": "vip_day"})
                    body = r.get_json()
                    self.assertEqual(r.status_code, 429)
                    self.assertEqual(body["error"]["code"], "rate_limited")


if __name__ == "__main__":
    unittest.main()
