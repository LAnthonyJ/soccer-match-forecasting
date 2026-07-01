# Step 10G Paid Beta Launch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the current H5 prediction product from a deployable demo into a paid beta that can safely accept real users, protect VIP data, and operate with observable payment and prediction flows.

**Architecture:** Keep the current Flask API, JSON stores, H5 frontend, and prediction pipeline. Add a production payment abstraction, signed webhook handling, lightweight user identity tokens, operational audit logs, and beta launch safeguards without rewriting billing or prediction models.

**Tech Stack:** Python Flask, unittest, JSON persistence, H5 JavaScript, HMAC signatures, existing billing/subscription stores, existing Step 10C API envelope.

---

## Scope

Step 10G is the first real paid beta layer. It does not replace the JSON store with a database and does not add new model features. It makes the existing checkout/subscription flow safe enough for a controlled paid beta.

In scope:

- Replace public mock webhook with a provider-neutral payment gateway adapter.
- Add signed webhook verification.
- Add admin/dev-only payment simulation.
- Add lightweight signed user tokens so `X-User-Id` cannot be freely spoofed in beta.
- Add H5 account token storage and header usage.
- Add payment and subscription audit events.
- Add endpoint-level tests for real/beta behavior.
- Add beta launch runbook.

Out of scope:

- Full OAuth/login/password system.
- Native mini-program.
- Real WeChat Pay SDK integration details.
- Database migration.
- Refund automation.
- Invoice/tax system.
- Model changes.

## Current State

Already done:

- `src/api/app.py` exposes prediction, H5, subscription, billing checkout, mock webhook, refresh jobs, health, and `/health/live`.
- `src/billing/*` supports orders, payment events, subscriptions, plans, and idempotency.
- `src/auth/access_control.py` resolves durable subscription tier from billing service.
- `frontend/h5/*` supports match list, prediction detail, account, subscribe, and admin refresh views.
- Step 10F added `src/server.py`, runtime config, launch smoke, deployment guide, and full tests.

Main remaining beta blockers:

- `X-User-Id` is still enough to claim a user identity.
- `/api/billing/webhook/mock` exists as a public-looking endpoint.
- Checkout returns `payment_url=None`.
- No signed webhook boundary exists.
- H5 cannot carry a signed auth token.
- No beta runbook for payment testing, subscription grant, and incident rollback.

## File Structure

Create:

- `src/auth/session_tokens.py`: HMAC signed beta session token helpers.
- `tests/test_session_tokens.py`: Token creation, verification, expiry, tamper tests.
- `src/billing/payment_gateway.py`: Provider-neutral checkout and webhook parsing interface.
- `tests/test_payment_gateway.py`: Gateway adapter tests.
- `src/billing/audit_log.py`: Append-only JSON audit log for checkout/webhook/subscription events.
- `tests/test_billing_audit_log.py`: Audit persistence tests.
- `docs/beta_launch_runbook.md`: Real paid beta operating guide.

Modify:

- `src/config/runtime.py`: Add beta auth/payment env vars.
- `.env.example`: Document beta auth/payment env vars.
- `src/api/context.py`: Resolve user from signed token when provided.
- `src/api/app.py`: Add token endpoint, real webhook endpoint, dev-only mock webhook guard, and checkout payment URL.
- `src/billing/subscription_service.py`: Emit audit records on checkout and webhook.
- `frontend/h5/api.js`: Send `Authorization: Bearer <token>`.
- `frontend/h5/state.js`: Persist beta session token.
- `frontend/h5/views/account.js`: Add beta login/session issue flow.
- `frontend/h5/views/subscribe.js`: Use returned payment URL or show pending order fallback.
- `docs/api_billing_contract.md`: Update billing and webhook contract.
- `docs/launch_checklist.md`: Add paid beta checks.

Do not modify:

- Prediction models.
- Backtest logic.
- Sportmonks ingestion.
- Existing plan prices unless explicitly requested.

## Environment Contract

Add:

```text
BETA_AUTH_SECRET=
BETA_SESSION_TTL_HOURS=720
PAYMENT_PROVIDER=mock
PAYMENT_WEBHOOK_SECRET=
PAYMENT_RETURN_URL=http://localhost:5000/h5#/account
ENABLE_DEV_PAYMENT_WEBHOOK=false
```

Rules:

- `APP_ENV=prod` requires `BETA_AUTH_SECRET` and `PAYMENT_WEBHOOK_SECRET`.
- `ENABLE_DEV_PAYMENT_WEBHOOK=true` is rejected in prod.
- `PAYMENT_PROVIDER=mock` is allowed in dev/test; beta production may still use `mock` only if access is invite-only and payment is manually verified.
- Real provider adapters can be added behind the same `PaymentGateway` interface later.

