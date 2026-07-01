# Step 10C API Service Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the current Flask API from a working internal endpoint set into a stable service surface suitable for H5/mini-program clients and paid users.

**Architecture:** Keep Flask for this step and harden the service around it: shared response envelope, explicit API errors, request validation, auth context, rate limiting, cache policy, pagination, and endpoint contract tests. Do not introduce a new framework or database migration in this step; Step 10C should stabilize behavior without changing the prediction or billing core.

**Tech Stack:** Python stdlib, Flask, unittest, existing JSON-backed billing/cache stores, existing `PredictionService`, existing Step 10A/10B API endpoints.

---

## Scope

Step 10C covers API service quality only.

In scope:

- Stable response envelope for success and error responses.
- Shared `ApiError` exception and Flask error handlers.
- Request context: request id, user id, tier, admin flag.
- Request validation for JSON bodies and required headers.
- Route-level auth helpers for anonymous, authenticated, VIP, admin.
- Basic in-process rate limiting.
- Prediction response cache metadata and no-store/no-cache headers.
- Pagination helpers for list endpoints.
- API contract tests for response shapes and error codes.
- Documentation update for H5/mini-program integration.

Out of scope:

- Real payment provider signature verification.
- SQLite/Postgres migration.
- Frontend implementation.
- Admin dashboard.
- CDN, nginx, deployment scripts.

## File Map

- Create `src/api/errors.py`: `ApiError`, error codes, Flask error handler registration.
- Create `src/api/responses.py`: `api_success()`, `api_error_response()`, response envelope helpers.
- Create `src/api/context.py`: request context extraction, request id, user id, tier, admin checks.
- Create `src/api/validation.py`: JSON body parsing and required-field validation.
- Create `src/api/rate_limit.py`: small in-memory fixed-window limiter.
- Create `src/api/cache_headers.py`: cache-control helpers for prediction and billing endpoints.
- Create `src/api/pagination.py`: parse `limit`/`cursor`, produce list envelope.
- Modify `src/api/app.py`: register error handlers, use response helpers, use auth context, add cache headers, add list pagination for match endpoints.
- Modify `src/auth/access_control.py`: no behavior change unless tests reveal helper mismatch.
- Modify `docs/api_prediction_contract.md`: update response envelope and error format.
- Modify `docs/api_billing_contract.md`: update response envelope and auth requirements.
- Create `tests/test_api_errors_responses.py`: response/error envelope unit tests.
- Create `tests/test_api_context_validation.py`: request context and validation tests.
- Create `tests/test_api_rate_limit.py`: limiter behavior tests.
- Create `tests/test_api_contract.py`: end-to-end route contract tests for prediction and billing endpoints.
- Modify `tests/test_api_app.py`: adapt existing expectations to new envelope.
- Modify `tests/test_api_billing.py`: adapt existing expectations to new envelope.

## API Response Contract

All successful responses should use this shape:

```json
{
  "ok": true,
  "data": {},
  "meta": {
    "request_id": "req_...",
    "generated_at": "2026-06-29T00:00:00+00:00"
  }
}
```

All error responses should use this shape:

```json
{
  "ok": false,
  "error": {
    "code": "vip_required",
    "message": "VIP access required",
    "details": {}
  },
  "meta": {
    "request_id": "req_...",
    "generated_at": "2026-06-29T00:00:00+00:00"
  }
}
```

Recommended error codes:

- `bad_request`
- `unauthorized`
- `forbidden`
- `not_found`
- `vip_required`
- `validation_error`
- `rate_limited`
- `prediction_failed`
- `billing_error`
- `upstream_unavailable`
- `internal_error`

## Endpoint Policy

`GET /health`

- Keep existing plain health response for infrastructure compatibility.
- Do not wrap `/health` in the standard envelope.

`GET /api/predictions/<fixture_id>`

- Public endpoint.
- Uses `X-User-Id` if present.
- Free/basic users receive filtered prediction fields.
- VIP/admin users receive VIP fields.
- Response includes `meta.source`: `cache` or `live`.
- Response includes `meta.tier`: effective tier.
- HTTP status:
  - `200` success
  - `400` invalid fixture id
  - `503` prediction unavailable

`GET /api/predictions/<fixture_id>/vip`

