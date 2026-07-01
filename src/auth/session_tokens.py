"""HMAC-signed beta session tokens. Lightweight alternative to JWT for beta launch."""
import base64
import hashlib
import hmac
import json
import time
from typing import Any


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


def _sign(payload_b64: str, secret: str) -> str:
    return _b64(hmac.new(
        secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256,
    ).digest())


def create_session_token(
    user_id: str, secret: str, ttl_seconds: int, now: int | None = None,
) -> str:
    if not user_id:
        raise ValueError("user_id required")
    if not secret:
        raise ValueError("secret required")
    ts = int(time.time() if now is None else now)
    payload = {"user_id": user_id, "iat": ts, "exp": ts + ttl_seconds}
    payload_b64 = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"{payload_b64}.{_sign(payload_b64, secret)}"


def verify_session_token(
    token: str, secret: str, now: int | None = None,
) -> dict[str, Any]:
    if not secret:
        raise ValueError("secret required")
    try:
        payload_b64, sig = token.split(".", 1)
    except ValueError as exc:
        raise ValueError("invalid token format") from exc
    expected = _sign(payload_b64, secret)
    if not hmac.compare_digest(sig, expected):
        raise ValueError("invalid token signature")
    payload = json.loads(_unb64(payload_b64).decode("utf-8"))
    ts = int(time.time() if now is None else now)
    if ts > int(payload["exp"]):
        raise ValueError("token expired")
    return payload
