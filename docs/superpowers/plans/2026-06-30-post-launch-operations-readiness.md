# Post-Launch Operations Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prepare the World Cup prediction H5/API/payment system for controlled paid beta operation after Step 10G sign-off.

**Architecture:** Treat post-launch operations as a controlled runbook: stable production config, repeatable deployment, monitored prediction and billing APIs, daily refresh operations, customer support playbooks, and rollback paths. The system already has Flask API, H5 frontend, signed beta sessions, billing webhook, refresh jobs, health checks, and JSON-backed durable stores; this plan turns those into an operating process.

**Tech Stack:** Python Flask, H5 static frontend, JSON durable stores under `DATA_DIR`, Sportmonks API, signed HMAC session tokens, signed payment webhooks, `unittest`, `gunicorn`, Windows/PowerShell or Linux shell deployment.

---

## Files And Artifacts

- Verify: `.env.example`
- Verify: `docs/deployment_guide.md`
- Verify: `docs/launch_checklist.md`
- Verify: `docs/beta_launch_runbook.md`
- Verify: `docs/api_billing_contract.md`
- Verify: `docs/api_prediction_contract.md`
- Verify: `src/config/runtime.py`
- Verify: `src/api/app.py`
- Verify: `src/monitoring/health.py`
- Verify: `src/smoke_test_launch.py`
- Runtime data: `DATA_DIR/billing/orders.json`
- Runtime data: `DATA_DIR/billing/payment_events.json`
- Runtime data: `DATA_DIR/billing/subscriptions.json`
- Runtime data: `DATA_DIR/billing/audit_log.json`
- Runtime data: `DATA_DIR/prediction_snapshots/`
- Runtime data: `DATA_DIR/jobs/refresh_runs.json`

---

### Task 1: Production Environment Configuration

**Files:**
- Read: `.env.example`
- Read: `src/config/runtime.py`
- Update if needed: `docs/deployment_guide.md`
- Update if needed: `docs/launch_checklist.md`

- [ ] **Step 1: Prepare production environment variables**

Use these values as the production baseline:

```bash
APP_ENV=prod
APP_BASE_URL=https://your-domain.example
DATA_DIR=/srv/worldcup-predict/data
ENABLE_H5=true
PORT=5000
FLASK_DEBUG=false
SPORTMONKS_API_KEY=<real_sportmonks_key>
BETA_AUTH_SECRET=<random_32_plus_char_secret>
BETA_SESSION_TTL_HOURS=720
PAYMENT_PROVIDER=mock
PAYMENT_WEBHOOK_SECRET=<random_32_plus_char_secret>
PAYMENT_RETURN_URL=https://your-domain.example/h5#/account
ENABLE_DEV_PAYMENT_WEBHOOK=false
```

- [ ] **Step 2: Generate secrets**

Run one of these locally and store the values in the production secret manager:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Expected: two different random strings. Use one for `BETA_AUTH_SECRET`, one for `PAYMENT_WEBHOOK_SECRET`.

- [ ] **Step 3: Validate runtime config in prod mode**

Run:

```bash
APP_ENV=prod FLASK_DEBUG=false ENABLE_DEV_PAYMENT_WEBHOOK=false BETA_AUTH_SECRET=<32+ char random string> PAYMENT_WEBHOOK_SECRET=<32+ char random string> python -m unittest tests.test_runtime_config
```

Expected: all runtime config tests pass.

- [ ] **Step 4: Confirm forbidden prod settings fail**

Run:

```bash
APP_ENV=prod FLASK_DEBUG=true BETA_AUTH_SECRET=<32+ char random string> PAYMENT_WEBHOOK_SECRET=<32+ char random string> python -c "from src.config.runtime import load_runtime_config; load_runtime_config()"
```

Expected: command fails with `FLASK_DEBUG must be disabled when APP_ENV=prod`.

- [ ] **Step 5: Commit documentation updates if any**

Run:

```bash
git status --short
git add docs/deployment_guide.md docs/launch_checklist.md .env.example
git commit -m "docs: finalize production environment checklist"
```

Expected: commit only if documentation changed.

---

### Task 2: Deployment Preflight

**Files:**
- Read: `requirements.txt`
- Read: `src/server.py`
- Read: `src/api/app.py`
- Read: `src/smoke_test_launch.py`

