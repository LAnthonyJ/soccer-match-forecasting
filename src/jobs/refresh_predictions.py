"""
Scheduled prediction cache refresh.

Refresh rules:
    >24h before kickoff:  every 6h
    6-24h before kickoff: every 1h
    1-6h before kickoff:  every 15min
    <1h or after kickoff: no refresh (locked)
    confirmed lineup:     generate locked prediction, then no further refresh

Usage:
    python src/jobs/refresh_predictions.py --window-hours 48
"""
import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def should_refresh_prediction(
    kickoff_utc: datetime,
    last_updated_at: datetime | None,
    lineup_status: str = "none",
) -> bool:
    """
    Determine whether a prediction should be refreshed.

    Returns True if the cache is stale based on time-to-kickoff rules.
    """
    now = datetime.now(timezone.utc)

    # After kickoff: never refresh
    if kickoff_utc.tzinfo is None:
        kickoff_utc = kickoff_utc.replace(tzinfo=timezone.utc)
    if now >= kickoff_utc:
        return False

    # Confirmed lineup: prediction is locked, no refresh
    if lineup_status == "confirmed":
        return False

    hours_to_kickoff = (kickoff_utc - now).total_seconds() / 3600

    # Determine refresh interval
    if hours_to_kickoff > 24:
        max_age_hours = 6
    elif hours_to_kickoff > 6:
        max_age_hours = 1
    elif hours_to_kickoff > 1:
        max_age_hours = 0.25  # 15 min
    else:
        return False  # locked in final hour

    if last_updated_at is None:
        return True

    age_hours = (now - last_updated_at).total_seconds() / 3600
    return age_hours >= max_age_hours


def refresh_match_prediction(fixture_id: int, force: bool = False) -> Path | None:
    """Refresh prediction for a single fixture. Returns saved path or None if skipped."""
    from src.services.prediction_service import (
        PredictionService,
        load_latest_prediction,
        save_prediction_snapshot,
    )

    service = PredictionService()

    if not force:
        # Check if refresh is needed
        cached = load_latest_prediction(f"sportmonks-{fixture_id}")
        if cached:
            try:
                kickoff = datetime.fromisoformat(cached.get("_kickoff_utc", ""))
            except (ValueError, TypeError):
                kickoff = datetime.now(timezone.utc) + timedelta(days=1)
            last_updated = None
            if cached.get("generated_at"):
                try:
                    last_updated = datetime.fromisoformat(cached["generated_at"])
                except (ValueError, TypeError):
                    pass
            lineup = cached.get("lineup_data_status", "none")
            if not should_refresh_prediction(kickoff, last_updated, lineup):
                return None

    result = service.predict_match(fixture_id=fixture_id, use_prematch_api=True)
    path = save_prediction_snapshot(result.snapshot, tier="vip")
    return path


def refresh_upcoming_predictions(window_hours: int = 48) -> list[Path]:
    """
    Refresh predictions for all fixtures in the upcoming window.

    Returns list of saved paths.
    """
    from src.data.sportmonks_client import get_fixtures_between
    from src.data.sportmonks_ingestion import INTERNATIONAL_LEAGUES

    now = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=window_hours)

    fixtures = get_fixtures_between(
        now, window_end,
        league_ids=list(INTERNATIONAL_LEAGUES.keys()),
        includes="participants;league",
    )

    saved = []
    for f in fixtures:
        fid = f["id"]
        try:
            path = refresh_match_prediction(fid)
            if path:
                saved.append(path)
        except Exception:
            pass  # error captured by caller via RefreshJobAttempt

    return saved


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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Refresh prediction cache")
    parser.add_argument("--window-hours", type=int, default=48)
    parser.add_argument("--fixture-id", type=int, help="Refresh single fixture")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--latest", action="store_true")
    args = parser.parse_args()

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