- Requires `vip` or `admin`.
- HTTP status:
  - `200` success
  - `403` non-VIP
  - `400` invalid fixture id
  - `503` prediction unavailable

`GET /api/matches/today`

- Public endpoint.
- Response `data.matches` and `data.count`.
- Add `limit` query parameter with max `100`.
- Upstream failure returns `200` with empty list and `meta.warning`.

`GET /api/me/subscription`

- Requires `X-User-Id`.
- Anonymous should return `401`, not `"anonymous"`.

`POST /api/billing/checkout`

- Requires `X-User-Id`.
- Requires JSON body with `plan_id`.
- Bad plan returns `400 validation_error`.

`POST /api/billing/webhook/mock`

- Keep mock endpoint for dev/testing.
- Requires JSON body with `event_id`, `order_id`, `event_type`.
- Unknown order returns `400 billing_error`.
- Existing idempotency behavior remains.

## Task 1: Response Envelope And API Errors

**Files:**

- Create: `src/api/responses.py`
- Create: `src/api/errors.py`
- Create: `tests/test_api_errors_responses.py`

- [ ] **Step 1: Write tests**

Create `tests/test_api_errors_responses.py`:

```python
import unittest

from src.api.errors import ApiError
from src.api.responses import build_error_payload, build_success_payload


class ApiResponsesTests(unittest.TestCase):
    def test_success_payload_shape(self):
        payload = build_success_payload({"value": 1}, request_id="req_test")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["value"], 1)
        self.assertEqual(payload["meta"]["request_id"], "req_test")
        self.assertIn("generated_at", payload["meta"])

    def test_error_payload_shape(self):
        err = ApiError("vip_required", "VIP access required", status_code=403)
        payload = build_error_payload(err, request_id="req_test")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "vip_required")
        self.assertEqual(payload["error"]["message"], "VIP access required")
        self.assertEqual(payload["meta"]["request_id"], "req_test")

    def test_api_error_defaults(self):
        err = ApiError("bad_request", "Bad request")
        self.assertEqual(err.status_code, 400)
        self.assertEqual(err.details, {})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run failing tests**

Run:

```powershell
python -m unittest tests.test_api_errors_responses
```

Expected: fail because files do not exist.

- [ ] **Step 3: Implement `src/api/errors.py`**

```python
class ApiError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: dict | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
```

- [ ] **Step 4: Implement `src/api/responses.py`**

```python
from datetime import datetime, timezone
from typing import Any

from flask import jsonify

from src.api.errors import ApiError


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_success_payload(
    data: Any,
    request_id: str,
    meta: dict | None = None,
) -> dict:
    payload_meta = {"request_id": request_id, "generated_at": _now_iso()}
    if meta:
        payload_meta.update(meta)
    return {"ok": True, "data": data, "meta": payload_meta}


def build_error_payload(error: ApiError, request_id: str) -> dict:
    return {
        "ok": False,
        "error": {
            "code": error.code,
            "message": error.message,
            "details": error.details,
        },
        "meta": {"request_id": request_id, "generated_at": _now_iso()},
    }


def api_success(data: Any, request_id: str, meta: dict | None = None, status_code: int = 200):
    return jsonify(build_success_payload(data, request_id, meta)), status_code


def api_error_response(error: ApiError, request_id: str):
    return jsonify(build_error_payload(error, request_id)), error.status_code
```

- [ ] **Step 5: Run tests**

Run:

```powershell
python -m unittest tests.test_api_errors_responses
```

Expected: OK.

## Task 2: Request Context And Auth Helpers

**Files:**

- Create: `src/api/context.py`
- Create: `tests/test_api_context_validation.py`

- [ ] **Step 1: Write context tests**

Create `tests/test_api_context_validation.py` with the context tests first:

```python
import unittest
from unittest.mock import MagicMock

from src.api.app import app
from src.api.context import build_request_context
from src.api.errors import ApiError


