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
