import tests.warnings_filter  # noqa: F401 — suppress pickle warnings

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
                with patch("src.jobs.runner.refresh_match_prediction",
                           side_effect=[Exception("boom"), Path("b.json")]):
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
