# Billing API Contract

## Identity

Billing and prediction APIs use `Authorization: Bearer <session_token>` in production.
`X-User-Id` remains a dev/test fallback only.

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