## Task 1: Runtime Beta Config

**Files:**

- Modify: `src/config/runtime.py`
- Modify: `.env.example`
- Test: `tests/test_runtime_config.py`

- [ ] **Step 1: Add failing config tests**

Add to `tests/test_runtime_config.py`:

```python
    def test_beta_auth_env_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            cfg = load_runtime_config()
        self.assertEqual(cfg.payment_provider, "mock")
        self.assertFalse(cfg.enable_dev_payment_webhook)
        self.assertEqual(cfg.beta_session_ttl_hours, 720)

    def test_prod_requires_beta_secrets(self):
        with patch.dict(os.environ, {"APP_ENV": "prod"}, clear=True):
            with self.assertRaises(ValueError) as ctx:
                load_runtime_config()
        self.assertIn("BETA_AUTH_SECRET", str(ctx.exception))

    def test_prod_rejects_dev_webhook(self):
        env = {
            "APP_ENV": "prod",
            "BETA_AUTH_SECRET": "secret",
            "PAYMENT_WEBHOOK_SECRET": "webhook",
            "ENABLE_DEV_PAYMENT_WEBHOOK": "true",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ValueError) as ctx:
                load_runtime_config()
        self.assertIn("ENABLE_DEV_PAYMENT_WEBHOOK", str(ctx.exception))
```

- [ ] **Step 2: Implement config fields**

In `RuntimeConfig`, add:

```python
    beta_auth_secret: str
    beta_session_ttl_hours: int
    payment_provider: str
    payment_webhook_secret: str
    payment_return_url: str
    enable_dev_payment_webhook: bool
```

In `load_runtime_config()`, parse:

```python
        beta_auth_secret=os.environ.get("BETA_AUTH_SECRET", ""),
        beta_session_ttl_hours=_parse_int("BETA_SESSION_TTL_HOURS", 720),
        payment_provider=os.environ.get("PAYMENT_PROVIDER", "mock").strip().lower(),
        payment_webhook_secret=os.environ.get("PAYMENT_WEBHOOK_SECRET", ""),
        payment_return_url=os.environ.get("PAYMENT_RETURN_URL", "http://localhost:5000/h5#/account").strip(),
        enable_dev_payment_webhook=_parse_bool("ENABLE_DEV_PAYMENT_WEBHOOK", False),
```

Add validation:

```python
    if cfg.is_prod and not cfg.beta_auth_secret:
        raise ValueError("BETA_AUTH_SECRET is required when APP_ENV=prod")
    if cfg.is_prod and not cfg.payment_webhook_secret:
        raise ValueError("PAYMENT_WEBHOOK_SECRET is required when APP_ENV=prod")
    if cfg.is_prod and cfg.enable_dev_payment_webhook:
        raise ValueError("ENABLE_DEV_PAYMENT_WEBHOOK must be false when APP_ENV=prod")
```

- [ ] **Step 3: Update `.env.example`**

Append:

```text
BETA_AUTH_SECRET=
BETA_SESSION_TTL_HOURS=720
PAYMENT_PROVIDER=mock
PAYMENT_WEBHOOK_SECRET=
PAYMENT_RETURN_URL=http://localhost:5000/h5#/account
ENABLE_DEV_PAYMENT_WEBHOOK=false
```

- [ ] **Step 4: Verify**

Run:

```powershell
python -m unittest tests.test_runtime_config
```

Expected:

```text
OK
```

## Task 2: Signed Beta Session Tokens

**Files:**

- Create: `src/auth/session_tokens.py`
- Create: `tests/test_session_tokens.py`

- [ ] **Step 1: Write token tests**

Create `tests/test_session_tokens.py`:

```python
import time
import unittest

from src.auth.session_tokens import create_session_token, verify_session_token


class SessionTokenTests(unittest.TestCase):
    def test_roundtrip(self):
        token = create_session_token("user_1", "secret", ttl_seconds=3600, now=1000)
        payload = verify_session_token(token, "secret", now=1001)
        self.assertEqual(payload["user_id"], "user_1")

    def test_tampered_token_rejected(self):
        token = create_session_token("user_1", "secret", ttl_seconds=3600, now=1000)
        bad = token[:-2] + "xx"
        with self.assertRaises(ValueError):
            verify_session_token(bad, "secret", now=1001)

    def test_expired_token_rejected(self):
        token = create_session_token("user_1", "secret", ttl_seconds=10, now=1000)
        with self.assertRaises(ValueError):
            verify_session_token(token, "secret", now=1011)
```

