# Step 10B Subscription And Orders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace in-memory/demo subscription access with a durable user, subscription, order, and payment-status layer that Step 10A API authorization can trust.

**Architecture:** Keep the first production version intentionally small: JSON-backed storage with explicit repository interfaces, so it can later move to SQLite/Postgres without changing API behavior. Access control should resolve tiers from the subscription service, not request headers. Payment webhooks should be idempotent and update orders/subscriptions through one service path.

**Tech Stack:** Python stdlib dataclasses/json/pathlib/datetime, existing Flask API, existing unittest test suite, existing `src/auth/access_control.py` tier model.

---

## Scope

This step builds the backend commercial state layer only. It does not build the H5 payment UI, real Stripe/WeChat/Alipay SDK integration, or admin dashboard. Those belong after the subscription/order contract is stable.

Step 10B deliverable:

- Durable users stored in `data/auth/users.json`
- Durable subscriptions stored in `data/auth/subscriptions.json`
- Durable orders stored in `data/billing/orders.json`
- Payment webhook endpoint with idempotency
- API endpoints to create checkout orders and inspect current subscription
- Step 10A auth updated to resolve user tier from durable subscription state
- Full unit/API tests

## File Map

- Create `src/auth/user_store.py`: user model, API-key/session-token lookup, durable JSON user repository.
- Create `src/billing/models.py`: `OrderRecord`, `PaymentEventRecord`, `SubscriptionPlan`, constants for tiers and plans.
- Create `src/billing/order_store.py`: durable JSON order repository with idempotent event storage.
- Create `src/billing/subscription_service.py`: plan duration rules, order creation, payment confirmation, subscription activation, cancellation/expiry logic.
- Modify `src/auth/access_control.py`: replace `_subscriptions` as primary source with subscription service lookup, keep test helper functions as compatibility wrappers.
- Modify `src/api/app.py`: add user auth helper, add billing/subscription endpoints, use durable tier lookup.
- Create `tests/test_billing_models.py`: model serialization and validation tests.
- Create `tests/test_subscription_service.py`: order lifecycle, idempotency, subscription activation, expiry tests.
- Create `tests/test_api_billing.py`: Flask endpoint tests for checkout, subscription status, payment webhook, auth integration.
- Modify `tests/test_access_control.py`: adapt tests to durable resolver while preserving existing behavior.
- Create `docs/api_billing_contract.md`: API contract for Step 10B.

## Data Models

Use JSON records so tests stay deterministic and no database dependency is introduced in this step.

`UserRecord`:

```python
@dataclass
class UserRecord:
    user_id: str
    display_name: str = ""
    api_key_hash: str = ""
    created_at: str = ""
    disabled: bool = False
```

`SubscriptionRecord`:

```python
@dataclass
class SubscriptionRecord:
    user_id: str
    tier: str
    status: str
    starts_at: str
    expires_at: str | None
    source_order_id: str | None = None
    updated_at: str = ""
```

Allowed subscription `status`: `active`, `expired`, `cancelled`.

`OrderRecord`:

```python
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
```

Allowed order `status`: `pending`, `paid`, `cancelled`, `expired`, `refunded`.

`PaymentEventRecord`:

```python
@dataclass
class PaymentEventRecord:
    event_id: str
    provider: str
    order_id: str
    event_type: str
    received_at: str
    raw_payload: dict
```

Plan table for first version:

```python
PLANS = {
    "vip_day": {"tier": "vip", "amount_cents": 990, "currency": "CNY", "duration_days": 1},
    "vip_week": {"tier": "vip", "amount_cents": 2990, "currency": "CNY", "duration_days": 7},
    "vip_month": {"tier": "vip", "amount_cents": 9900, "currency": "CNY", "duration_days": 30},
}
```

## API Contract

Add these endpoints to `src/api/app.py`:

`GET /api/me/subscription`

Request headers:

```http
X-User-Id: user_123
```

Response:

```json
{
  "user_id": "user_123",
  "tier": "vip",
  "status": "active",
  "expires_at": "2026-07-29T00:00:00+00:00"
}
```

