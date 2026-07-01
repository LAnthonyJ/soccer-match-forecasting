# Step 10D Refresh Jobs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade prediction refresh from an ad hoc script into a durable, observable job runner that can refresh upcoming matches, record every attempt, retry failures, and expose job health to API/admin consumers.

**Architecture:** Keep the current `refresh_predictions.py` refresh rules, but move orchestration into focused job modules: job models, JSON store, planner, runner, CLI, and admin API endpoints. Use JSON persistence for Step 10D to match current billing/cache storage and avoid a database migration before Step 10E.

**Tech Stack:** Python stdlib dataclasses/json/pathlib/datetime/argparse, Flask API, existing `PredictionService`, existing `refresh_match_prediction()` rules, unittest.

---

## Scope

Step 10D is about operational reliability of prediction refresh.

In scope:

- Persisted job run records under `data/jobs/refresh_runs.json`.
- Per-fixture attempt records with status, timestamps, duration, error, saved snapshot path.
- Refresh planner that decides which fixtures are due.
- Runner that processes fixtures, continues after failures, and writes a summary.
- Retry support for failed fixture attempts.
- CLI commands for run, retry, inspect latest run.
- API/admin endpoints for refresh status and latest run.
- Health check extension for stale jobs and recent failures.

Out of scope:

- External queue systems such as Celery, Redis, RQ, APScheduler.
- OS-level cron or cloud scheduler setup.
- SQLite/Postgres migration.
- Frontend display of job status.
- Real-time push updates.

## File Map

- Create `src/jobs/models.py`: `RefreshJobRun`, `RefreshJobAttempt`, status constants, serialization.
- Create `src/jobs/job_store.py`: JSON-backed store for runs and attempts.
- Create `src/jobs/planner.py`: fixture discovery and due-refresh planning.
- Create `src/jobs/runner.py`: orchestrates one refresh run, per-fixture attempts, retry logic.
- Modify `src/jobs/refresh_predictions.py`: keep existing public helpers, delegate CLI orchestration to runner.
- Modify `src/api/app.py`: add admin endpoints for refresh job status.
- Modify `src/monitoring/health.py`: add refresh job health check.
- Create `tests/test_refresh_job_models.py`: model roundtrip tests.
- Create `tests/test_refresh_job_store.py`: store roundtrip/latest/filter tests.
- Create `tests/test_refresh_planner.py`: due fixture planning tests.
- Create `tests/test_refresh_runner.py`: success/failure/retry/summary tests.
- Create `tests/test_api_refresh_jobs.py`: admin API route tests.
- Modify `tests/test_health_and_refresh.py`: add refresh health check coverage.
- Create `docs/refresh_jobs.md`: CLI/API contract and operating notes.

## Data Model

`RefreshJobRun`

```python
@dataclass
class RefreshJobRun:
    run_id: str
    status: str
    started_at: str
    finished_at: str | None
    window_hours: int
    total: int
    refreshed: int
    skipped: int
    failed: int
    force: bool = False
```

Allowed run status:

- `running`
- `success`
- `partial_failure`
- `failed`

`RefreshJobAttempt`

```python
@dataclass
class RefreshJobAttempt:
    run_id: str
    fixture_id: int
    status: str
    started_at: str
    finished_at: str | None
    duration_ms: int | None
    snapshot_path: str | None
    error: str | None
    skipped_reason: str | None
```

Allowed attempt status:

- `refreshed`
- `skipped`
- `failed`

Persistence shape in `data/jobs/refresh_runs.json`:

```json
{
  "runs": {
    "run_abc": {}
  },
  "attempts": {
    "run_abc": []
  }
}
```

## CLI Contract

Run upcoming refresh:

```powershell
python src/jobs/refresh_predictions.py --window-hours 48
```

Force a fixture:

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

Expected output:

```text
run_id=run_20260629T120000Z status=partial_failure total=12 refreshed=10 skipped=1 failed=1
```

## API Contract

Admin-only endpoint:

`GET /api/admin/jobs/refresh/latest`

