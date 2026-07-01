# Refresh Jobs

## Purpose

Refresh jobs keep cached prediction snapshots current before kickoff.

## Refresh Rules

| Time to kickoff | Refresh interval |
|---|---:|
| >24h | 6h |
| 6h-24h | 1h |
| 1h-6h | 15min |
| <1h | locked |
| confirmed lineup | locked |

## CLI

Run upcoming refresh:
```powershell
python src/jobs/refresh_predictions.py --window-hours 48
```

Force single fixture:
```powershell
python src/jobs/refresh_predictions.py --fixture-id 19606960 --force
```

Retry failed attempts from latest run:
```powershell
python src/jobs/refresh_predictions.py --retry-failed
```

Inspect latest run:
```powershell
python src/jobs/refresh_predictions.py --latest
```

## API

`GET /api/admin/jobs/refresh/latest` — requires admin.

`POST /api/admin/jobs/refresh/run` — requires admin. Body: `{"window_hours": 48, "force": false}`.

## Persistence

Job runs and per-fixture attempts are stored in `data/jobs/refresh_runs.json`.