`POST /api/billing/checkout`

Request:

```json
{
  "plan_id": "vip_month"
}
```

Response:

```json
{
  "order_id": "ord_...",
  "status": "pending",
  "plan_id": "vip_month",
  "tier": "vip",
  "amount_cents": 9900,
  "currency": "CNY",
  "payment_url": null
}
```

`POST /api/billing/webhook/mock`

Request:

```json
{
  "event_id": "evt_001",
  "provider": "mock",
  "order_id": "ord_...",
  "event_type": "payment.succeeded",
  "provider_order_id": "mock_123"
}
```

Response:

```json
{
  "ok": true,
  "idempotent": false,
  "order_status": "paid",
  "subscription_tier": "vip"
}
```

Repeated `event_id` must return `idempotent: true` and must not extend the subscription twice.

## Task 1: Billing Models

**Files:**

- Create: `src/billing/models.py`
- Create: `tests/test_billing_models.py`

- [ ] **Step 1: Write model tests**

Create `tests/test_billing_models.py`:

```python
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
            order_id="ord_1",
            user_id="u1",
            plan_id="vip_month",
            tier="vip",
            amount_cents=9900,
            currency="CNY",
            status="pending",
            provider="mock",
            provider_order_id=None,
            created_at="2026-06-29T00:00:00+00:00",
        )
        loaded = OrderRecord.from_dict(order.to_dict())
        self.assertEqual(loaded.order_id, "ord_1")
        self.assertEqual(loaded.status, "pending")

    def test_subscription_roundtrip(self):
        sub = SubscriptionRecord(
            user_id="u1",
            tier="vip",
            status="active",
            starts_at="2026-06-29T00:00:00+00:00",
            expires_at="2026-07-29T00:00:00+00:00",
            source_order_id="ord_1",
            updated_at="2026-06-29T00:00:00+00:00",
        )
        loaded = SubscriptionRecord.from_dict(sub.to_dict())
        self.assertEqual(loaded.tier, "vip")
        self.assertEqual(loaded.source_order_id, "ord_1")

    def test_payment_event_roundtrip(self):
        event = PaymentEventRecord(
            event_id="evt_1",
            provider="mock",
            order_id="ord_1",
            event_type="payment.succeeded",
            received_at="2026-06-29T00:00:00+00:00",
            raw_payload={"a": 1},
        )
        loaded = PaymentEventRecord.from_dict(event.to_dict())
        self.assertEqual(loaded.raw_payload["a"], 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```powershell
python -m unittest tests.test_billing_models
```

Expected: fail because `src.billing.models` does not exist.

- [ ] **Step 3: Implement models**

Create `src/billing/models.py`:

```python
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
```

- [ ] **Step 4: Run tests**

Run:

```powershell
python -m unittest tests.test_billing_models
```

Expected: OK.

## Task 2: Durable User And Subscription Stores

**Files:**

- Create: `src/auth/user_store.py`
- Create: `src/billing/order_store.py`
- Test: `tests/test_subscription_service.py`

- [ ] **Step 1: Write store tests**

Create the first part of `tests/test_subscription_service.py`:

```python
import tempfile
import unittest
from pathlib import Path

from src.auth.user_store import UserRecord, UserStore
from src.billing.models import OrderRecord, PaymentEventRecord, SubscriptionRecord
from src.billing.order_store import BillingStore


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
                order_id="ord_1",
                user_id="u1",
                plan_id="vip_day",
                tier="vip",
                amount_cents=990,
                currency="CNY",
                status="pending",
                provider="mock",
                provider_order_id=None,
                created_at="2026-06-29T00:00:00+00:00",
            )
            store.save_order(order)
            self.assertEqual(store.get_order("ord_1").user_id, "u1")

    def test_billing_store_event_idempotency(self):
        with tempfile.TemporaryDirectory() as td:
            store = BillingStore(Path(td))
            event = PaymentEventRecord(
                event_id="evt_1",
                provider="mock",
                order_id="ord_1",
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
                user_id="u1",
                tier="vip",
                status="active",
                starts_at="2026-06-29T00:00:00+00:00",
                expires_at=None,
                source_order_id="ord_1",
                updated_at="2026-06-29T00:00:00+00:00",
            )
            store.save_subscription(sub)
            self.assertEqual(store.get_subscription("u1").tier, "vip")
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```powershell
python -m unittest tests.test_subscription_service
```