Response envelope:

```json
{
  "ok": true,
  "data": {
    "run": {},
    "attempts": []
  },
  "meta": {
    "request_id": "req_..."
  }
}
```

Admin-only endpoint:

`POST /api/admin/jobs/refresh/run`

Request:

```json
{
  "window_hours": 48,
  "force": false
}
```

Response:

```json
{
  "ok": true,
  "data": {
    "run_id": "run_...",
    "status": "success",
    "total": 3,
    "refreshed": 2,
    "skipped": 1,
    "failed": 0
  }
}
```

## Task 1: Job Models

**Files:**

- Create: `src/jobs/models.py`
- Create: `tests/test_refresh_job_models.py`

- [ ] **Step 1: Write model tests**

Create `tests/test_refresh_job_models.py`:

```python
import unittest

from src.jobs.models import RefreshJobAttempt, RefreshJobRun


class RefreshJobModelsTests(unittest.TestCase):
    def test_run_roundtrip(self):
        run = RefreshJobRun(
            run_id="run_1",
            status="running",
            started_at="2026-06-29T00:00:00+00:00",
            finished_at=None,
            window_hours=48,
            total=0,
            refreshed=0,
            skipped=0,
            failed=0,
            force=False,
        )
        loaded = RefreshJobRun.from_dict(run.to_dict())
        self.assertEqual(loaded.run_id, "run_1")
        self.assertEqual(loaded.status, "running")

    def test_attempt_roundtrip(self):
        attempt = RefreshJobAttempt(
            run_id="run_1",
            fixture_id=123,
            status="refreshed",
            started_at="2026-06-29T00:00:00+00:00",
            finished_at="2026-06-29T00:00:01+00:00",
            duration_ms=1000,
            snapshot_path="data/prediction_snapshots/x.json",
            error=None,
            skipped_reason=None,
        )
        loaded = RefreshJobAttempt.from_dict(attempt.to_dict())
        self.assertEqual(loaded.fixture_id, 123)
        self.assertEqual(loaded.status, "refreshed")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run failing test**

Run:

```powershell
python -m unittest tests.test_refresh_job_models
```

Expected: fail because `src.jobs.models` does not exist.

- [ ] **Step 3: Implement models**

Create `src/jobs/models.py`:

```python
from dataclasses import asdict, dataclass
from typing import Any


RUN_STATUSES = {"running", "success", "partial_failure", "failed"}
ATTEMPT_STATUSES = {"refreshed", "skipped", "failed"}


@dataclass
class RefreshJobRun:
    run_id: str
    status: str
    started_at: str
    finished_at: str | None
    window_hours: int
    total: int
    refreshed: int
    skipped: int
    failed: int
    force: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RefreshJobRun":
        return cls(**payload)


@dataclass
class RefreshJobAttempt:
    run_id: str
    fixture_id: int
    status: str
    started_at: str
    finished_at: str | None
    duration_ms: int | None
    snapshot_path: str | None
    error: str | None
    skipped_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RefreshJobAttempt":
        return cls(**payload)
```

- [ ] **Step 4: Run model tests**

Run:

```powershell
python -m unittest tests.test_refresh_job_models
```

Expected: OK.

## Task 2: Job Store

**Files:**

- Create: `src/jobs/job_store.py`
- Create: `tests/test_refresh_job_store.py`

- [ ] **Step 1: Write store tests**

Create `tests/test_refresh_job_store.py`:

```python
import tempfile
import unittest
from pathlib import Path

from src.jobs.job_store import RefreshJobStore
from src.jobs.models import RefreshJobAttempt, RefreshJobRun


