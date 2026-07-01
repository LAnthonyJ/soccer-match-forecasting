import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from src.billing.models import OrderRecord, PaymentEventRecord, SubscriptionRecord, get_plan
from src.billing.order_store import BillingStore


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


class SubscriptionService:
    def __init__(self, store: BillingStore, audit_log=None):
        self.store = store
        self.audit_log = audit_log

    def _audit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.audit_log:
            try:
                self.audit_log.append(event_type, payload)
            except Exception:
                pass  # audit failure must not block business logic

    def create_checkout_order(self, user_id: str, plan_id: str, provider: str = "mock") -> OrderRecord:
        plan = get_plan(plan_id)
        now = utc_now()
        order = OrderRecord(
            order_id=f"ord_{uuid.uuid4().hex[:16]}",
            user_id=user_id,
            plan_id=plan_id,
            tier=plan["tier"],
            amount_cents=plan["amount_cents"],
            currency=plan["currency"],
            status="pending",
            provider=provider,
            provider_order_id=None,
            created_at=now.isoformat(),
            expires_at=(now + timedelta(minutes=30)).isoformat(),
        )
        self.store.save_order(order)
        self._audit("checkout.created", order.to_dict())
        return order

    def handle_payment_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        event_id = payload["event_id"]
        if self.store.has_event(event_id):
            order = self.store.get_order(payload["order_id"])
            sub = self.store.get_subscription(order.user_id) if order else None
            result = {
                "ok": True,
                "idempotent": True,
                "order_status": order.status if order else "unknown",
                "subscription_tier": sub.tier if sub else "free",
            }
            self._audit("payment.idempotent", {"event_id": event_id, "result": result})
            return result

        order = self.store.get_order(payload["order_id"])
        if order is None:
            raise ValueError(f"Unknown order_id: {payload['order_id']}")

        # Block already-paid orders (different event_id on same order)
        if order.status == "paid":
            sub = self.store.get_subscription(order.user_id)
            result = {
                "ok": True,
                "idempotent": False,
                "order_status": "already_paid",
                "subscription_tier": sub.tier if sub else "free",
            }
            self._audit("payment.already_paid", {"order_id": order.order_id, "event_id": event_id})
            return result

        # Block expired orders
        now = utc_now()
        if order.expires_at:
            try:
                expires = datetime.fromisoformat(order.expires_at)
                if now >= expires:
                    raise ValueError(f"Order {order.order_id} has expired")
            except ValueError as exc:
                if "expired" in str(exc):
                    raise
                pass  # unparseable expiry → let through
        event = PaymentEventRecord(
            event_id=event_id,
            provider=payload.get("provider", order.provider),
            order_id=order.order_id,
            event_type=payload["event_type"],
            received_at=now.isoformat(),
            raw_payload=payload,
        )
        self.store.save_event(event)
        self._audit("payment.received", {"event_id": event_id, "order_id": order.order_id, "event_type": payload["event_type"]})

        if payload["event_type"] != "payment.succeeded":
            return {"ok": True, "idempotent": False, "order_status": order.status, "subscription_tier": "free"}

        order.status = "paid"
        order.paid_at = now.isoformat()
        order.provider_order_id = payload.get("provider_order_id")
        self.store.save_order(order)

        plan = get_plan(order.plan_id)
        expires_at = now + timedelta(days=plan["duration_days"])
        sub = SubscriptionRecord(
            user_id=order.user_id,
            tier=order.tier,
            status="active",
            starts_at=now.isoformat(),
            expires_at=expires_at.isoformat(),
            source_order_id=order.order_id,
            updated_at=now.isoformat(),
        )
        self.store.save_subscription(sub)
        self._audit("subscription.activated", {"user_id": sub.user_id, "tier": sub.tier, "order_id": order.order_id})
        return {"ok": True, "idempotent": False, "order_status": "paid", "subscription_tier": sub.tier}

    def get_subscription(self, user_id: str) -> SubscriptionRecord | None:
        return self.store.get_subscription(user_id)

    def resolve_tier(self, user_id: str, now: datetime | None = None) -> str:
        sub = self.get_subscription(user_id)
        if sub is None or sub.status != "active":
            return "free"
        current = now or utc_now()
        expires = parse_dt(sub.expires_at)
        if expires is not None and current >= expires:
            return "free"
        return sub.tier