- [ ] **Step 2: Implement token helpers**

Create `src/auth/session_tokens.py`:

```python
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
    return _b64(hmac.new(secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).digest())


def create_session_token(user_id: str, secret: str, ttl_seconds: int, now: int | None = None) -> str:
    if not user_id:
        raise ValueError("user_id required")
    if not secret:
        raise ValueError("secret required")
    ts = int(time.time() if now is None else now)
    payload = {"user_id": user_id, "iat": ts, "exp": ts + ttl_seconds}
    payload_b64 = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"{payload_b64}.{_sign(payload_b64, secret)}"


def verify_session_token(token: str, secret: str, now: int | None = None) -> dict[str, Any]:
    if not secret:
        raise ValueError("secret required")
    try:
        payload_b64, sig = token.split(".", 1)
    except ValueError as exc:
        raise ValueError("invalid token") from exc
    expected = _sign(payload_b64, secret)
    if not hmac.compare_digest(sig, expected):
        raise ValueError("invalid token signature")
    payload = json.loads(_unb64(payload_b64).decode("utf-8"))
    ts = int(time.time() if now is None else now)
    if ts > int(payload["exp"]):
        raise ValueError("token expired")
    return payload
```

- [ ] **Step 3: Verify**

Run:

```powershell
python -m unittest tests.test_session_tokens
```

Expected:

```text
OK
```

## Task 3: API Context Uses Signed Token

**Files:**

- Modify: `src/api/context.py`
- Modify: `src/api/app.py`
- Test: `tests/test_api_context_validation.py`
- Test: `tests/test_api_app.py`

- [ ] **Step 1: Add context tests**

Add tests that prove:

- `Authorization: Bearer <token>` sets `ctx.user_id`.
- Invalid token returns `unauthorized`.
- `X-User-Id` remains allowed in `APP_ENV=dev`.
- `X-User-Id` is rejected or ignored in `APP_ENV=prod` unless paired with valid token.

Test shape:

```python
def test_context_uses_bearer_token(self):
    from src.auth.session_tokens import create_session_token
    token = create_session_token("paid_1", "secret", ttl_seconds=3600, now=1000)
    # patch runtime config secret, build request context inside Flask request
```

- [ ] **Step 2: Implement context token resolution**

In `src/api/context.py`, add:

```python
def _bearer_token(header: str | None) -> str | None:
    if not header:
        return None
    prefix = "Bearer "
    return header[len(prefix):].strip() if header.startswith(prefix) else None
```

Use `verify_session_token()` when `Authorization` is present. If invalid, raise `ApiError("unauthorized", "Invalid session token", 401)`.

In dev/test, keep `X-User-Id` fallback. In prod, do not trust `X-User-Id` without a valid token.

- [ ] **Step 3: Add session endpoint**

In `src/api/app.py`, add:

```python
@app.route("/api/auth/session", methods=["POST"])
def create_beta_session():
    ctx = _ctx()
    payload = get_json_body()
    require_fields(payload, ["user_id"])
    cfg = load_runtime_config()
    if cfg.is_prod:
        raise ApiError("not_available", "Direct beta session creation is disabled in production", status_code=403)
    token = create_session_token(
        payload["user_id"],
        cfg.beta_auth_secret or "dev-secret",
        ttl_seconds=cfg.beta_session_ttl_hours * 3600,
    )
    return api_success({"token": token, "user_id": payload["user_id"]}, ctx.request_id)
```

- [ ] **Step 4: Verify**

Run:

```powershell
python -m unittest tests.test_api_context_validation tests.test_api_app
```

Expected:

```text
OK
```

## Task 4: Payment Gateway Interface

**Files:**

- Create: `src/billing/payment_gateway.py`
- Create: `tests/test_payment_gateway.py`
- Modify: `src/billing/subscription_service.py`

- [ ] **Step 1: Define gateway tests**

Create `tests/test_payment_gateway.py`:

```python
import unittest

from src.billing.payment_gateway import MockPaymentGateway, verify_webhook_signature


class PaymentGatewayTests(unittest.TestCase):
    def test_mock_checkout_returns_payment_url(self):
        gateway = MockPaymentGateway(base_url="http://localhost:5000", return_url="http://localhost:5000/h5#/account")
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
```

- [ ] **Step 2: Implement gateway**

Create `src/billing/payment_gateway.py`:

```python
import hashlib
import hmac
from dataclasses import dataclass


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_webhook_signature(body: bytes, signature: str, secret: str) -> bool:
    if not secret:
        return False
    return hmac.compare_digest(signature, _sign(body, secret))


verify_webhook_signature.sign = _sign


@dataclass
class MockPaymentGateway:
    base_url: str
    return_url: str

    def create_checkout(self, order_id: str, amount_cents: int, currency: str) -> dict:
        return {
            "provider": "mock",
            "payment_url": f"{self.base_url.rstrip()}/h5#/subscribe?order_id={order_id}",
            "amount_cents": amount_cents,
            "currency": currency,
            "return_url": self.return_url,
        }
```

- [ ] **Step 3: Verify**

Run:

```powershell
python -m unittest tests.test_payment_gateway
```

Expected:

```text
OK
```

## Task 5: Signed Payment Webhook

**Files:**

- Modify: `src/api/app.py`
- Modify: `src/billing/subscription_service.py`
- Test: `tests/test_api_billing.py`
- Test: `tests/test_subscription_service.py`

- [ ] **Step 1: Add webhook tests**

Add tests:

- Missing `X-Payment-Signature` returns `401`.
- Invalid signature returns `401`.
- Valid signature processes `payment.succeeded`.
- Replayed event id remains idempotent.

- [ ] **Step 2: Add real webhook route**

In `src/api/app.py`, add:

```python
@app.route("/api/billing/webhook", methods=["POST"])
def payment_webhook():
    ctx = _ctx()
    cfg = load_runtime_config()
    raw = request.get_data()
    signature = request.headers.get("X-Payment-Signature", "")
    if not verify_webhook_signature(raw, signature, cfg.payment_webhook_secret or "dev-webhook-secret"):
        raise ApiError("unauthorized", "Invalid payment signature", status_code=401)
    payload = get_json_body()
    require_fields(payload, ["event_id", "order_id", "event_type"])
    try:
        result = _billing_service.handle_payment_event(payload)
    except ValueError as e:
        raise ApiError("billing_error", str(e), status_code=400)
    return api_success(result, ctx.request_id)
```

- [ ] **Step 3: Guard mock webhook**

Modify `/api/billing/webhook/mock`:

```python
    cfg = load_runtime_config()
    if not cfg.enable_dev_payment_webhook:
        raise ApiError("not_found", "Mock webhook disabled", status_code=404)
```

- [ ] **Step 4: Verify**

Run:

```powershell
python -m unittest tests.test_api_billing tests.test_subscription_service
```

Expected:

```text
OK
```

## Task 6: Billing Audit Log

**Files:**

- Create: `src/billing/audit_log.py`
- Create: `tests/test_billing_audit_log.py`
- Modify: `src/billing/subscription_service.py`

- [ ] **Step 1: Add audit tests**

Create tests proving:

- Appends one record.
- Preserves event order.
- Does not crash when log file does not exist.

- [ ] **Step 2: Implement audit log**

Create `src/billing/audit_log.py`:

```python
import json
from datetime import datetime, timezone
from pathlib import Path


class BillingAuditLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event_type: str, payload: dict) -> dict:
        record = {
            "event_type": event_type,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        records = []
        if self.path.exists():
            records = json.loads(self.path.read_text(encoding="utf-8"))
        records.append(record)
        self.path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        return record
```

- [ ] **Step 3: Wire service**

In `SubscriptionService`, accept optional `audit_log`. Emit:

- `checkout.created`
- `payment.succeeded`
- `payment.already_paid`
- `subscription.activated`

- [ ] **Step 4: Verify**

Run:

```powershell
python -m unittest tests.test_billing_audit_log tests.test_subscription_service
```

Expected:

```text
OK
```

## Task 7: H5 Auth And Checkout Updates

**Files:**

- Modify: `frontend/h5/state.js`
- Modify: `frontend/h5/api.js`
- Modify: `frontend/h5/views/account.js`
- Modify: `frontend/h5/views/subscribe.js`
- Test: `tests/test_h5_files.py`

- [ ] **Step 1: Update frontend state**

Add `sessionToken` and localStorage key:

```javascript
state.sessionToken = localStorage.getItem("wc_predict_session_token");
```

Add:

```javascript
export function setSessionToken(token) {
  state.sessionToken = token || null;
  if (state.sessionToken) localStorage.setItem("wc_predict_session_token", state.sessionToken);
  else localStorage.removeItem("wc_predict_session_token");
}
```

- [ ] **Step 2: Update API headers**

In `frontend/h5/api.js`, add token header:

```javascript
if (options.token) result.Authorization = `Bearer ${options.token}`;
```

Pass `state.sessionToken` to subscription, checkout, prediction, and VIP prediction calls.

- [ ] **Step 3: Add account session action**