class RefreshJobStoreTests(unittest.TestCase):
    def _run(self, run_id="run_1", status="success"):
        return RefreshJobRun(
            run_id=run_id,
            status=status,
            started_at=f"2026-06-29T00:00:00+00:00",
            finished_at="2026-06-29T00:01:00+00:00",
            window_hours=48,
            total=1,
            refreshed=1,
            skipped=0,
            failed=0,
        )

    def _attempt(self, run_id="run_1", status="refreshed"):
        return RefreshJobAttempt(
            run_id=run_id,
            fixture_id=123,
            status=status,
            started_at="2026-06-29T00:00:00+00:00",
            finished_at="2026-06-29T00:00:01+00:00",
            duration_ms=1000,
            snapshot_path="snapshot.json",
            error=None,
            skipped_reason=None,
        )

    def test_save_and_load_run(self):
        with tempfile.TemporaryDirectory() as td:
            store = RefreshJobStore(Path(td) / "refresh_runs.json")
            store.save_run(self._run())
            loaded = store.get_run("run_1")
            self.assertEqual(loaded.status, "success")

    def test_append_attempts(self):
        with tempfile.TemporaryDirectory() as td:
            store = RefreshJobStore(Path(td) / "refresh_runs.json")
            store.save_run(self._run())
            store.append_attempt(self._attempt())
            attempts = store.list_attempts("run_1")
            self.assertEqual(len(attempts), 1)
            self.assertEqual(attempts[0].fixture_id, 123)

    def test_latest_run(self):
        with tempfile.TemporaryDirectory() as td:
            store = RefreshJobStore(Path(td) / "refresh_runs.json")
            store.save_run(self._run("run_1"))
            store.save_run(self._run("run_2"))
            self.assertEqual(store.latest_run().run_id, "run_2")

    def test_failed_attempts(self):
        with tempfile.TemporaryDirectory() as td:
            store = RefreshJobStore(Path(td) / "refresh_runs.json")
            store.save_run(self._run())
            failed = self._attempt(status="failed")
            failed.error = "boom"
            store.append_attempt(failed)
            self.assertEqual(len(store.failed_attempts("run_1")), 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run failing test**

Run:

```powershell
python -m unittest tests.test_refresh_job_store
```

Expected: fail because `RefreshJobStore` does not exist.

- [ ] **Step 3: Implement store**

Create `src/jobs/job_store.py`:

```python
import json
from pathlib import Path
from typing import Any

from src.jobs.models import RefreshJobAttempt, RefreshJobRun


class RefreshJobStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"runs": {}, "attempts": {}}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def save_run(self, run: RefreshJobRun) -> None:
        payload = self._read()
        payload.setdefault("runs", {})[run.run_id] = run.to_dict()
        payload.setdefault("attempts", {}).setdefault(run.run_id, [])
        self._write(payload)

    def get_run(self, run_id: str) -> RefreshJobRun | None:
        raw = self._read().get("runs", {}).get(run_id)
        return RefreshJobRun.from_dict(raw) if raw else None

    def latest_run(self) -> RefreshJobRun | None:
        runs = self._read().get("runs", {})
        if not runs:
            return None
        latest = max(runs.values(), key=lambda item: item.get("started_at", ""))
        return RefreshJobRun.from_dict(latest)

    def append_attempt(self, attempt: RefreshJobAttempt) -> None:
        payload = self._read()
        payload.setdefault("attempts", {}).setdefault(attempt.run_id, []).append(attempt.to_dict())
        self._write(payload)

    def list_attempts(self, run_id: str) -> list[RefreshJobAttempt]:
        raw = self._read().get("attempts", {}).get(run_id, [])
        return [RefreshJobAttempt.from_dict(item) for item in raw]

    def failed_attempts(self, run_id: str) -> list[RefreshJobAttempt]:
        return [item for item in self.list_attempts(run_id) if item.status == "failed"]
```

- [ ] **Step 4: Run store tests**

Run:

```powershell
python -m unittest tests.test_refresh_job_store
```

Expected: OK.

## Task 3: Refresh Planner

**Files:**

- Create: `src/jobs/planner.py`
- Create: `tests/test_refresh_planner.py`

- [ ] **Step 1: Write planner tests**

Create `tests/test_refresh_planner.py`:

```python
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from src.jobs.planner import RefreshPlanItem, plan_upcoming_refreshes


class RefreshPlannerTests(unittest.TestCase):
    def test_plan_returns_fixture_ids(self):
        fixtures = [{"id": 1}, {"id": 2}]
        with patch("src.jobs.planner.get_fixtures_between", return_value=fixtures):
            items = plan_upcoming_refreshes(window_hours=48)
            self.assertEqual([item.fixture_id for item in items], [1, 2])

    def test_plan_empty_on_provider_error(self):
        with patch("src.jobs.planner.get_fixtures_between", side_effect=Exception("down")):
            items = plan_upcoming_refreshes(window_hours=48)
            self.assertEqual(items, [])

    def test_plan_item_roundtrip_fields(self):
        item = RefreshPlanItem(fixture_id=123, raw_fixture={"id": 123})
        self.assertEqual(item.fixture_id, 123)
        self.assertEqual(item.raw_fixture["id"], 123)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Implement planner**

Create `src/jobs/planner.py`:

```python
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from src.data.sportmonks_client import get_fixtures_between
from src.data.sportmonks_ingestion import INTERNATIONAL_LEAGUES


@dataclass
class RefreshPlanItem:
    fixture_id: int
    raw_fixture: dict[str, Any]


def plan_upcoming_refreshes(window_hours: int = 48) -> list[RefreshPlanItem]:
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=window_hours)
    try:
        fixtures = get_fixtures_between(
            now,
            window_end,
            league_ids=list(INTERNATIONAL_LEAGUES.keys()),
            includes="participants;league",
        )
    except Exception:
        return []
    return [RefreshPlanItem(fixture_id=item["id"], raw_fixture=item) for item in fixtures]
