import tests.warnings_filter  # noqa: F401 — suppress pickle warnings

import unittest
from src.jobs.models import RefreshJobAttempt, RefreshJobRun


class RefreshJobModelsTests(unittest.TestCase):
    def test_run_roundtrip(self):
        run = RefreshJobRun(
            run_id="run_1", status="running",
            started_at="2026-06-29T00:00:00+00:00", finished_at=None,
            window_hours=48, total=0, refreshed=0, skipped=0, failed=0, force=False,
        )
        loaded = RefreshJobRun.from_dict(run.to_dict())
        self.assertEqual(loaded.run_id, "run_1")
        self.assertEqual(loaded.status, "running")

    def test_attempt_roundtrip(self):
        attempt = RefreshJobAttempt(
            run_id="run_1", fixture_id=123, status="refreshed",
            started_at="2026-06-29T00:00:00+00:00", finished_at="2026-06-29T00:00:01+00:00",
            duration_ms=1000, snapshot_path="data/prediction_snapshots/x.json",
            error=None, skipped_reason=None,
        )
        loaded = RefreshJobAttempt.from_dict(attempt.to_dict())
        self.assertEqual(loaded.fixture_id, 123)
        self.assertEqual(loaded.status, "refreshed")


if __name__ == "__main__":
    unittest.main()
