import tests.warnings_filter  # noqa: F401 — suppress pickle warnings

import unittest
from unittest.mock import MagicMock, patch

from src.api.app import app
from src.api.context import build_request_context
from src.api.errors import ApiError
from src.api.validation import get_json_body, require_fields


class ApiContextTests(unittest.TestCase):
    def test_context_defaults_free(self):
        fake_billing = MagicMock()
        fake_billing.resolve_tier.return_value = "free"
        with app.test_request_context("/api/test"):
            ctx = build_request_context(fake_billing)
            self.assertEqual(ctx.user_id, "anonymous")
            self.assertEqual(ctx.tier, "free")
            self.assertFalse(ctx.is_admin)

    def test_context_uses_user_id_header(self):
        fake_billing = MagicMock()
        fake_billing.resolve_tier.return_value = "vip"
        with app.test_request_context("/api/test",
                                       headers={"X-User-Id": "u1", "X-Request-Id": "req_in"}):
            ctx = build_request_context(fake_billing)
            self.assertEqual(ctx.user_id, "u1")
            self.assertEqual(ctx.request_id, "req_in")
            self.assertEqual(ctx.tier, "vip")

    def test_require_user_rejects_anonymous(self):
        fake_billing = MagicMock()
        fake_billing.resolve_tier.return_value = "free"
        with app.test_request_context("/api/test"):
            ctx = build_request_context(fake_billing)
            with self.assertRaises(ApiError) as raised:
                ctx.require_user()
            self.assertEqual(raised.exception.status_code, 401)

    def test_require_vip_rejects_free(self):
        fake_billing = MagicMock()
        fake_billing.resolve_tier.return_value = "free"
        with app.test_request_context("/api/test", headers={"X-User-Id": "u1"}):
            ctx = build_request_context(fake_billing)
            with self.assertRaises(ApiError) as raised:
                ctx.require_tier("vip")
            self.assertEqual(raised.exception.code, "vip_required")


class ApiContextTokenTests(unittest.TestCase):
    def _cfg(self, app_env="dev", beta_secret="test-secret"):
        return type("Cfg", (), {
            "app_env": app_env, "beta_auth_secret": beta_secret,
            "enable_h5": True, "flask_debug": False, "port": 5000,
            "payment_webhook_secret": "", "enable_dev_payment_webhook": False,
        })()

    def _valid_token(self):
        from src.auth.session_tokens import create_session_token
        return create_session_token("u1", "test-secret", ttl_seconds=36000)

    def test_context_uses_bearer_token(self):
        fake_billing = MagicMock()
        fake_billing.resolve_tier.return_value = "vip"
        token = self._valid_token()
        with patch("src.config.runtime.load_runtime_config", return_value=self._cfg()):
            with app.test_request_context("/api/test", headers={
                "Authorization": f"Bearer {token}",
            }):
                ctx = build_request_context(fake_billing)
                self.assertEqual(ctx.user_id, "u1")

    def test_context_rejects_invalid_token_in_dev(self):
        fake_billing = MagicMock()
        fake_billing.resolve_tier.return_value = "free"
        with patch("src.config.runtime.load_runtime_config", return_value=self._cfg()):
            with app.test_request_context("/api/test", headers={
                "Authorization": "Bearer bad.token.here",
            }):
                # In dev, invalid token falls through to X-User-Id → anonymous
                ctx = build_request_context(fake_billing)
                self.assertEqual(ctx.user_id, "anonymous")

    def test_context_rejects_invalid_token_in_prod(self):
        fake_billing = MagicMock()
        with patch("src.config.runtime.load_runtime_config", return_value=self._cfg("prod", "prod-secret")):
            with app.test_request_context("/api/test", headers={
                "Authorization": "Bearer bad.token.here",
            }):
                with self.assertRaises(ApiError) as raised:
                    build_request_context(fake_billing)
                self.assertEqual(raised.exception.status_code, 401)

    def test_context_rejects_raw_x_user_id_in_prod(self):
        fake_billing = MagicMock()
        fake_billing.resolve_tier.return_value = "free"
        with patch("src.config.runtime.load_runtime_config", return_value=self._cfg("prod", "prod-secret")):
            with app.test_request_context("/api/test", headers={"X-User-Id": "u1"}):
                with self.assertRaises(ApiError) as raised:
                    build_request_context(fake_billing)
                self.assertEqual(raised.exception.status_code, 401)
                self.assertIn("Session token", raised.exception.message)


class ApiValidationTests(unittest.TestCase):
    def test_get_json_body_rejects_non_object(self):
        with app.test_request_context("/api/test", method="POST", json=["bad"]):
            with self.assertRaises(ApiError) as raised:
                get_json_body()
            self.assertEqual(raised.exception.code, "validation_error")

    def test_require_fields_reports_missing(self):
        with self.assertRaises(ApiError) as raised:
            require_fields({"plan_id": "vip_day"}, ["plan_id", "event_id"])
        self.assertEqual(raised.exception.details["missing"], ["event_id"])

    def test_require_fields_accepts_present_values(self):
        require_fields({"plan_id": "vip_day"}, ["plan_id"])


if __name__ == "__main__":
    unittest.main()
