from dataclasses import asdict, dataclass
from typing import Any


PLANS: dict[str, dict[str, Any]] = {
    "vip_day": {"tier": "vip", "amount_cents": 990, "currency": "CNY", "duration_days": 1},
    "vip_week": {"tier": "vip", "amount_cents": 2990, "currency": "CNY", "duration_days": 7},
    "vip_month": {"tier": "vip", "amount_cents": 9900, "currency": "CNY", "duration_days": 30},
}

VALID_ORDER_STATUSES = {"pending", "paid", "cancelled", "expired", "refunded"}
VALID_SUBSCRIPTION_STATUSES = {"active", "expired", "cancelled"}


def get_plan(plan_id: str) -> dict[str, Any]:
    plan = PLANS.get(plan_id)
    if plan is None:
        raise ValueError(f"Unknown plan_id: {plan_id}")
    return dict(plan)


@dataclass
class OrderRecord:
    order_id: str
    user_id: str
    plan_id: str
    tier: str
    amount_cents: int
    currency: str
    status: str
    provider: str
    provider_order_id: str | None
    created_at: str
    paid_at: str | None = None
    expires_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "OrderRecord":
        return cls(**payload)


@dataclass
class SubscriptionRecord:
    user_id: str
    tier: str
    status: str
    starts_at: str
    expires_at: str | None
    source_order_id: str | None = None
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SubscriptionRecord":
        return cls(**payload)


@dataclass
class PaymentEventRecord:
    event_id: str
    provider: str
    order_id: str
    event_type: str
    received_at: str
    raw_payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PaymentEventRecord":
        return cls(**payload)
