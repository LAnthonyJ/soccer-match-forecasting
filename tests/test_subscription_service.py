import tests.warnings_filter  # noqa: F401 — suppress pickle warnings

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.auth.user_store import UserRecord, UserStore
from src.billing.models import OrderRecord, PaymentEventRecord, SubscriptionRecord
from src.billing.order_store import BillingStore
from src.billing.subscription_service import SubscriptionService


class StoreTests(unittest.TestCase):
    def test_user_store_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            store = UserStore(Path(td) / "users.json")
            store.save_user(UserRecord(user_id="u1", display_name="User One"))
            loaded = store.get_user("u1")
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.display_name, "User One")

    def test_billing_store_order_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            store = BillingStore(Path(td))
            order = OrderRecord(
                order_id="ord_1", user_id="u1", plan_id="vip_day",
                tier="vip", amount_cents=990, currency="CNY",
                status="pending", provider="mock", provider_order_id=None,
                created_at="2026-06-29T00:00:00+00:00",
            )
            store.save_order(order)
            self.assertEqual(store.get_order("ord_1").user_id, "u1")

    def test_billing_store_event_idempotency(self):
        with tempfile.TemporaryDirectory() as td:
            store = BillingStore(Path(td))
            event = PaymentEventRecord(
                event_id="evt_1", provider="mock", order_id="ord_1",
                event_type="payment.succeeded",
                received_at="2026-06-29T00:00:00+00:00",
                raw_payload={},
            )
            self.assertFalse(store.has_event("evt_1"))
            store.save_event(event)
            self.assertTrue(store.has_event("evt_1"))

    def test_subscription_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            store = BillingStore(Path(td))
            sub = SubscriptionRecord(
                user_id="u1", tier="vip", status="active",
                starts_at="2026-06-29T00:00:00+00:00",
                expires_at=None, source_order_id="ord_1",
                updated_at="2026-06-29T00:00:00+00:00",
            )
            store.save_subscription(sub)
            self.assertEqual(store.get_subscription("u1").tier, "vip")


class SubscriptionServiceTests(unittest.TestCase):
    def test_create_checkout_order(self):
        with tempfile.TemporaryDirectory() as td:
            svc = SubscriptionService(BillingStore(Path(td)))
            order = svc.create_checkout_order("u1", "vip_month")
            self.assertEqual(order.user_id, "u1")
            self.assertEqual(order.status, "pending")
            self.assertEqual(order.tier, "vip")

    def test_confirm_payment_activates_subscription(self):
        with tempfile.TemporaryDirectory() as td:
            svc = SubscriptionService(BillingStore(Path(td)))
            order = svc.create_checkout_order("u1", "vip_day")
            result = svc.handle_payment_event({
                "event_id": "evt_1", "provider": "mock",
                "order_id": order.order_id,
                "event_type": "payment.succeeded",
                "provider_order_id": "pay_1",
            })
            self.assertTrue(result["ok"])
            self.assertFalse(result["idempotent"])
            sub = svc.get_subscription("u1")
            self.assertEqual(sub.tier, "vip")
            self.assertEqual(sub.status, "active")

    def test_payment_event_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            svc = SubscriptionService(BillingStore(Path(td)))
            order = svc.create_checkout_order("u1", "vip_day")
            event = {
                "event_id": "evt_1", "provider": "mock",
                "order_id": order.order_id,
                "event_type": "payment.succeeded",
            }
            first = svc.handle_payment_event(event)
            second = svc.handle_payment_event(event)
            self.assertFalse(first["idempotent"])
            self.assertTrue(second["idempotent"])

    def test_unknown_order_payment_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            svc = SubscriptionService(BillingStore(Path(td)))
            with self.assertRaises(ValueError):
                svc.handle_payment_event({
                    "event_id": "evt_bad", "provider": "mock",
                    "order_id": "missing",
                    "event_type": "payment.succeeded",
                })

    def test_expired_subscription_resolves_free(self):
        with tempfile.TemporaryDirectory() as td:
            store = BillingStore(Path(td))
            store.save_subscription(SubscriptionRecord(
                user_id="u1", tier="vip", status="active",
                starts_at="2026-01-01T00:00:00+00:00",
                expires_at="2026-01-02T00:00:00+00:00",
                source_order_id="ord_old",
                updated_at="2026-01-01T00:00:00+00:00",
            ))
            svc = SubscriptionService(store)
            self.assertEqual(
                svc.resolve_tier("u1", now=datetime(2026, 6, 29, tzinfo=timezone.utc)),
                "free",
            )


    def test_already_paid_order_blocked(self):
        """Different event_id on already-paid order must return already_paid."""
        with tempfile.TemporaryDirectory() as td:
            svc = SubscriptionService(BillingStore(Path(td)))
            order = svc.create_checkout_order("u1", "vip_day")
            svc.handle_payment_event({
                "event_id": "evt_first", "provider": "mock",
                "order_id": order.order_id,
                "event_type": "payment.succeeded",
            })
            result = svc.handle_payment_event({
                "event_id": "evt_second", "provider": "mock",
                "order_id": order.order_id,
                "event_type": "payment.succeeded",
            })
            self.assertEqual(result["order_status"], "already_paid")

    def test_expired_order_payment_rejected(self):
        """Payment on an expired order must raise ValueError."""
        with tempfile.TemporaryDirectory() as td:
            svc = SubscriptionService(BillingStore(Path(td)))
            order = svc.create_checkout_order("u1", "vip_day")
            # Manually expire the order
            from datetime import datetime, timedelta, timezone
            order.expires_at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            svc.store.save_order(order)
            with self.assertRaises(ValueError) as ctx:
                svc.handle_payment_event({
                    "event_id": "evt_exp", "provider": "mock",
                    "order_id": order.order_id,
                    "event_type": "payment.succeeded",
                })
            self.assertIn("expired", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
