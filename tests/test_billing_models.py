import tests.warnings_filter  # noqa: F401 — suppress pickle warnings

import unittest

from src.billing.models import (
    OrderRecord,
    PaymentEventRecord,
    SubscriptionRecord,
    get_plan,
)


class BillingModelsTests(unittest.TestCase):
    def test_get_plan_known_vip_month(self):
        plan = get_plan("vip_month")
        self.assertEqual(plan["tier"], "vip")
        self.assertEqual(plan["currency"], "CNY")
        self.assertEqual(plan["duration_days"], 30)

    def test_get_plan_unknown_raises(self):
        with self.assertRaises(ValueError):
            get_plan("bad_plan")

    def test_order_roundtrip(self):
        order = OrderRecord(
            order_id="ord_1", user_id="u1", plan_id="vip_month",
            tier="vip", amount_cents=9900, currency="CNY",
            status="pending", provider="mock", provider_order_id=None,
            created_at="2026-06-29T00:00:00+00:00",
        )
        loaded = OrderRecord.from_dict(order.to_dict())
        self.assertEqual(loaded.order_id, "ord_1")
        self.assertEqual(loaded.status, "pending")

    def test_subscription_roundtrip(self):
        sub = SubscriptionRecord(
            user_id="u1", tier="vip", status="active",
            starts_at="2026-06-29T00:00:00+00:00",
            expires_at="2026-07-29T00:00:00+00:00",
            source_order_id="ord_1", updated_at="2026-06-29T00:00:00+00:00",
        )
        loaded = SubscriptionRecord.from_dict(sub.to_dict())
        self.assertEqual(loaded.tier, "vip")
        self.assertEqual(loaded.source_order_id, "ord_1")

    def test_payment_event_roundtrip(self):
        event = PaymentEventRecord(
            event_id="evt_1", provider="mock", order_id="ord_1",
            event_type="payment.succeeded",
            received_at="2026-06-29T00:00:00+00:00",
            raw_payload={"a": 1},
        )
        loaded = PaymentEventRecord.from_dict(event.to_dict())
        self.assertEqual(loaded.raw_payload["a"], 1)


if __name__ == "__main__":
    unittest.main()
