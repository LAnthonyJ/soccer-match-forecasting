import tests.warnings_filter  # noqa: F401 - suppress pickle warnings

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.api.app import app
from src.billing.order_store import BillingStore
from src.billing.subscription_service import SubscriptionService


def _ok(r):
    return r.get_json()["ok"]


def _data(r):
    body = r.get_json()
    return body["data"] if body.get("ok") else body


class ApiBillingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = BillingStore(Path(self.tmp.name))
        self.service = SubscriptionService(self.store)
        import os
        os.environ.setdefault("ENABLE_DEV_PAYMENT_WEBHOOK", "true")

    def tearDown(self):
        self.tmp.cleanup()

    def test_get_subscription_requires_user_id(self):
        with patch("src.api.app._billing_service", self.service):
            with app.test_client() as c:
                r = c.get("/api/me/subscription")
                self.assertEqual(r.status_code, 401)

    def test_get_subscription_with_user_id_returns_tier(self):
        with patch("src.api.app._billing_service", self.service):
            with app.test_client() as c:
                r = c.get("/api/me/subscription", headers={"X-User-Id": "u1"})
                self.assertEqual(r.status_code, 200)
                self.assertEqual(_data(r)["tier"], "free")

    def test_checkout_requires_user_id(self):
        with patch("src.api.app._billing_service", self.service):
            with app.test_client() as c:
                r = c.post("/api/billing/checkout", json={"plan_id": "vip_month"})
                self.assertEqual(r.status_code, 401)

    def test_checkout_creates_pending_order(self):
        with patch("src.api.app._billing_service", self.service):
            with app.test_client() as c:
                r = c.post("/api/billing/checkout",
                           headers={"X-User-Id": "u1"},
                           json={"plan_id": "vip_month"})
                self.assertEqual(r.status_code, 200)
                data = _data(r)
                self.assertEqual(data["status"], "pending")
                self.assertEqual(data["tier"], "vip")

    def test_webhook_activates_subscription(self):
        order = self.service.create_checkout_order("u1", "vip_day")
        with patch("src.api.app._billing_service", self.service):
            with app.test_client() as c:
                r = c.post("/api/billing/webhook/mock", json={
                    "event_id": "evt_1", "provider": "mock",
                    "order_id": order.order_id,
                    "event_type": "payment.succeeded",
                })
                self.assertEqual(r.status_code, 200)
                self.assertEqual(_data(r)["subscription_tier"], "vip")

    def test_webhook_idempotent(self):
        order = self.service.create_checkout_order("u1", "vip_day")
        payload = {
            "event_id": "evt_1", "provider": "mock",
            "order_id": order.order_id,
            "event_type": "payment.succeeded",
        }
        with patch("src.api.app._billing_service", self.service):
            with app.test_client() as c:
                c.post("/api/billing/webhook/mock", json=payload)
                r = c.post("/api/billing/webhook/mock", json=payload)
                self.assertTrue(_data(r)["idempotent"])

    def test_checkout_bad_plan_returns_400(self):
        with patch("src.api.app._billing_service", self.service):
            with app.test_client() as c:
                r = c.post("/api/billing/checkout",
                           headers={"X-User-Id": "u1"},
                           json={"plan_id": "bad_plan"})
                self.assertEqual(r.status_code, 400)

    def test_webhook_missing_event_id_returns_400(self):
        with patch("src.api.app._billing_service", self.service):
            with app.test_client() as c:
                r = c.post("/api/billing/webhook/mock", json={
                    "provider": "mock", "order_id": "ord_x",
                    "event_type": "payment.succeeded",
                })
                self.assertEqual(r.status_code, 400)

    def test_webhook_unknown_order_returns_400(self):
        with patch("src.api.app._billing_service", self.service):
            with app.test_client() as c:
                r = c.post("/api/billing/webhook/mock", json={
                    "event_id": "evt_x", "provider": "mock",
                    "order_id": "ord_unknown",
                    "event_type": "payment.succeeded",
                })
                self.assertEqual(r.status_code, 400)

    def test_repeat_payment_different_event_id_does_not_extend(self):
        order = self.service.create_checkout_order("u1", "vip_day")
        evt1 = {"event_id": "evt_a", "provider": "mock", "order_id": order.order_id,
                "event_type": "payment.succeeded"}
        evt2 = {"event_id": "evt_b", "provider": "mock", "order_id": order.order_id,
                "event_type": "payment.succeeded"}
        with patch("src.api.app._billing_service", self.service):
            with app.test_client() as c:
                r1 = c.post("/api/billing/webhook/mock", json=evt1)
                self.assertEqual(_data(r1)["order_status"], "paid")
                r2 = c.post("/api/billing/webhook/mock", json=evt2)
                self.assertEqual(_data(r2)["order_status"], "already_paid")


