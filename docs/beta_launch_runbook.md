# Beta Launch Runbook

## Before Opening Sales

- [ ] `python -m unittest discover -s tests` passes.
- [ ] `python src/smoke_test_launch.py` passes.
- [ ] Set `APP_ENV=prod`.
- [ ] Set `BETA_AUTH_SECRET` (random 32+ char string).
- [ ] Set `PAYMENT_WEBHOOK_SECRET` (random 32+ char string).
- [ ] Keep `ENABLE_DEV_PAYMENT_WEBHOOK=false`.
- [ ] Confirm `/health/live` returns 200.
- [ ] Confirm `/health` returns 200 or expected degraded details.
- [ ] `DATA_DIR` is backed up.

## Session Tokens

- [ ] `POST /api/auth/session` is disabled in prod (returns 403).
- [ ] Tokens are issued via a separate admin/CLI tool in prod, or via the H5 dev endpoint in dev/test.
- [ ] Token format: `{payload_b64}.{hmac_sha256_signature}`.
- [ ] Tokens expire based on `BETA_SESSION_TTL_HOURS` (default 720h = 30 days).

## Payment Flow

1. User opens `/h5#/account`.
2. User obtains beta session (dev: enter user_id, click save, session created).
3. User selects VIP plan on `/h5#/subscribe`.
4. `POST /api/billing/checkout` creates pending order, returns `payment_url`.
5. Provider redirects or confirms payment.
6. Provider sends signed `POST /api/billing/webhook` with `X-Payment-Signature` header.
7. Subscription becomes active (`vip` tier).

## Webhook Signature

- Signature: `HMAC-SHA256(body_bytes, webhook_secret)` as hex digest.
- Header: `X-Payment-Signature: <signature>`.
- Replayed event_ids are idempotent (return `idempotent: true`).

## Incident Rollback

- Disable sales: set `ENABLE_H5=false` or remove checkout UI.
- Keep prediction access for already paid users.
- Preserve `data/billing/` (orders, subscriptions, events, audit log).
- Preserve `data/prediction_snapshots/`.

## Audit

- `data/billing/audit_log.json` records all checkout, payment, and subscription events.
- Append-only; do not manually edit.
