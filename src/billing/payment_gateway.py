"""Provider-neutral payment gateway abstraction for beta launch."""
import hashlib
import hmac
from dataclasses import dataclass


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_webhook_signature(body: bytes, signature: str, secret: str) -> bool:
    if not secret:
        return False
    return hmac.compare_digest(signature, _sign(body, secret))


# Attach sign helper for tests
verify_webhook_signature.sign = _sign


@dataclass
class MockPaymentGateway:
    base_url: str
    return_url: str

    def create_checkout(self, order_id: str, amount_cents: int, currency: str) -> dict:
        return {
            "provider": "mock",
            "payment_url": f"{self.base_url.rstrip('/')}/h5#/subscribe?order_id={order_id}",
            "amount_cents": amount_cents,
            "currency": currency,
            "return_url": self.return_url,
        }