class ApiContextTests(unittest.TestCase):
    def test_context_defaults_free(self):
        fake_billing = MagicMock()
        fake_billing.resolve_tier.return_value = "free"
        with app.test_request_context("/api/test"):
            ctx = build_request_context(fake_billing)
            self.assertEqual(ctx.user_id, "anonymous")
            self.assertEqual(ctx.tier, "free")
            self.assertFalse(ctx.is_admin)

    def test_context_uses_user_id_header(self):
        fake_billing = MagicMock()
        fake_billing.resolve_tier.return_value = "vip"
        with app.test_request_context("/api/test", headers={"X-User-Id": "u1", "X-Request-Id": "req_in"}):
            ctx = build_request_context(fake_billing)
            self.assertEqual(ctx.user_id, "u1")
            self.assertEqual(ctx.request_id, "req_in")
            self.assertEqual(ctx.tier, "vip")

    def test_require_user_rejects_anonymous(self):
        fake_billing = MagicMock()
        fake_billing.resolve_tier.return_value = "free"
        with app.test_request_context("/api/test"):
            ctx = build_request_context(fake_billing)
            with self.assertRaises(ApiError) as raised:
                ctx.require_user()
            self.assertEqual(raised.exception.status_code, 401)

    def test_require_vip_rejects_free(self):
        fake_billing = MagicMock()
        fake_billing.resolve_tier.return_value = "free"
        with app.test_request_context("/api/test", headers={"X-User-Id": "u1"}):
            ctx = build_request_context(fake_billing)
            with self.assertRaises(ApiError) as raised:
                ctx.require_tier("vip")
            self.assertEqual(raised.exception.code, "vip_required")
```

- [ ] **Step 2: Run failing tests**

Run:

```powershell
python -m unittest tests.test_api_context_validation.ApiContextTests
```

Expected: fail because context module does not exist.

- [ ] **Step 3: Implement `src/api/context.py`**

```python
import os
import uuid
from dataclasses import dataclass

from flask import request

from src.api.errors import ApiError
from src.auth.access_control import TIER_HIERARCHY, resolve_user_tier_from_service


@dataclass
class ApiRequestContext:
    request_id: str
    user_id: str
    tier: str

    @property
    def is_authenticated(self) -> bool:
        return self.user_id != "anonymous"

    @property
    def is_admin(self) -> bool:
        return self.tier == "admin"

    def require_user(self) -> None:
        if not self.is_authenticated:
            raise ApiError("unauthorized", "X-User-Id required", status_code=401)

    def require_tier(self, required_tier: str) -> None:
        user_level = TIER_HIERARCHY.get(self.tier, 0)
        required_level = TIER_HIERARCHY.get(required_tier, 0)
        if user_level < required_level:
            code = "vip_required" if required_tier == "vip" else "forbidden"
            raise ApiError(code, f"{required_tier.upper()} access required", status_code=403)


def _request_id() -> str:
    return request.headers.get("X-Request-Id") or f"req_{uuid.uuid4().hex[:16]}"


def _user_id() -> str:
    user_id = (request.headers.get("X-User-Id", "") or "").strip()
    return user_id or "anonymous"


def build_request_context(billing_service) -> ApiRequestContext:
    user_id = _user_id()
    if os.environ.get("APP_ENV") == "dev":
        tier_hint = request.headers.get("X-User-Tier", "")
        if tier_hint in TIER_HIERARCHY:
            return ApiRequestContext(_request_id(), user_id, tier_hint)
    tier = resolve_user_tier_from_service(user_id, billing_service)
    return ApiRequestContext(_request_id(), user_id, tier)
```

- [ ] **Step 4: Run context tests**

Run:

```powershell
python -m unittest tests.test_api_context_validation.ApiContextTests
```

Expected: OK.

## Task 3: Request Validation

**Files:**

- Create: `src/api/validation.py`
- Modify: `tests/test_api_context_validation.py`

- [ ] **Step 1: Add validation tests**

Append to `tests/test_api_context_validation.py`:

```python
from src.api.validation import get_json_body, require_fields


class ApiValidationTests(unittest.TestCase):
    def test_get_json_body_rejects_non_object(self):
        with app.test_request_context("/api/test", method="POST", json=["bad"]):
            with self.assertRaises(ApiError) as raised:
                get_json_body()
            self.assertEqual(raised.exception.code, "validation_error")

    def test_require_fields_reports_missing(self):
        with self.assertRaises(ApiError) as raised:
            require_fields({"plan_id": "vip_day"}, ["plan_id", "event_id"])
        self.assertEqual(raised.exception.details["missing"], ["event_id"])

    def test_require_fields_accepts_present_values(self):
        require_fields({"plan_id": "vip_day"}, ["plan_id"])
