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
