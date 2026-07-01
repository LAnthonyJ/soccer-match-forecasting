# H5 Frontend Contract

## Routes

| route | purpose |
|---|---|
| `/h5#/matches` | Match list |
| `/h5#/match/<fixture_id>` | Prediction detail |
| `/h5#/subscribe` | VIP checkout |
| `/h5#/account` | Subscription status |
| `/h5#/admin-refresh` | Admin refresh status |

## User State

`wc_predict_user_id` stores the current user id in local storage.

## API Envelope

All API calls expect:

```json
{"ok": true, "data": {}, "meta": {}}
```

Errors are displayed from:

```json
{"ok": false, "error": {"code": "...", "message": "..."}}
```

## Basic vs VIP

Basic users see HDA probabilities, top 3 scorelines, confidence, model version, and data update time.

VIP users additionally see top 15 scorelines, over/under, BTTS, market edge, lineup adjustment, and score heatmap when available.

## Launch Checklist

- `/h5` returns the H5 shell.
- `/api/matches/today` returns envelope data.
- `/api/predictions/<id>` renders for anonymous/basic users.
- `/api/predictions/<id>/vip` unlocks for VIP users.
- `/api/billing/checkout` creates pending orders.
- `/api/me/subscription` updates account state.