```

- [ ] **Step 2: Run failing tests**

Run:

```powershell
python -m unittest tests.test_api_context_validation.ApiValidationTests
```

Expected: fail because validation module does not exist.

- [ ] **Step 3: Implement `src/api/validation.py`**

```python
from flask import request

from src.api.errors import ApiError


def get_json_body() -> dict:
    payload = request.get_json(silent=True)
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ApiError("validation_error", "JSON body must be an object", status_code=400)
    return payload


def require_fields(payload: dict, fields: list[str]) -> None:
    missing = [field for field in fields if payload.get(field) in (None, "")]
    if missing:
        raise ApiError(
            "validation_error",
            "Missing required fields",
            status_code=400,
            details={"missing": missing},
        )
```

- [ ] **Step 4: Run validation tests**

Run:

```powershell
python -m unittest tests.test_api_context_validation
```

Expected: OK.

## Task 4: Rate Limiting

**Files:**

- Create: `src/api/rate_limit.py`
- Create: `tests/test_api_rate_limit.py`

- [ ] **Step 1: Write rate-limit tests**

Create `tests/test_api_rate_limit.py`:

```python
import time
import unittest

from src.api.errors import ApiError
from src.api.rate_limit import FixedWindowRateLimiter


class RateLimitTests(unittest.TestCase):
    def test_allows_within_limit(self):
        limiter = FixedWindowRateLimiter(limit=2, window_seconds=60)
        limiter.check("u1")
        limiter.check("u1")

    def test_rejects_over_limit(self):
        limiter = FixedWindowRateLimiter(limit=1, window_seconds=60)
        limiter.check("u1")
        with self.assertRaises(ApiError) as raised:
            limiter.check("u1")
        self.assertEqual(raised.exception.code, "rate_limited")
        self.assertEqual(raised.exception.status_code, 429)

    def test_window_resets(self):
        limiter = FixedWindowRateLimiter(limit=1, window_seconds=0.01)
        limiter.check("u1")
        time.sleep(0.02)
        limiter.check("u1")
```

- [ ] **Step 2: Run failing tests**

Run:

```powershell
python -m unittest tests.test_api_rate_limit
```

Expected: fail because limiter module does not exist.

- [ ] **Step 3: Implement `src/api/rate_limit.py`**

```python
import time

from src.api.errors import ApiError


class FixedWindowRateLimiter:
    def __init__(self, limit: int, window_seconds: float):
        self.limit = limit
        self.window_seconds = window_seconds
        self._buckets: dict[str, tuple[float, int]] = {}

    def check(self, key: str) -> None:
        now = time.time()
        window_start, count = self._buckets.get(key, (now, 0))
        if now - window_start >= self.window_seconds:
            window_start, count = now, 0
        count += 1
        self._buckets[key] = (window_start, count)
        if count > self.limit:
            raise ApiError("rate_limited", "Too many requests", status_code=429)
```

- [ ] **Step 4: Run rate-limit tests**

Run:

```powershell
python -m unittest tests.test_api_rate_limit
```

Expected: OK.

## Task 5: Cache Headers And Pagination Helpers

**Files:**

- Create: `src/api/cache_headers.py`
- Create: `src/api/pagination.py`
- Create: `tests/test_api_contract.py`

- [ ] **Step 1: Write helper tests**

Create first part of `tests/test_api_contract.py`:

```python
import unittest

from src.api.cache_headers import apply_no_store, apply_prediction_cache_headers
from src.api.pagination import parse_limit


class ApiHelperContractTests(unittest.TestCase):
    def test_parse_limit_defaults(self):
        self.assertEqual(parse_limit(None), 20)

    def test_parse_limit_caps_max(self):
        self.assertEqual(parse_limit("500", max_limit=100), 100)

    def test_parse_limit_rejects_bad_value(self):
        with self.assertRaises(ValueError):
            parse_limit("bad")

    def test_cache_header_helpers(self):
        headers = {}
        apply_no_store(headers)
        self.assertEqual(headers["Cache-Control"], "no-store")
        headers = {}
        apply_prediction_cache_headers(headers)
        self.assertIn("max-age", headers["Cache-Control"])