```

- [ ] **Step 3: Run planner tests**

Run:

```powershell
python -m unittest tests.test_refresh_planner
```

Expected: OK.

## Task 4: Refresh Runner

**Files:**

- Create: `src/jobs/runner.py`
- Create: `tests/test_refresh_runner.py`

- [ ] **Step 1: Write runner tests**

Create `tests/test_refresh_runner.py`:

```python
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.jobs.job_store import RefreshJobStore
from src.jobs.planner import RefreshPlanItem
from src.jobs.runner import RefreshJobRunner


class RefreshRunnerTests(unittest.TestCase):
    def test_run_records_success_and_skip(self):
        with tempfile.TemporaryDirectory() as td:
            store = RefreshJobStore(Path(td) / "runs.json")
            runner = RefreshJobRunner(store)
            plan = [RefreshPlanItem(1, {"id": 1}), RefreshPlanItem(2, {"id": 2})]
            with patch("src.jobs.runner.plan_upcoming_refreshes", return_value=plan):
                with patch("src.jobs.runner.refresh_match_prediction", side_effect=[Path("a.json"), None]):
                    run = runner.run(window_hours=48)
            self.assertEqual(run.total, 2)
            self.assertEqual(run.refreshed, 1)
            self.assertEqual(run.skipped, 1)
            self.assertEqual(run.failed, 0)
            self.assertEqual(len(store.list_attempts(run.run_id)), 2)

    def test_run_records_failure_and_continues(self):
        with tempfile.TemporaryDirectory() as td:
            store = RefreshJobStore(Path(td) / "runs.json")
            runner = RefreshJobRunner(store)
            plan = [RefreshPlanItem(1, {"id": 1}), RefreshPlanItem(2, {"id": 2})]
            with patch("src.jobs.runner.plan_upcoming_refreshes", return_value=plan):
                with patch("src.jobs.runner.refresh_match_prediction", side_effect=[Exception("boom"), Path("b.json")]):
                    run = runner.run(window_hours=48)
            self.assertEqual(run.status, "partial_failure")
            self.assertEqual(run.failed, 1)
            self.assertEqual(run.refreshed, 1)

    def test_retry_failed_uses_latest_run(self):
        with tempfile.TemporaryDirectory() as td:
            store = RefreshJobStore(Path(td) / "runs.json")
            runner = RefreshJobRunner(store)
            plan = [RefreshPlanItem(1, {"id": 1})]
            with patch("src.jobs.runner.plan_upcoming_refreshes", return_value=plan):
                with patch("src.jobs.runner.refresh_match_prediction", side_effect=Exception("boom")):
                    first = runner.run(window_hours=48)
            with patch("src.jobs.runner.refresh_match_prediction", return_value=Path("ok.json")):
                retry = runner.retry_failed(first.run_id)
            self.assertEqual(retry.refreshed, 1)
            self.assertEqual(retry.failed, 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Implement runner**

Create `src/jobs/runner.py`:

```python
import uuid
from datetime import datetime, timezone
from pathlib import Path

from src.jobs.job_store import RefreshJobStore
from src.jobs.models import RefreshJobAttempt, RefreshJobRun
from src.jobs.planner import RefreshPlanItem, plan_upcoming_refreshes
from src.jobs.refresh_predictions import refresh_match_prediction


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso() -> str:
    return _now().isoformat()


def _duration_ms(started: datetime, finished: datetime) -> int:
    return int((finished - started).total_seconds() * 1000)


class RefreshJobRunner:
    def __init__(self, store: RefreshJobStore):
        self.store = store

    def run(self, window_hours: int = 48, force: bool = False) -> RefreshJobRun:
        run = RefreshJobRun(
            run_id=f"run_{uuid.uuid4().hex[:16]}",
            status="running",
            started_at=_iso(),
            finished_at=None,
            window_hours=window_hours,
            total=0,
            refreshed=0,
            skipped=0,
            failed=0,
            force=force,
        )
        self.store.save_run(run)
        plan = plan_upcoming_refreshes(window_hours=window_hours)
        self._execute_plan(run, plan, force=force)
        return run

    def retry_failed(self, run_id: str) -> RefreshJobRun:
        failed = self.store.failed_attempts(run_id)
        plan = [RefreshPlanItem(item.fixture_id, {"id": item.fixture_id}) for item in failed]
        retry = RefreshJobRun(
            run_id=f"run_{uuid.uuid4().hex[:16]}",
            status="running",
            started_at=_iso(),
            finished_at=None,
            window_hours=0,
            total=0,
            refreshed=0,
            skipped=0,
            failed=0,
            force=True,
        )
        self.store.save_run(retry)
        self._execute_plan(retry, plan, force=True)
        return retry

    def _execute_plan(self, run: RefreshJobRun, plan: list[RefreshPlanItem], force: bool) -> None:
        run.total = len(plan)
        for item in plan:
            started = _now()
            started_iso = started.isoformat()
            try:
                path = refresh_match_prediction(item.fixture_id, force=force)
                finished = _now()
                if path is None:
                    run.skipped += 1
                    attempt = RefreshJobAttempt(
                        run_id=run.run_id,
                        fixture_id=item.fixture_id,
                        status="skipped",
                        started_at=started_iso,
                        finished_at=finished.isoformat(),
                        duration_ms=_duration_ms(started, finished),
                        snapshot_path=None,
                        error=None,
                        skipped_reason="not_due",
                    )
                else:
                    run.refreshed += 1
                    attempt = RefreshJobAttempt(
                        run_id=run.run_id,
                        fixture_id=item.fixture_id,
                        status="refreshed",
                        started_at=started_iso,
                        finished_at=finished.isoformat(),
                        duration_ms=_duration_ms(started, finished),
                        snapshot_path=str(path),
                        error=None,
                        skipped_reason=None,
                    )
            except Exception as exc:
                finished = _now()
                run.failed += 1
                attempt = RefreshJobAttempt(
                    run_id=run.run_id,
                    fixture_id=item.fixture_id,
                    status="failed",
                    started_at=started_iso,
                    finished_at=finished.isoformat(),
                    duration_ms=_duration_ms(started, finished),
                    snapshot_path=None,
                    error=str(exc),
                    skipped_reason=None,
                )
            self.store.append_attempt(attempt)
        run.finished_at = _iso()
        if run.failed and (run.refreshed or run.skipped):
            run.status = "partial_failure"
        elif run.failed:
            run.status = "failed"
        else:
            run.status = "success"
        self.store.save_run(run)
```

- [ ] **Step 3: Run runner tests**

Run:

```powershell
python -m unittest tests.test_refresh_runner
```

Expected: OK.

## Task 5: CLI Integration

**Files:**

- Modify: `src/jobs/refresh_predictions.py`
- Modify: `tests/test_health_and_refresh.py`

- [ ] **Step 1: Preserve existing refresh helper tests**

Run current tests before editing:

```powershell
python -m unittest tests.test_health_and_refresh
```

Expected: OK.

- [ ] **Step 2: Add CLI-facing helpers**

Modify `src/jobs/refresh_predictions.py` to add:

```python
DEFAULT_JOB_STORE_PATH = Path(__file__).parent.parent.parent / "data" / "jobs" / "refresh_runs.json"


def build_default_runner():
    from src.jobs.job_store import RefreshJobStore
    from src.jobs.runner import RefreshJobRunner
    return RefreshJobRunner(RefreshJobStore(DEFAULT_JOB_STORE_PATH))


def format_run_summary(run) -> str:
    return (
        f"run_id={run.run_id} status={run.status} total={run.total} "
        f"refreshed={run.refreshed} skipped={run.skipped} failed={run.failed}"
    )
```

Update argparse:

```python
parser.add_argument("--retry-failed", action="store_true")
parser.add_argument("--latest", action="store_true")
```

In main:

```python
runner = build_default_runner()
if args.latest:
    latest = runner.store.latest_run()
    print(format_run_summary(latest) if latest else "No refresh runs")
elif args.retry_failed:
    latest = runner.store.latest_run()
    if latest is None:
        print("No refresh runs to retry")
    else:
        print(format_run_summary(runner.retry_failed(latest.run_id)))
elif args.fixture_id:
    path = refresh_match_prediction(args.fixture_id, force=args.force)
    print(f"Refreshed: {path}" if path else "Skipped (not due for refresh)")
else:
    print(format_run_summary(runner.run(window_hours=args.window_hours, force=args.force)))
```

- [ ] **Step 3: Run existing refresh tests**

Run:

```powershell
python -m unittest tests.test_health_and_refresh tests.test_refresh_runner
```

Expected: OK.

## Task 6: API Admin Endpoints

**Files:**

- Modify: `src/api/app.py`
- Create: `tests/test_api_refresh_jobs.py`

- [ ] **Step 1: Write API tests**

Create `tests/test_api_refresh_jobs.py`:

```python
import unittest
from unittest.mock import MagicMock, patch

from src.api.app import app
from src.jobs.models import RefreshJobRun


class ApiRefreshJobsTests(unittest.TestCase):
    def _admin_billing(self):
        fake = MagicMock()
        fake.resolve_tier.return_value = "admin"
        return fake

    def _run(self):
        return RefreshJobRun(
            run_id="run_1",
            status="success",
            started_at="2026-06-29T00:00:00+00:00",
            finished_at="2026-06-29T00:01:00+00:00",
            window_hours=48,
            total=1,
            refreshed=1,
            skipped=0,
            failed=0,
        )

    def test_latest_requires_admin(self):
        fake_billing = MagicMock()
        fake_billing.resolve_tier.return_value = "vip"
        with patch("src.api.app._billing_service", fake_billing):
            with app.test_client() as c:
                r = c.get("/api/admin/jobs/refresh/latest", headers={"X-User-Id": "u1"})
                self.assertEqual(r.status_code, 403)

    def test_latest_returns_run(self):
        fake_runner = MagicMock()
        fake_runner.store.latest_run.return_value = self._run()
        fake_runner.store.list_attempts.return_value = []
        with patch("src.api.app._billing_service", self._admin_billing()):
            with patch("src.api.app._refresh_runner", fake_runner):
                with app.test_client() as c:
                    r = c.get("/api/admin/jobs/refresh/latest", headers={"X-User-Id": "admin"})
                    body = r.get_json()
                    self.assertTrue(body["ok"])
                    self.assertEqual(body["data"]["run"]["run_id"], "run_1")

    def test_run_starts_refresh(self):
        fake_runner = MagicMock()
        fake_runner.run.return_value = self._run()
        with patch("src.api.app._billing_service", self._admin_billing()):
            with patch("src.api.app._refresh_runner", fake_runner):
                with app.test_client() as c:
                    r = c.post(
                        "/api/admin/jobs/refresh/run",
                        headers={"X-User-Id": "admin"},
                        json={"window_hours": 24, "force": False},
                    )
                    body = r.get_json()
                    self.assertTrue(body["ok"])
                    fake_runner.run.assert_called_with(window_hours=24, force=False)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Add app wiring**

In `src/api/app.py`, add imports:

```python
from src.jobs.job_store import RefreshJobStore
from src.jobs.runner import RefreshJobRunner
```

Add construction:

```python
_JOBS_DIR = Path(__file__).parent.parent.parent / "data" / "jobs"
_refresh_runner = RefreshJobRunner(RefreshJobStore(_JOBS_DIR / "refresh_runs.json"))
```

Add endpoints:

```python
@app.route("/api/admin/jobs/refresh/latest")
def admin_latest_refresh_run():
    ctx = _ctx()
    ctx.require_tier("admin")
    run = _refresh_runner.store.latest_run()
    if run is None:
        return api_success({"run": None, "attempts": []}, ctx.request_id)
    attempts = _refresh_runner.store.list_attempts(run.run_id)
    return api_success(
        {"run": run.to_dict(), "attempts": [item.to_dict() for item in attempts]},
        ctx.request_id,
    )


@app.route("/api/admin/jobs/refresh/run", methods=["POST"])
def admin_run_refresh_job():
    ctx = _ctx()
    ctx.require_tier("admin")
    payload = get_json_body()
    window_hours = int(payload.get("window_hours", 48))
    force = bool(payload.get("force", False))
    run = _refresh_runner.run(window_hours=window_hours, force=force)
    return api_success(run.to_dict(), ctx.request_id)
```

- [ ] **Step 3: Run API refresh tests**

Run:

```powershell
python -m unittest tests.test_api_refresh_jobs
```

Expected: OK.

## Task 7: Health Check Integration

**Files:**

- Modify: `src/monitoring/health.py`
- Modify: `tests/test_health_and_refresh.py`

- [ ] **Step 1: Add health tests**

Append to `tests/test_health_and_refresh.py`:

```python
class RefreshJobHealthTests(unittest.TestCase):
    def test_refresh_job_health_no_runs_ok(self):
        from unittest.mock import MagicMock, patch
        from src.monitoring.health import check_refresh_jobs
        fake_store = MagicMock()
        fake_store.latest_run.return_value = None
        with patch("src.monitoring.health.RefreshJobStore", return_value=fake_store):
            result = check_refresh_jobs()
            self.assertTrue(result["ok"])

    def test_refresh_job_health_failed_latest_degraded(self):
        from unittest.mock import MagicMock, patch
        from src.jobs.models import RefreshJobRun
        from src.monitoring.health import check_refresh_jobs
        fake_store = MagicMock()
        fake_store.latest_run.return_value = RefreshJobRun(
            run_id="run_1",
            status="failed",
            started_at="2026-06-29T00:00:00+00:00",
            finished_at="2026-06-29T00:01:00+00:00",
            window_hours=48,
            total=1,
            refreshed=0,
            skipped=0,
            failed=1,
        )
        with patch("src.monitoring.health.RefreshJobStore", return_value=fake_store):
            result = check_refresh_jobs()
            self.assertFalse(result["ok"])
```

- [ ] **Step 2: Implement health check**

Modify `src/monitoring/health.py`:

```python
def check_refresh_jobs() -> dict:
    try:
        from src.jobs.job_store import RefreshJobStore
        path = Path(__file__).parent.parent.parent / "data" / "jobs" / "refresh_runs.json"
        store = RefreshJobStore(path)
        latest = store.latest_run()
        if latest is None:
            return {"ok": True, "detail": "no refresh runs yet"}
        if latest.status in ("failed", "partial_failure"):
            return {"ok": False, "detail": f"latest refresh run {latest.run_id}: {latest.status}"}
        return {"ok": True, "detail": f"latest refresh run {latest.run_id}: {latest.status}"}
    except Exception as e:
        return {"ok": False, "detail": str(e)}
```

Add to `check_all()`:

```python
"refresh_jobs": check_refresh_jobs(),
```

- [ ] **Step 3: Run health tests**

Run:

```powershell
python -m unittest tests.test_health_and_refresh
```

Expected: OK.

## Task 8: Documentation

**Files:**

- Create: `docs/refresh_jobs.md`
- Modify: `docs/api_error_codes.md`

- [ ] **Step 1: Write refresh jobs docs**

Create `docs/refresh_jobs.md`:

```markdown
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

Retry failed attempts:

```powershell
python src/jobs/refresh_predictions.py --retry-failed
```

Inspect latest:

```powershell
python src/jobs/refresh_predictions.py --latest
```

## API

`GET /api/admin/jobs/refresh/latest` requires admin.

`POST /api/admin/jobs/refresh/run` requires admin.

## Persistence

Job runs are stored in `data/jobs/refresh_runs.json`.
```

- [ ] **Step 2: Update API error docs**

Add these rows to `docs/api_error_codes.md`:

```markdown
| job_error | 500 | Refresh job runner failed |
| admin_required | 403 | Admin-only endpoint |
```

## Task 9: Final Verification

**Files:**

- All Step 10D files

- [ ] **Step 1: Run focused job tests**

Run:

```powershell
python -m unittest tests.test_refresh_job_models tests.test_refresh_job_store tests.test_refresh_planner tests.test_refresh_runner tests.test_api_refresh_jobs
```

Expected: OK.

- [ ] **Step 2: Run existing refresh and API tests**

Run:

```powershell
python -m unittest tests.test_health_and_refresh tests.test_api_contract tests.test_api_app tests.test_api_billing
```

Expected: OK.

- [ ] **Step 3: Run full test suite**

Run:

```powershell
python -m unittest discover -s tests
```

Expected: all tests pass.

- [ ] **Step 4: Manual CLI smoke**

Run:

```powershell
python src/jobs/refresh_predictions.py --latest
python src/jobs/refresh_predictions.py --window-hours 1
python src/jobs/refresh_predictions.py --latest
```

Expected:

- First command prints `No refresh runs` or latest run summary.
- Second command prints a `run_id=...` summary.
- Third command prints the same latest run id.

- [ ] **Step 5: Manual API smoke**

Start API:

```powershell
python src/api/app.py
```

Then call admin endpoint with a mocked/persisted admin user path if available:

```powershell
curl http://localhost:5000/api/admin/jobs/refresh/latest -H "X-User-Id: admin_user"
```

Expected:

- Non-admin returns `403`.
- Admin returns `ok=true`.

## Implementation Order

1. Task 1: job models.
2. Task 2: job store.
3. Task 3: refresh planner.
4. Task 4: refresh runner.
5. Task 5: CLI integration.
6. Task 6: admin API endpoints.
7. Task 7: health check integration.
8. Task 8: docs.
9. Task 9: focused tests, full tests, manual smoke.

## Acceptance Criteria

- Every refresh run is persisted with run-level summary.
- Every fixture attempt is persisted with status and error/snapshot path.
- Batch refresh continues after individual fixture failures.
- Failed attempts can be retried.
- Existing refresh rules still pass.
- Admin API exposes latest run and can trigger a run.
- Health check reports failed latest refresh as degraded.
- Full test suite passes.

## Known Follow-Ups For Step 10E

- Build H5/VIP frontend around stable Step 10C API responses.
- Surface stale prediction and refresh status in admin UI.
- Add visible prediction update timestamps on match cards.
- Add paid/unpaid UI feature gating.