class ApiBillingSecurityTests(unittest.TestCase):
    """Tests for Step 10G security: signed webhook, mock disabled, session tokens."""

    def setUp(self):
        import os
        self.tmp = tempfile.TemporaryDirectory()
        self.store = BillingStore(Path(self.tmp.name))
        self.service = SubscriptionService(self.store)
        os.environ["ENABLE_DEV_PAYMENT_WEBHOOK"] = "true"

    def tearDown(self):
        import os
        self.tmp.cleanup()
        os.environ.pop("ENABLE_DEV_PAYMENT_WEBHOOK", None)

    def test_signed_webhook_missing_signature_returns_401(self):
        order = self.service.create_checkout_order("u1", "vip_day")
        with patch("src.api.app._billing_service", self.service):
            with app.test_client() as c:
                r = c.post("/api/billing/webhook", json={
                    "event_id": "evt_1", "order_id": order.order_id,
                    "event_type": "payment.succeeded",
                })
                self.assertEqual(r.status_code, 401)

    def test_signed_webhook_valid_signature_processes(self):
        import os
        os.environ["PAYMENT_WEBHOOK_SECRET"] = "wh-secret"
        order = self.service.create_checkout_order("u1", "vip_day")
        from src.billing.payment_gateway import verify_webhook_signature
        import json
        body = json.dumps({
            "event_id": "evt_signed", "order_id": order.order_id,
            "event_type": "payment.succeeded",
        }).encode("utf-8")
        sig = verify_webhook_signature.sign(body, "wh-secret")
        with patch("src.api.app._billing_service", self.service):
            with app.test_client() as c:
                r = c.post("/api/billing/webhook",
                           data=body,
                           content_type="application/json",
                           headers={"X-Payment-Signature": sig})
                self.assertEqual(r.status_code, 200)
                self.assertTrue(_ok(r))

    def test_mock_webhook_disabled_returns_404(self):
        import os
        os.environ.pop("ENABLE_DEV_PAYMENT_WEBHOOK", None)
        os.environ["ENABLE_DEV_PAYMENT_WEBHOOK"] = "false"
        try:
            with patch("src.api.app._billing_service", self.service):
                with app.test_client() as c:
                    r = c.post("/api/billing/webhook/mock", json={
                        "event_id": "evt_1", "order_id": "ord_x",
                        "event_type": "payment.succeeded",
                    })
                    self.assertEqual(r.status_code, 404)
        finally:
            os.environ.pop("ENABLE_DEV_PAYMENT_WEBHOOK", None)
            os.environ["ENABLE_DEV_PAYMENT_WEBHOOK"] = "true"

    def test_session_endpoint_creates_token(self):
        with app.test_client() as c:
            r = c.post("/api/auth/session", json={"user_id": "u1"})
            self.assertEqual(r.status_code, 200)
            data = _data(r)
            self.assertIn("token", data)
            self.assertIn(".", data["token"])

    def test_redeem_endpoint_returns_session_and_tier(self):
        from src.auth.card_codes import CardCodeService, CardCodeStore

        card_service = CardCodeService(
            store=CardCodeStore(Path(self.tmp.name) / "cards"),
            subscription_service=self.service,
        )
        code = card_service.generate_codes(tier="vip", duration_days=7, count=1)[0]["code"]

        with patch("src.api.app._billing_service", self.service):
            with patch("src.api.app._card_code_service", card_service):
                with app.test_client() as c:
                    r = c.post("/api/auth/redeem", json={"username": "alice", "card_code": code})
                    self.assertEqual(r.status_code, 200)
                    data = _data(r)
                    self.assertEqual(data["tier"], "vip")
                    self.assertEqual(data["user_id"], "alice")
                    self.assertIn("token", data)

    def test_redeem_endpoint_rejects_reused_code(self):
        from src.auth.card_codes import CardCodeService, CardCodeStore

        card_service = CardCodeService(
            store=CardCodeStore(Path(self.tmp.name) / "cards"),
            subscription_service=self.service,
        )
        code = card_service.generate_codes(tier="basic", duration_days=7, count=1)[0]["code"]
        card_service.redeem_code("alice", code)

        with patch("src.api.app._billing_service", self.service):
            with patch("src.api.app._card_code_service", card_service):
                with app.test_client() as c:
                    r = c.post("/api/auth/redeem", json={"username": "bob", "card_code": code})
                    self.assertEqual(r.status_code, 400)

    def test_prod_checkout_without_token_returns_401_not_500(self):
        env = {
            "APP_ENV": "prod",
            "BETA_AUTH_SECRET": "prod-beta-secret",
            "PAYMENT_WEBHOOK_SECRET": "prod-webhook-secret",
            "ENABLE_DEV_PAYMENT_WEBHOOK": "false",
        }
        with patch.dict("os.environ", env, clear=False):
            with patch("src.api.app._billing_service", self.service):
                with app.test_client() as c:
                    r = c.post(
                        "/api/billing/checkout",
                        headers={"X-User-Id": "u1"},
                        json={"plan_id": "vip_day"},
                    )
                    self.assertEqual(r.status_code, 401)
                    self.assertEqual(r.get_json()["error"]["code"], "unauthorized")


if __name__ == "__main__":
    unittest.main()