```

- [ ] **Step 2: Implement helpers**

Create `src/api/cache_headers.py`:

```python
def apply_no_store(headers) -> None:
    headers["Cache-Control"] = "no-store"


def apply_prediction_cache_headers(headers, max_age_seconds: int = 60) -> None:
    headers["Cache-Control"] = f"private, max-age={max_age_seconds}"
```

Create `src/api/pagination.py`:

```python
def parse_limit(raw: str | None, default: int = 20, max_limit: int = 100) -> int:
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError("limit must be an integer")
    if value < 1:
        raise ValueError("limit must be positive")
    return min(value, max_limit)
```

- [ ] **Step 3: Run helper tests**

Run:

```powershell
python -m unittest tests.test_api_contract.ApiHelperContractTests
```

Expected: OK.

## Task 6: Wire Standard API Layer Into Flask App

**Files:**

- Modify: `src/api/app.py`
- Modify: `tests/test_api_app.py`
- Modify: `tests/test_api_billing.py`

- [ ] **Step 1: Add route contract tests**

Add to `tests/test_api_contract.py`:

```python
from unittest.mock import MagicMock, patch

from src.api.app import app


class ApiEnvelopeRouteTests(unittest.TestCase):
    def test_prediction_success_uses_envelope(self):
        cached = {
            "match_id": "sportmonks-1",
            "home_team": "A",
            "away_team": "B",
            "probabilities": {"home": 0.4, "draw": 0.3, "away": 0.3},
            "top_scorelines": [],
            "confidence": 0.7,
        }
        fake_billing = MagicMock()
        fake_billing.resolve_tier.return_value = "free"
        with patch("src.api.app._billing_service", fake_billing):
            with patch("src.api.app.load_latest_prediction", return_value=cached):
                with app.test_client() as c:
                    r = c.get("/api/predictions/1", headers={"X-Request-Id": "req_test"})
                    body = r.get_json()
                    self.assertTrue(body["ok"])
                    self.assertIn("data", body)
                    self.assertEqual(body["meta"]["request_id"], "req_test")

    def test_prediction_bad_id_uses_error_envelope(self):
        with app.test_client() as c:
            r = c.get("/api/predictions/bad", headers={"X-Request-Id": "req_test"})
            body = r.get_json()
            self.assertEqual(r.status_code, 400)
            self.assertFalse(body["ok"])
            self.assertEqual(body["error"]["code"], "bad_request")

    def test_vip_endpoint_forbidden_uses_error_envelope(self):
        fake_billing = MagicMock()
        fake_billing.resolve_tier.return_value = "free"
        with patch("src.api.app._billing_service", fake_billing):
            with app.test_client() as c:
                r = c.get("/api/predictions/1/vip")
                body = r.get_json()
                self.assertEqual(r.status_code, 403)
                self.assertEqual(body["error"]["code"], "vip_required")

    def test_checkout_missing_user_uses_error_envelope(self):
        with app.test_client() as c:
            r = c.post("/api/billing/checkout", json={"plan_id": "vip_day"})
            body = r.get_json()
            self.assertEqual(r.status_code, 401)
            self.assertEqual(body["error"]["code"], "unauthorized")
```

- [ ] **Step 2: Refactor `src/api/app.py`**

Import new helpers:

```python
from src.api.cache_headers import apply_no_store, apply_prediction_cache_headers
from src.api.context import build_request_context
from src.api.errors import ApiError
from src.api.pagination import parse_limit
from src.api.responses import api_error_response, api_success
from src.api.validation import get_json_body, require_fields
```

Add helper:

```python
def _ctx():
    return build_request_context(_billing_service)