- [ ] **Step 1: Install dependencies in a clean environment**

Run:

```bash
python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
```

Expected: install completes without missing packages.

- [ ] **Step 2: Run full tests before deployment**

Run:

```bash
python -m unittest discover -s tests
```

Expected: `OK`. Current signed-off baseline is `285` tests passing.

- [ ] **Step 3: Run local smoke**

Run:

```bash
python src/smoke_test_launch.py
```

Expected:

```json
{
  "ok": true,
  "failed_count": 0
}
```

- [ ] **Step 4: Start production entrypoint locally**

Windows local check:

```bash
set APP_ENV=prod
set FLASK_DEBUG=false
set ENABLE_DEV_PAYMENT_WEBHOOK=false
set BETA_AUTH_SECRET=<32+ char random string>
set PAYMENT_WEBHOOK_SECRET=<32+ char random string>
python -m gunicorn src.server:app
```

Linux production check:

```bash
APP_ENV=prod FLASK_DEBUG=false ENABLE_DEV_PAYMENT_WEBHOOK=false BETA_AUTH_SECRET=<32+ char random string> PAYMENT_WEBHOOK_SECRET=<32+ char random string> gunicorn src.server:app --bind 0.0.0.0:5000 --workers 2 --timeout 120
```

Expected: server starts. If Windows cannot run `gunicorn`, use `python src/api/app.py` only for local smoke and deploy `gunicorn` on Linux.

- [ ] **Step 5: Run live smoke against local server**

Run:

```bash
python src/smoke_test_launch.py --base-url http://localhost:5000
```

Expected: `ok=true`, `failed_count=0`.

---

### Task 3: Data Directory And Backup Setup

**Files:**
- Runtime: `DATA_DIR/billing/`
- Runtime: `DATA_DIR/prediction_snapshots/`
- Runtime: `DATA_DIR/jobs/`

- [ ] **Step 1: Create production data directories**

Linux:

```bash
mkdir -p /srv/worldcup-predict/data/billing
mkdir -p /srv/worldcup-predict/data/prediction_snapshots
mkdir -p /srv/worldcup-predict/data/jobs
```

Windows:

```powershell
New-Item -ItemType Directory -Force C:\worldcup-predict\data\billing
New-Item -ItemType Directory -Force C:\worldcup-predict\data\prediction_snapshots
New-Item -ItemType Directory -Force C:\worldcup-predict\data\jobs
```

Expected: directories exist and are writable by the app process.

- [ ] **Step 2: Check live health with production `DATA_DIR`**

Run:

```bash
DATA_DIR=/srv/worldcup-predict/data python src/smoke_test_launch.py
```

Expected: `health_live` passes quickly.

- [ ] **Step 3: Create backup script**

Create an operational script outside the app repo, for example:

```bash
#!/usr/bin/env bash
set -euo pipefail
STAMP=$(date -u +"%Y%m%dT%H%M%SZ")
tar -czf "/srv/worldcup-predict/backups/data-${STAMP}.tar.gz" /srv/worldcup-predict/data
find /srv/worldcup-predict/backups -type f -name "data-*.tar.gz" -mtime +14 -delete
```

Expected: backup archive includes `billing`, `prediction_snapshots`, and `jobs`.

- [ ] **Step 4: Test restore in a temporary directory**

Run:

```bash
mkdir -p /tmp/worldcup-restore-test
tar -xzf /srv/worldcup-predict/backups/<latest-backup>.tar.gz -C /tmp/worldcup-restore-test
```

Expected: billing JSON files and prediction snapshots are readable after extraction.

- [ ] **Step 5: Schedule backups**

Recommended cadence:

```text
Every 6 hours during active match days.
Daily during inactive periods.
Keep 14 days of rolling backups.
Keep launch-day backup permanently.
```

---

### Task 4: Monitoring And Alerting

**Files:**
- Read: `src/monitoring/health.py`
- Read: `src/api/app.py`
- Verify: `docs/launch_checklist.md`

- [ ] **Step 1: Monitor liveness**

Endpoint:

```text
GET /health/live
```

Expected:

```json
{
  "healthy": true,
  "status": "ok"
}
```

Alert rule:

```text
Trigger if /health/live is not 2xx for 2 consecutive checks over 2 minutes.
```