In `account.js`, call `/api/auth/session` in dev after user enters user id. Save returned token.

- [ ] **Step 4: Use payment URL**

In `subscribe.js`, after checkout:

```javascript
if (data.payment_url) {
  window.location.href = data.payment_url;
} else {
  showToast(`订单已创建：${data.order_id}`);
}
```

- [ ] **Step 5: Add static tests**

In `tests/test_h5_files.py`, assert:

```python
self.assertIn("wc_predict_session_token", state_js)
self.assertIn("Authorization", api_js)
self.assertIn("/api/auth/session", account_js)
self.assertIn("payment_url", subscribe_js)
```

- [ ] **Step 6: Verify**

Run:

```powershell
python -m unittest tests.test_h5_files tests.test_h5_static
```

Expected:

```text
OK
```

## Task 8: Documentation And Launch Runbook

**Files:**

- Create: `docs/beta_launch_runbook.md`
- Modify: `docs/api_billing_contract.md`
- Modify: `docs/launch_checklist.md`

- [ ] **Step 1: Create beta runbook**

Create `docs/beta_launch_runbook.md` with:

```markdown
# Beta Launch Runbook

## Before Opening Sales

- Run `python -m unittest discover -s tests`.
- Run `python src/smoke_test_launch.py`.
- Set `APP_ENV=prod`.
- Set `BETA_AUTH_SECRET`.
- Set `PAYMENT_WEBHOOK_SECRET`.
- Keep `ENABLE_DEV_PAYMENT_WEBHOOK=false`.
- Confirm `/health/live` returns 200.
- Confirm `/health` returns 200 or expected degraded details.

## Payment Flow

1. User opens `/h5#/account`.
2. User obtains beta session.
3. User selects VIP plan.
4. `/api/billing/checkout` creates order.
5. Provider redirects or confirms payment.
6. Provider sends signed `/api/billing/webhook`.
7. Subscription becomes VIP.

## Incident Rollback

- Disable sales by removing checkout UI or blocking `/api/billing/checkout`.
- Keep prediction access for already paid users.
- Preserve `data/billing`.
- Preserve `data/prediction_snapshots`.
```

- [ ] **Step 2: Update billing contract**

Document:

- `POST /api/auth/session`
- `POST /api/billing/webhook`
- `X-Payment-Signature`
- Dev-only `/api/billing/webhook/mock`

- [ ] **Step 3: Update launch checklist**

Add:

- Signed token checked.
- Mock webhook disabled in prod.
- Webhook signature tested.
- Audit log writable.
- Paid user can unlock VIP from H5.

## Task 9: Final Verification

**Files:**

- No source files.

- [ ] **Step 1: Run targeted tests**

Run:

```powershell
python -m unittest tests.test_runtime_config tests.test_session_tokens tests.test_payment_gateway tests.test_api_context_validation tests.test_api_billing tests.test_subscription_service tests.test_h5_files
```

Expected:

```text
OK
```

- [ ] **Step 2: Run full regression**

Run:

```powershell
python -m unittest discover -s tests
```

Expected:

```text
OK
```

- [ ] **Step 3: Run launch smoke**

Run:

```powershell
python src/smoke_test_launch.py
```

Expected:

```json
{"ok": true, "failed_count": 0}
```

- [ ] **Step 4: Manual H5 paid beta smoke**

Start:

```powershell
python src/api/app.py
```

Open:

```text
http://localhost:5000/h5#/account
```

Check:

- Create beta session.
- Open subscribe page.
- Create checkout order.
- Confirm `payment_url` is shown or navigated.
- Simulate signed webhook in dev.
- Account page shows VIP.
- Prediction detail unlocks VIP fields.

## Acceptance Criteria

- Public beta no longer relies on spoofable `X-User-Id` alone.
- Production rejects missing auth/payment secrets.
- Mock payment webhook is disabled unless explicitly enabled.
- Real webhook endpoint requires HMAC signature.
- Checkout produces a payment URL or explicit pending payment state.
- Billing events are audit logged.
- H5 stores and sends signed session token.
- Full test suite passes.
- Beta runbook exists and matches implementation.

## Suggested Execution Order

1. Runtime config.
2. Session tokens.
3. API context auth.
4. Payment gateway abstraction.
5. Signed webhook.
6. Audit log.
7. H5 auth/checkout.
8. Docs.
9. Full verification.

## Step 10H Candidates

- Real WeChat Pay or Stripe adapter.
- Proper login via SMS/email/OAuth.
- Database migration.
- Admin billing dashboard.
- Refund and access revocation.
- Production telemetry dashboard.