```

Replace `_get_user_id()` and `_get_user_tier()` usages gradually with `_ctx()`.

Example prediction route shape:

```python
@app.route("/api/predictions/<fixture_id>")
def get_prediction(fixture_id: str):
    ctx = _ctx()
    try:
        fid = _resolve_fixture_id(fixture_id)
    except ValueError as e:
        return api_error_response(ApiError("bad_request", str(e), status_code=400), ctx.request_id)

    cached = load_latest_prediction(f"sportmonks-{fid}")
    if cached:
        payload = filter_prediction_payload(cached, ctx.tier)
        payload["_source"] = "cache"
        if ctx.is_admin:
            payload = add_debug_info(payload, cached.get("_fallback", []), cached.get("_warnings", []))
        response, code = api_success(payload, ctx.request_id, meta={"source": "cache", "tier": ctx.tier})
        apply_prediction_cache_headers(response.headers)
        return response, code

    try:
        result = _service.predict_match(fixture_id=fid, use_prematch_api=True)
    except Exception as e:
        return api_error_response(ApiError("prediction_failed", f"Prediction failed: {e}", status_code=503), ctx.request_id)

    internal = result.snapshot.to_public_dict(tier="internal")
    payload = filter_prediction_payload(internal, ctx.tier)
    payload["_source"] = "live"
    if ctx.is_admin:
        payload = add_debug_info(payload, result.fallback_used, result.warnings)
    response, code = api_success(payload, ctx.request_id, meta={"source": "live", "tier": ctx.tier})
    apply_prediction_cache_headers(response.headers)
    return response, code
```

For billing routes, use:

```python
ctx = _ctx()
ctx.require_user()
payload = get_json_body()
require_fields(payload, ["plan_id"])
```

Catch `ApiError` inside each route or add an app-level error handler:

```python
@app.errorhandler(ApiError)
def handle_api_error(error):
    ctx = _ctx()
    return api_error_response(error, ctx.request_id)
```

For `/api/me/subscription`, return `401` if no `X-User-Id`, per Step 10C endpoint policy.

- [ ] **Step 3: Update existing tests**

Existing tests in `tests/test_api_app.py` and `tests/test_api_billing.py` currently read fields directly from `r.get_json()`. Update them to read:

```python
body = r.get_json()
data = body["data"]
```

For error tests, update to:

```python
body = r.get_json()
self.assertFalse(body["ok"])
self.assertEqual(body["error"]["code"], "validation_error")
```

- [ ] **Step 4: Run API tests**

Run:

```powershell
python -m unittest tests.test_api_contract tests.test_api_app tests.test_api_billing
```

Expected: OK.

## Task 7: Rate Limit Integration

**Files:**

- Modify: `src/api/app.py`
- Modify: `tests/test_api_contract.py`

- [ ] **Step 1: Add route-level rate-limit test**

Append to `tests/test_api_contract.py`:

```python
class ApiRateLimitRouteTests(unittest.TestCase):
    def test_checkout_rate_limit_returns_429(self):
        fake_limiter = MagicMock()
        fake_limiter.check.side_effect = Exception("rate limited")
        with patch("src.api.app._billing_limiter", fake_limiter):
            with app.test_client() as c:
                r = c.post("/api/billing/checkout", headers={"X-User-Id": "u1"}, json={"plan_id": "vip_day"})
                self.assertIn(r.status_code, (400, 429))
```

Better final test after `ApiError` integration:

```python
from src.api.errors import ApiError


class ApiRateLimitRouteTests(unittest.TestCase):
    def test_checkout_rate_limit_returns_429(self):
        fake_limiter = MagicMock()
        fake_limiter.check.side_effect = ApiError("rate_limited", "Too many requests", status_code=429)
        with patch("src.api.app._billing_limiter", fake_limiter):
            with app.test_client() as c:
                r = c.post("/api/billing/checkout", headers={"X-User-Id": "u1"}, json={"plan_id": "vip_day"})
                body = r.get_json()
                self.assertEqual(r.status_code, 429)
                self.assertEqual(body["error"]["code"], "rate_limited")
```

- [ ] **Step 2: Add limiters in app**

In `src/api/app.py`:

```python
from src.api.rate_limit import FixedWindowRateLimiter

