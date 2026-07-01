import tests.warnings_filter  # noqa: F401 — suppress pickle warnings

import unittest

from src.billing.payment_gateway import MockPaymentGateway, verify_webhook_signature


class PaymentGatewayTests(unittest.TestCase):
    def test_mock_checkout_returns_payment_url(self):
        gateway = MockPaymentGateway(
            base_url="http://localhost:5000",
            return_url="http://localhost:5000/h5#/account",
        )
        result = gateway.create_checkout(order_id="ord_1", amount_cents=990, currency="CNY")
        self.assertIn("payment_url", result)
        self.assertIn("ord_1", result["payment_url"])

    def test_webhook_signature_valid(self):
        body = b'{"event_id":"evt_1"}'
        sig = verify_webhook_signature.sign(body, "secret")
        self.assertTrue(verify_webhook_signature(body, sig, "secret"))

    def test_webhook_signature_invalid(self):
        body = b'{"event_id":"evt_1"}'
        self.assertFalse(verify_webhook_signature(body, "bad", "secret"))
