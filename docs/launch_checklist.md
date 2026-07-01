# Launch Checklist

## Pre-Launch

- [ ] `python -m unittest discover -s tests` passes.
- [ ] `python src/smoke_test_launch.py` passes.
- [ ] `/health` returns HTTP 200 in target environment.
- [ ] `/h5` opens on mobile viewport.
- [ ] `/api/matches/today?limit=5` returns standard envelope.
- [ ] Basic prediction page hides VIP-only fields.
- [ ] VIP user can access `/api/predictions/<id>/vip`.
- [ ] Non-VIP user gets `vip_required` for VIP endpoint.
- [ ] Billing checkout creates a pending order.
- [ ] Mock webhook is disabled or protected before public launch.
- [ ] `DATA_DIR` is backed up.
- [ ] `SPORTMONKS_API_KEY` is configured when live upstream refresh is required.

## Launch

- [ ] Deploy `src.server:app`.
- [ ] Run live smoke test.
- [ ] Trigger admin refresh for the next 48 hours.
- [ ] Check latest refresh job status.
- [ ] Open H5 match list.
- [ ] Open one prediction detail page.
- [ ] Verify account/subscription page.

## Rollback

- [ ] Stop new deployment.
- [ ] Restore previous app version.
- [ ] Reuse existing `DATA_DIR`.
- [ ] Run live smoke test.
- [ ] Verify paid subscription lookup still works.