- [ ] **Step 2: Monitor full health**

Endpoint:

```text
GET /health
```

Alert rule:

```text
Trigger warning if status=degraded for 10 minutes.
Trigger incident if model/cache/runtime/storage checks fail.
Sportmonks API missing can be warning during local/dev, but should be incident in prod.
```

- [ ] **Step 3: Monitor API status codes**

Track these route families:

```text
/api/predictions/*
/api/billing/checkout
/api/billing/webhook
/api/me/subscription
/api/matches/today
```

Alert thresholds:

```text
5xx rate > 2% over 5 minutes: incident.
401/403 spike after deployment: check auth/session token flow.
Webhook 401 spike: check provider signature secret.
Prediction 503 spike: check Sportmonks/API/cache/model loading.
```

- [ ] **Step 4: Monitor latency**

Target thresholds:

```text
/health/live p95 < 200ms
/h5 p95 < 500ms
/api/predictions cache hit p95 < 500ms
/api/billing/checkout p95 < 1000ms
/api/billing/webhook p95 < 1000ms
```

- [ ] **Step 5: Write daily monitoring note**

Use this format:

```text
Date:
Traffic:
Prediction requests:
Checkout attempts:
Paid subscriptions activated:
Webhook failures:
Prediction 5xx:
Health incidents:
Manual fixes:
Open risks:
```

---

### Task 5: Payment Operations

**Files:**
- Read: `docs/api_billing_contract.md`
- Read: `src/billing/models.py`
- Read: `src/billing/subscription_service.py`
- Runtime: `DATA_DIR/billing/orders.json`
- Runtime: `DATA_DIR/billing/payment_events.json`
- Runtime: `DATA_DIR/billing/subscriptions.json`
- Runtime: `DATA_DIR/billing/audit_log.json`

- [ ] **Step 1: Verify checkout path**

Manual test in dev:

```text
Open /h5#/account
Create session
Open /h5#/subscribe
Select vip_day
Confirm checkout returns payment_url
```

Expected: user is redirected to payment URL or receives checkout URL.

- [ ] **Step 2: Verify signed webhook path**

Run a signed webhook test against staging:

```bash
python - <<'PY'
import hashlib, hmac, json, requests
secret = "replace-with-staging-secret"
body = json.dumps({
  "event_id": "evt_manual_001",
  "order_id": "replace-with-order-id",
  "event_type": "payment.succeeded"
}, separators=(",", ":")).encode()
sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
r = requests.post(
  "https://your-domain.example/api/billing/webhook",
  data=body,
  headers={"Content-Type": "application/json", "X-Payment-Signature": sig},
  timeout=10,
)
print(r.status_code, r.text)
PY
```

Expected: `200`, subscription activated.

- [ ] **Step 3: Confirm idempotency**

Send the same webhook payload again.

Expected:

```json
{
  "idempotent": true
}
```

- [ ] **Step 4: Daily billing reconciliation**

Check:

```text
Provider paid orders count.
orders.json paid orders count.
payment_events.json unique event count.
subscriptions.json active vip users count.
audit_log.json event count.
```

Expected: provider paid orders and local active subscriptions match, except for manually refunded or expired cases.

- [ ] **Step 5: Refund/manual correction rule**

For beta, do not edit JSON files while the app is running. Use this operational rule:

```text
Stop app.
Backup DATA_DIR.
Apply manual correction.
Restart app.
Run /health/live.
Run /api/me/subscription for affected user.
Record action in operations notes.
```

---

### Task 6: Prediction Refresh Operations

**Files:**
- Read: `docs/refresh_jobs.md`
- Read: `src/jobs/refresh_predictions.py`
- Read: `src/jobs/runner.py`
- Runtime: `DATA_DIR/jobs/refresh_runs.json`
- Runtime: `DATA_DIR/prediction_snapshots/`

- [ ] **Step 1: Run upcoming match refresh**

Run:

```bash
python src/jobs/refresh_predictions.py --window-hours 48
```

Expected: upcoming fixtures are refreshed, skipped, or recorded as failed without aborting the whole batch.

- [ ] **Step 2: Check latest refresh run**

Run:

```bash
python src/jobs/refresh_predictions.py --latest
```