Expected: fail because stores do not exist.

- [ ] **Step 3: Implement `src/auth/user_store.py`**

```python
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class UserRecord:
    user_id: str
    display_name: str = ""
    api_key_hash: str = ""
    created_at: str = ""
    disabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if not payload["created_at"]:
            payload["created_at"] = datetime.now(timezone.utc).isoformat()
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "UserRecord":
        return cls(**payload)


class UserStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"users": {}}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def save_user(self, user: UserRecord) -> None:
        payload = self._read()
        payload.setdefault("users", {})[user.user_id] = user.to_dict()
        self._write(payload)

    def get_user(self, user_id: str) -> UserRecord | None:
        raw = self._read().get("users", {}).get(user_id)
        return UserRecord.from_dict(raw) if raw else None
```

- [ ] **Step 4: Implement `src/billing/order_store.py`**

```python
import json
from pathlib import Path
from typing import Any

from src.billing.models import OrderRecord, PaymentEventRecord, SubscriptionRecord


class BillingStore:
    def __init__(self, base_dir: Path | str):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.orders_path = self.base_dir / "orders.json"
        self.events_path = self.base_dir / "payment_events.json"
        self.subscriptions_path = self.base_dir / "subscriptions.json"

    def _read_json(self, path: Path, key: str) -> dict[str, Any]:
        if not path.exists():
            return {key: {}}
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def save_order(self, order: OrderRecord) -> None:
        payload = self._read_json(self.orders_path, "orders")
        payload.setdefault("orders", {})[order.order_id] = order.to_dict()
        self._write_json(self.orders_path, payload)

    def get_order(self, order_id: str) -> OrderRecord | None:
        raw = self._read_json(self.orders_path, "orders").get("orders", {}).get(order_id)
        return OrderRecord.from_dict(raw) if raw else None

    def save_event(self, event: PaymentEventRecord) -> None:
        payload = self._read_json(self.events_path, "events")
        payload.setdefault("events", {})[event.event_id] = event.to_dict()
        self._write_json(self.events_path, payload)

    def has_event(self, event_id: str) -> bool:
        return event_id in self._read_json(self.events_path, "events").get("events", {})

    def save_subscription(self, sub: SubscriptionRecord) -> None:
        payload = self._read_json(self.subscriptions_path, "subscriptions")
        payload.setdefault("subscriptions", {})[sub.user_id] = sub.to_dict()
        self._write_json(self.subscriptions_path, payload)

    def get_subscription(self, user_id: str) -> SubscriptionRecord | None:
        raw = self._read_json(self.subscriptions_path, "subscriptions").get("subscriptions", {}).get(user_id)
        return SubscriptionRecord.from_dict(raw) if raw else None
```

- [ ] **Step 5: Run store tests**

Run:

```powershell
python -m unittest tests.test_subscription_service.StoreTests
```

Expected: OK.

## Task 3: Subscription Service

**Files:**

- Create: `src/billing/subscription_service.py`
- Modify: `tests/test_subscription_service.py`

- [ ] **Step 1: Add service tests**

Append to `tests/test_subscription_service.py`:

```python
from datetime import datetime, timezone

from src.billing.subscription_service import SubscriptionService


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
                "event_id": "evt_1",
                "provider": "mock",
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
                "event_id": "evt_1",
                "provider": "mock",
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
                    "event_id": "evt_bad",
                    "provider": "mock",
                    "order_id": "missing",
                    "event_type": "payment.succeeded",
                })

    def test_expired_subscription_resolves_free(self):
        with tempfile.TemporaryDirectory() as td:
            store = BillingStore(Path(td))
            store.save_subscription(SubscriptionRecord(
                user_id="u1",
                tier="vip",
                status="active",
                starts_at="2026-01-01T00:00:00+00:00",
                expires_at="2026-01-02T00:00:00+00:00",
                source_order_id="ord_old",
                updated_at="2026-01-01T00:00:00+00:00",
            ))
            svc = SubscriptionService(store)
            self.assertEqual(svc.resolve_tier("u1", now=datetime(2026, 6, 29, tzinfo=timezone.utc)), "free")
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```powershell
python -m unittest tests.test_subscription_service.SubscriptionServiceTests
```

Expected: fail because service does not exist.

- [ ] **Step 3: Implement service**

Create `src/billing/subscription_service.py`:

```python
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
    def __init__(self, store: BillingStore):
        self.store = store

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
        return order

    def handle_payment_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        event_id = payload["event_id"]
        if self.store.has_event(event_id):
            order = self.store.get_order(payload["order_id"])
            sub = self.store.get_subscription(order.user_id) if order else None
            return {
                "ok": True,
                "idempotent": True,
                "order_status": order.status if order else "unknown",
                "subscription_tier": sub.tier if sub else "free",
            }

        order = self.store.get_order(payload["order_id"])
        if order is None:
            raise ValueError(f"Unknown order_id: {payload['order_id']}")

        now = utc_now()
        event = PaymentEventRecord(
            event_id=event_id,
            provider=payload.get("provider", order.provider),
            order_id=order.order_id,
            event_type=payload["event_type"],
            received_at=now.isoformat(),
            raw_payload=payload,
        )
        self.store.save_event(event)

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
```

- [ ] **Step 4: Run tests**

Run:

```powershell
python -m unittest tests.test_subscription_service
```

Expected: OK.

## Task 4: Access Control Integration

**Files:**

- Modify: `src/auth/access_control.py`
- Modify: `tests/test_access_control.py`

- [ ] **Step 1: Add compatibility tests**

Add tests to `tests/test_access_control.py`:

```python
    def test_durable_subscription_resolves_tier(self):
        set_subscription(UserSubscription("durable_u1", "vip"))
        self.assertEqual(resolve_user_tier("durable_u1"), "vip")

    def test_invalid_tier_downgrades_to_free(self):
        set_subscription(UserSubscription("bad_tier", "supervip"))
        self.assertEqual(resolve_user_tier("bad_tier"), "free")
```

- [ ] **Step 2: Modify access control**

Update `src/auth/access_control.py` so `effective_tier()` validates known tiers:

```python
    def effective_tier(self) -> str:
        if not self.is_active():
            return "free"
        if self.tier not in TIER_HIERARCHY:
            return "free"
        return self.tier
```

Then add a durable service resolver helper:

```python
def resolve_user_tier_from_service(user_id: str, service: Any | None = None) -> str:
    if service is not None:
        return service.resolve_tier(user_id)
    return resolve_user_tier(user_id)
```

- [ ] **Step 3: Run access tests**

Run:

```powershell
python -m unittest tests.test_access_control
```

Expected: OK.

## Task 5: API Billing Endpoints

**Files:**

- Modify: `src/api/app.py`
- Create: `tests/test_api_billing.py`

- [ ] **Step 1: Write API tests**

Create `tests/test_api_billing.py`:

```python
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.api.app import app
from src.billing.order_store import BillingStore
from src.billing.subscription_service import SubscriptionService


class ApiBillingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = BillingStore(Path(self.tmp.name))
        self.service = SubscriptionService(self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def test_get_subscription_defaults_free(self):
        with patch("src.api.app._billing_service", self.service):
            with app.test_client() as c:
                r = c.get("/api/me/subscription", headers={"X-User-Id": "u1"})
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.get_json()["tier"], "free")

    def test_checkout_requires_user_id(self):
        with patch("src.api.app._billing_service", self.service):
            with app.test_client() as c:
                r = c.post("/api/billing/checkout", json={"plan_id": "vip_month"})
                self.assertEqual(r.status_code, 401)

    def test_checkout_creates_pending_order(self):
        with patch("src.api.app._billing_service", self.service):
            with app.test_client() as c:
                r = c.post("/api/billing/checkout", headers={"X-User-Id": "u1"}, json={"plan_id": "vip_month"})
                self.assertEqual(r.status_code, 200)
                data = r.get_json()
                self.assertEqual(data["status"], "pending")
                self.assertEqual(data["tier"], "vip")

    def test_webhook_activates_subscription(self):
        order = self.service.create_checkout_order("u1", "vip_day")
        with patch("src.api.app._billing_service", self.service):
            with app.test_client() as c:
                r = c.post("/api/billing/webhook/mock", json={
                    "event_id": "evt_1",
                    "provider": "mock",
                    "order_id": order.order_id,
                    "event_type": "payment.succeeded",
                })
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.get_json()["subscription_tier"], "vip")

    def test_webhook_idempotent(self):
        order = self.service.create_checkout_order("u1", "vip_day")
        payload = {
            "event_id": "evt_1",
            "provider": "mock",
            "order_id": order.order_id,
            "event_type": "payment.succeeded",
        }
        with patch("src.api.app._billing_service", self.service):
            with app.test_client() as c:
                c.post("/api/billing/webhook/mock", json=payload)
                r = c.post("/api/billing/webhook/mock", json=payload)
                self.assertTrue(r.get_json()["idempotent"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Modify Flask app**

Add imports and service construction near the top of `src/api/app.py`:

```python
from src.billing.order_store import BillingStore
from src.billing.subscription_service import SubscriptionService

_billing_service = SubscriptionService(
    BillingStore(Path(__file__).parent.parent.parent / "data" / "billing")
)
```

Add helper:

```python
def _get_user_id() -> str | None:
    user_id = request.headers.get("X-User-Id", "").strip()
    return user_id or None
```

Modify `_get_user_tier()`:

```python
def _get_user_tier() -> str:
    user_id = _get_user_id() or "anonymous"
    if os.environ.get("APP_ENV") == "dev":
        tier_hint = request.headers.get("X-User-Tier", "")
        if tier_hint in ("basic", "vip", "admin"):
            return tier_hint
    return _billing_service.resolve_tier(user_id)
```

Add endpoints:

```python
@app.route("/api/me/subscription")
def get_my_subscription():
    user_id = _get_user_id() or "anonymous"
    sub = _billing_service.get_subscription(user_id)
    if sub is None:
        return jsonify({"user_id": user_id, "tier": "free", "status": "none", "expires_at": None})
    return jsonify({
        "user_id": user_id,
        "tier": _billing_service.resolve_tier(user_id),
        "status": sub.status,
        "expires_at": sub.expires_at,
    })


@app.route("/api/billing/checkout", methods=["POST"])
def create_checkout():
    user_id = _get_user_id()
    if not user_id:
        return jsonify({"error": "X-User-Id required"}), 401
    payload = request.get_json(silent=True) or {}
    try:
        order = _billing_service.create_checkout_order(user_id, payload.get("plan_id", ""))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    data = order.to_dict()
    data["payment_url"] = None
    return jsonify(data)


@app.route("/api/billing/webhook/mock", methods=["POST"])
def mock_payment_webhook():
    payload = request.get_json(silent=True) or {}
    try:
        result = _billing_service.handle_payment_event(payload)
    except KeyError as e:
        return jsonify({"error": f"missing field: {e}"}), 400
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(result)
```

- [ ] **Step 3: Run API billing tests**

Run:

```powershell
python -m unittest tests.test_api_billing
```

Expected: OK.

## Task 6: Prediction API Uses Durable Subscription

**Files:**

- Modify: `tests/test_api_app.py`
- Modify: `src/api/app.py` if needed

- [ ] **Step 1: Add regression test**

Add to `tests/test_api_app.py`:

```python
    def test_prediction_uses_billing_service_tier(self):
        cached = self._cache_payload()
        fake_billing = MagicMock()
        fake_billing.resolve_tier.return_value = "vip"
        with patch("src.api.app._billing_service", fake_billing):
            with patch("src.api.app.load_latest_prediction", return_value=cached):
                with app.test_client() as c:
                    r = c.get("/api/predictions/123", headers={"X-User-Id": "paid_user"})
                    data = r.get_json()
                    self.assertIn("score_heatmap", data)
                    fake_billing.resolve_tier.assert_called_with("paid_user")
```

- [ ] **Step 2: Run API app tests**

Run:

```powershell
python -m unittest tests.test_api_app
```

Expected: OK.

## Task 7: Documentation

**Files:**

- Create: `docs/api_billing_contract.md`

- [ ] **Step 1: Write billing API contract**

Create `docs/api_billing_contract.md`:

```markdown
# Billing API Contract

## Identity

All billing and prediction authorization uses `X-User-Id`.

`X-User-Tier` is ignored unless `APP_ENV=dev`.

## Plans

| plan_id | tier | price | duration |
|---|---:|---:|---:|
| vip_day | vip | CNY 9.90 | 1 day |
| vip_week | vip | CNY 29.90 | 7 days |
| vip_month | vip | CNY 99.00 | 30 days |

## Endpoints

### GET /api/me/subscription

Returns the current effective subscription.

### POST /api/billing/checkout

Creates a pending order.

### POST /api/billing/webhook/mock

Marks mock payment events as received. Repeated `event_id` values are idempotent.

## Production Notes

The mock webhook is a development adapter. Real WeChat, Alipay, Stripe, or Paddle webhooks must verify provider signatures before calling `SubscriptionService.handle_payment_event()`.
```

## Task 8: Final Verification

**Files:**

- All files touched in Step 10B

- [ ] **Step 1: Run focused tests**

Run:

```powershell
python -m unittest tests.test_billing_models tests.test_subscription_service tests.test_api_billing tests.test_api_app tests.test_access_control
```

Expected: OK.

- [ ] **Step 2: Run full test suite**

Run:

```powershell
python -m unittest discover -s tests
```

Expected: all tests pass.

- [ ] **Step 3: Manual smoke check**

Start API:

```powershell
python src/api/app.py
```

Then in another shell:

```powershell
curl -X POST http://localhost:5000/api/billing/checkout -H "Content-Type: application/json" -H "X-User-Id: demo_user" -d "{\"plan_id\":\"vip_day\"}"
```

Expected: returns a pending order.

Call webhook with the returned `order_id`:

```powershell
curl -X POST http://localhost:5000/api/billing/webhook/mock -H "Content-Type: application/json" -d "{\"event_id\":\"evt_demo_1\",\"provider\":\"mock\",\"order_id\":\"ord_replace_me\",\"event_type\":\"payment.succeeded\"}"
```

Expected: `subscription_tier` is `vip`.

Check subscription:

```powershell
curl http://localhost:5000/api/me/subscription -H "X-User-Id: demo_user"
```

Expected: tier is `vip`.

## Implementation Order

1. Task 1: billing dataclasses and plan table.
2. Task 2: durable JSON stores.
3. Task 3: subscription service and payment idempotency.
4. Task 4: access-control compatibility integration.
5. Task 5: billing API endpoints.
6. Task 6: prediction API authorization regression test.
7. Task 7: API contract documentation.
8. Task 8: focused tests, full tests, manual smoke.

## Acceptance Criteria

- A paid mock order activates a VIP subscription.
- Replaying the same payment event does not extend or duplicate a subscription.
- `X-User-Tier` cannot grant VIP outside `APP_ENV=dev`.
- `/api/predictions/<id>` grants VIP fields based on durable subscription state.
- Free users still receive only non-VIP prediction fields.
- All new endpoints return stable JSON errors for missing user, bad plan, and unknown order.
- Full test suite passes.

## Known Follow-Ups For Step 10C

- Replace JSON storage with SQLite/Postgres repository implementation.
- Add provider signature verification for real payment webhooks.
- Add admin endpoints to list users, orders, subscriptions.
- Add audit logs for subscription status changes.
- Add rate limiting and webhook replay window checks.
