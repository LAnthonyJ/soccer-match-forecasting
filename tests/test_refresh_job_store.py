import tests.warnings_filter  # noqa: F401 — suppress pickle warnings

import tempfile
import unittest
from pathlib import Path
from src.jobs.job_store import RefreshJobStore
from src.jobs.models import RefreshJobAttempt, RefreshJobRun


class RefreshJobStoreTests(unittest.TestCase):
    def _run(self, run_id="run_1", status="success"):
        return RefreshJobRun(
            run_id=run_id, status=status,
            started_at="2026-06-29T00:00:00+00:00", finished_at="2026-06-29T00:01:00+00:00",
            window_hours=48, total=1, refreshed=1, skipped=0, failed=0,
        )

    def _attempt(self, run_id="run_1", status="refreshed"):
        return RefreshJobAttempt(
            run_id=run_id, fixture_id=123, status=status,
            started_at="2026-06-29T00:00:00+00:00", finished_at="2026-06-29T00:00:01+00:00",
            duration_ms=1000, snapshot_path="snapshot.json", error=None, skipped_reason=None,
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
            r1 = self._run("run_1")
            r2 = self._run("run_2")
            r2.started_at = "2026-06-29T00:02:00+00:00"
            store.save_run(r1)
            store.save_run(r2)
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