Expected: run summary shows `success` or acceptable `partial_failure`.

- [ ] **Step 3: Retry failures**

Run:

```bash
python src/jobs/refresh_predictions.py --retry-failed
```

Expected: failed attempts are retried and new run is recorded.

- [ ] **Step 4: Schedule refresh cadence**

Use this cadence during tournament days:

```text
Every 6 hours for matches more than 6 hours away.
Every 1 hour for matches within 6 hours.
Every 15 minutes for matches within 2 hours.
Stop refreshing after confirmed lineup lock or kickoff.
```

- [ ] **Step 5: Confirm cache works after refresh**

Run:

```bash
python -c "from src.services.prediction_service import load_latest_prediction; print(load_latest_prediction('sportmonks-19606960') is not None)"
```

Expected: `True` for a known cached fixture.

---

### Task 7: Beta User Operations

**Files:**
- Read: `docs/beta_launch_runbook.md`
- Runtime: `DATA_DIR/billing/subscriptions.json`

- [ ] **Step 1: Define beta cohort**

Start with:

```text
10-30 paid users.
1-3 known fixtures per day.
One support channel.
Manual daily reconciliation.
```

- [ ] **Step 2: Define access model**

Production rule:

```text
Do not use X-User-Id in prod.
Only accept Authorization: Bearer <signed_session_token>.
Direct /api/auth/session is disabled in prod.
```

- [ ] **Step 3: Issue beta session tokens**

Use an admin-side script or console command that calls:

```python
from src.auth.session_tokens import create_session_token

token = create_session_token(
    user_id="customer_001",
    secret="<BETA_AUTH_SECRET>",
    ttl_seconds=720 * 3600,
)
print(token)
```

Expected: token format is `{payload_b64}.{signature_b64}`.

- [ ] **Step 4: User onboarding message**

Use this message:

```text
You are in the paid beta. Your access is tied to your account/session.
Predictions show probabilities, not guarantees.
VIP unlocks score heatmap, Top 15 scorelines, BTTS, Over 2.5, market edge, and model explanation fields.
If payment succeeds but access does not update within 2 minutes, send your order ID to support.
```

- [ ] **Step 5: Daily beta review**

Track:

```text
Number of active users.
Number of failed logins.
Number of checkout attempts.
Number of payment successes.
Number of prediction page loads.
Number of support issues.
Top 3 user complaints.
```

---

### Task 8: Customer Support Playbook

**Files:**
- Runtime: `DATA_DIR/billing/orders.json`
- Runtime: `DATA_DIR/billing/payment_events.json`
- Runtime: `DATA_DIR/billing/subscriptions.json`
- Runtime: `DATA_DIR/billing/audit_log.json`

- [ ] **Step 1: Payment succeeded but user is still free**

Check:

```text
Find order_id in orders.json.
Find event_id in payment_events.json.
Find user_id in subscriptions.json.
Check audit_log.json for payment.succeeded and subscription.activated.
```

Expected fix:

```text
If webhook missing, ask payment provider to resend webhook.
If webhook failed signature, check PAYMENT_WEBHOOK_SECRET.
If subscription missing after paid order, stop app, backup DATA_DIR, correct subscription, restart app, record action.
```

- [ ] **Step 2: User cannot open VIP prediction**

Check:

```text
Does request include Authorization: Bearer token?
Is token expired?
Does subscriptions.json contain active vip status?
Does /api/me/subscription return tier=vip?
```

Expected fix:

```text
Reissue session token if expired.
Fix subscription only if payment is verified.
```

- [ ] **Step 3: Prediction page returns 503**

Check:

```text
/health/live
/health
latest prediction cache exists
Sportmonks API key exists
model files load
refresh job latest status
```

Expected fix:

```text
If cache exists, serve cached prediction.
If fixture not cached and API unavailable, communicate temporary data delay.
If model load fails, rollback to previous deploy.
```

- [ ] **Step 4: User asks why prediction was wrong**

Response:

```text
The product gives calibrated probabilities, not deterministic results.
For a single match, even a 65% favorite loses often enough to be normal.
We evaluate model quality by LogLoss, Brier, calibration, and long-term performance, not one-match hit rate.
```

---

### Task 9: Incident Response And Rollback