_prediction_limiter = FixedWindowRateLimiter(limit=120, window_seconds=60)
_billing_limiter = FixedWindowRateLimiter(limit=20, window_seconds=60)
```

In prediction routes:

```python
_prediction_limiter.check(ctx.user_id)
```

In billing checkout:

```python
_billing_limiter.check(ctx.user_id)
```

Do not rate-limit `/health`.

- [ ] **Step 3: Run rate-limit route tests**

Run:

```powershell
python -m unittest tests.test_api_rate_limit tests.test_api_contract
```

Expected: OK.

## Task 8: Documentation Update

**Files:**

- Modify: `docs/api_prediction_contract.md`
- Modify: `docs/api_billing_contract.md`
- Create: `docs/api_error_codes.md`

- [ ] **Step 1: Update prediction contract**

Update examples in `docs/api_prediction_contract.md` to wrap payloads:

```json
{
  "ok": true,
  "data": {
    "match_id": "sportmonks-19606960",
    "probabilities": {"home": 0.4, "draw": 0.3, "away": 0.3}
  },
  "meta": {
    "request_id": "req_abc",
    "source": "cache",
    "tier": "vip"
  }
}
```

- [ ] **Step 2: Update billing contract**

Update `docs/api_billing_contract.md` so all endpoints show:

```json
{
  "ok": true,
  "data": {},
  "meta": {}
}
```

and error examples use:

```json
{
  "ok": false,
  "error": {
    "code": "validation_error",
    "message": "Missing required fields",
    "details": {"missing": ["plan_id"]}
  },
  "meta": {}
}
```

- [ ] **Step 3: Create `docs/api_error_codes.md`**

```markdown
# API Error Codes

| code | HTTP | Meaning |
|---|---:|---|
| bad_request | 400 | Invalid path/query parameter |
| validation_error | 400 | Missing or invalid JSON body |
| unauthorized | 401 | `X-User-Id` required |
| forbidden | 403 | Authenticated but insufficient permission |
| vip_required | 403 | VIP/admin required |
| rate_limited | 429 | Too many requests |
| prediction_failed | 503 | Prediction engine unavailable |
| billing_error | 400 | Billing/order operation failed |
| upstream_unavailable | 503 | External data provider unavailable |
| internal_error | 500 | Unexpected server failure |
```

## Task 9: Final Verification

**Files:**

- All Step 10C files

- [ ] **Step 1: Run focused tests**

Run:

```powershell
python -m unittest tests.test_api_errors_responses tests.test_api_context_validation tests.test_api_rate_limit tests.test_api_contract tests.test_api_app tests.test_api_billing
```

Expected: OK.

- [ ] **Step 2: Run existing billing/auth tests**

Run:

```powershell
python -m unittest tests.test_access_control tests.test_subscription_service tests.test_billing_models
```

Expected: OK.

- [ ] **Step 3: Run full test suite**

Run:

```powershell
python -m unittest discover -s tests
```

Expected: all tests pass.

- [ ] **Step 4: Manual API smoke**

Start server:

```powershell
python src/api/app.py
```

Run:

```powershell
curl http://localhost:5000/health
curl http://localhost:5000/api/predictions/not_a_number
curl http://localhost:5000/api/me/subscription
curl -X POST http://localhost:5000/api/billing/checkout -H "Content-Type: application/json" -H "X-User-Id: demo_user" -d "{\"plan_id\":\"vip_day\"}"
```

Expected:

- `/health` remains unwrapped.
- bad prediction id returns envelope with `ok=false`.
- `/api/me/subscription` without user returns `401`.
- checkout returns envelope with `ok=true`.

## Implementation Order

1. Task 1: response envelope and API error type.
2. Task 2: request context and auth helpers.
3. Task 3: JSON validation helpers.
4. Task 4: fixed-window rate limiter.
5. Task 5: cache header and pagination helpers.
6. Task 6: wire the helpers into Flask routes.
7. Task 7: integrate rate limiting into prediction and billing routes.
8. Task 8: update API docs.
9. Task 9: focused tests, full tests, manual smoke.

## Acceptance Criteria

- All non-health API endpoints return `ok/data/meta` or `ok/error/meta`.
- Every error path returns a stable `error.code`.
- `X-User-Id` is required for `/api/me/subscription` and `/api/billing/checkout`.
- `X-User-Tier` remains ignored unless `APP_ENV=dev`.
- VIP prediction endpoint returns `403 vip_required` for non-VIP users.
- Billing endpoints validate JSON and required fields before touching the service layer.
- Rate limiting returns `429 rate_limited`.
- Prediction responses include `meta.source` and `meta.tier`.
- `/health` remains compatible with existing monitoring.
- Full test suite passes.

## Known Follow-Ups For Step 10D

- Move scheduled refresh from script into a managed job runner.
- Add persisted job run records and retry counters.
- Add admin job health endpoints.
- Add stale prediction alerting.
- Add background refresh queue if traffic increases.