**Files:**
- Read: `docs/beta_launch_runbook.md`
- Read: `docs/deployment_guide.md`
- Runtime: `DATA_DIR/`

- [ ] **Step 1: Define incident severity**

Use:

```text
SEV1: payment creates wrong access, data loss, app unavailable.
SEV2: prediction API failing for many users, webhook delayed, refresh job failing.
SEV3: individual account issue, isolated prediction missing, UI display bug.
```

- [ ] **Step 2: Stop sales without killing paid access**

Options:

```text
Set ENABLE_H5=false to hide H5 if needed.
Disable checkout UI in frontend if only payment is affected.
Keep /api/predictions and /api/me/subscription available for paid users when possible.
```

- [ ] **Step 3: Roll back app version**

Run:

```bash
git log --oneline -5
git switch <last-known-good-branch-or-tag>
pip install -r requirements.txt
python -m unittest discover -s tests
python src/smoke_test_launch.py
restart app process
```

Expected: previous stable version serves `/health/live`, `/h5`, and cached predictions.

- [ ] **Step 4: Preserve runtime data before rollback**

Run:

```bash
tar -czf "/srv/worldcup-predict/backups/pre-rollback-$(date -u +%Y%m%dT%H%M%SZ).tar.gz" /srv/worldcup-predict/data
```

Expected: data backup exists before any code rollback.

- [ ] **Step 5: Write incident note**

Use:

```text
Incident start:
Incident end:
Severity:
Affected users:
Affected endpoints:
Root cause:
Mitigation:
Permanent fix:
Tests added:
Refunds/manual corrections:
```

---

### Task 10: Beta Expansion Gate

**Files:**
- Runtime: monitoring notes
- Runtime: billing reconciliation notes
- Runtime: support issue notes

- [ ] **Step 1: Hold first cohort for 48 hours**

Do not expand until:

```text
No SEV1.
No unresolved payment mismatch.
/health/live stable.
Webhook success rate above 99%.
Prediction cache hit path verified.
Support issues are understood.
```

- [ ] **Step 2: Review model/product trust**

Check:

```text
Do users understand probability output?
Are VIP fields valuable enough?
Are explanations clear?
Are users asking for betting stake sizing? If yes, do not add it without legal review.
```

- [ ] **Step 3: Expand to second cohort**

Criteria:

```text
First cohort: 10-30 users.
Second cohort: 50-100 users.
Keep manual reconciliation daily.
Keep rollout reversible.
```

- [ ] **Step 4: Freeze major changes during active match windows**

Rule:

```text
No model changes within 6 hours before high-traffic matches.
No billing changes on match day without rollback plan.
UI copy changes allowed only if they do not touch auth/payment/prediction API.
```

- [ ] **Step 5: Decide next engineering step**

Choose one:

```text
Step 11A: Replace mock payment provider with real provider adapter.
Step 11B: Add admin dashboard for users, orders, subscriptions, and refresh runs.
Step 11C: Add production logs/metrics export for external observability.
Step 11D: Improve prediction product page with model history and calibration display.
```

---

## Final Acceptance Checklist

- [ ] `python -m unittest discover -s tests` passes.
- [ ] `python src/smoke_test_launch.py` passes.
- [ ] `/health/live` returns `200` in production.
- [ ] `/health` has no unexpected failed checks.
- [ ] `/h5` loads on mobile and desktop.
- [ ] Basic prediction hides VIP fields.
- [ ] VIP prediction returns score heatmap and market edge for active paid user.
- [ ] Checkout creates pending order.
- [ ] Signed webhook activates subscription.
- [ ] Duplicate webhook is idempotent.
- [ ] `ENABLE_DEV_PAYMENT_WEBHOOK=false` in production.
- [ ] `DATA_DIR` backup is scheduled and restore-tested.
- [ ] First beta cohort size is capped.
- [ ] Rollback command and data backup path are known.

---

## Known Residual Risks

- Current payment provider is still `mock`; real provider integration must be treated as a separate Step 11 task.
- JSON durable stores are acceptable for beta but not ideal for high concurrency; move to SQLite/Postgres before larger paid scale.
- Model pickle warnings from XGBoost/sklearn should be handled before long-term production operation by exporting models in stable formats.
- Legal/compliance review is still needed before marketing this as a paid prediction product in broader markets.
