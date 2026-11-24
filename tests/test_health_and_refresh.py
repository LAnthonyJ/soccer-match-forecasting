import tests.warnings_filter  # noqa: F401 — suppress pickle warnings

import unittest
from datetime import datetime, timedelta, timezone

from src.jobs.refresh_predictions import should_refresh_prediction


class CacheRefreshTests(unittest.TestCase):
    def test_no_refresh_after_kickoff(self):
        kickoff = datetime.now(timezone.utc) - timedelta(hours=1)
        last = datetime.now(timezone.utc) - timedelta(hours=3)
        self.assertFalse(should_refresh_prediction(kickoff, last))

    def test_refresh_when_stale_6h_before_kickoff(self):
        kickoff = datetime.now(timezone.utc) + timedelta(hours=10)
        last = datetime.now(timezone.utc) - timedelta(hours=2)
        self.assertTrue(should_refresh_prediction(kickoff, last))

    def test_no_refresh_when_fresh_6h_before_kickoff(self):
        kickoff = datetime.now(timezone.utc) + timedelta(hours=10)
        last = datetime.now(timezone.utc) - timedelta(minutes=30)
        self.assertFalse(should_refresh_prediction(kickoff, last))

    def test_high_freq_refresh_2h_before_kickoff(self):
        kickoff = datetime.now(timezone.utc) + timedelta(hours=2)
        last = datetime.now(timezone.utc) - timedelta(minutes=20)
        self.assertTrue(should_refresh_prediction(kickoff, last))

    def test_no_refresh_final_hour(self):
        kickoff = datetime.now(timezone.utc) + timedelta(minutes=30)
        last = datetime.now(timezone.utc) - timedelta(hours=1)
        self.assertFalse(should_refresh_prediction(kickoff, last))

    def test_confirmed_lineup_locks_prediction(self):
        kickoff = datetime.now(timezone.utc) + timedelta(hours=3)
        last = datetime.now(timezone.utc) - timedelta(hours=2)
        self.assertFalse(
            should_refresh_prediction(kickoff, last, lineup_status="confirmed"),
        )

    def test_no_last_update_always_refreshes(self):
        kickoff = datetime.now(timezone.utc) + timedelta(hours=12)
        self.assertTrue(should_refresh_prediction(kickoff, None))

    def test_naive_kickoff_normalized(self):
        # Future naive datetime assumed UTC by the function
        kickoff = datetime.now(timezone.utc) + timedelta(hours=12)
        kickoff_naive = kickoff.replace(tzinfo=None)
        last = datetime.now(timezone.utc) - timedelta(hours=3)
        self.assertTrue(should_refresh_prediction(kickoff_naive, last))


class RefreshIntegrationTests(unittest.TestCase):
    def test_refresh_skips_when_fresh_cache(self):
        from unittest.mock import patch
        from src.jobs.refresh_predictions import refresh_match_prediction
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        # Cache from 30 min ago, kickoff in 12 hours → should skip
        cached = {
            "generated_at": (now - timedelta(minutes=30)).isoformat(),
            "_kickoff_utc": (now + timedelta(hours=12)).isoformat(),
            "lineup_data_status": "none",
        }
        with patch("src.services.prediction_service.load_latest_prediction", return_value=cached):
            path = refresh_match_prediction(123, force=False)
            self.assertIsNone(path, "should skip when cache is fresh")

    def test_refresh_runs_with_force(self):
        from unittest.mock import patch, MagicMock
        from src.jobs.refresh_predictions import refresh_match_prediction
        fake_result = MagicMock()
        fake_result.snapshot.to_public_dict.return_value = {}
        fake_path = MagicMock()
        with patch("src.services.prediction_service.PredictionService.predict_match",
                   return_value=fake_result):
            with patch("src.services.prediction_service.save_prediction_snapshot",
                       return_value=fake_path):
                path = refresh_match_prediction(123, force=True)
                self.assertIsNotNone(path)

    def test_single_failure_not_abort_batch(self):
        from unittest.mock import patch, MagicMock
        from src.jobs.refresh_predictions import refresh_upcoming_predictions
        fake_fixtures = [{"id": 1}, {"id": 2}]
        fake_result = MagicMock()
        fake_result.snapshot.to_public_dict.return_value = {}
        mock_svc = MagicMock()
        mock_svc.predict_match.side_effect = [Exception("fail"), fake_result]

        with patch("src.data.sportmonks_client.get_fixtures_between", return_value=fake_fixtures):
            with patch("src.services.prediction_service.PredictionService", return_value=mock_svc):
                with patch("src.services.prediction_service.save_prediction_snapshot",
                           return_value=MagicMock()):
                    with patch("src.services.prediction_service.load_latest_prediction",
                               return_value=None):
                        paths = refresh_upcoming_predictions(window_hours=48)
                        self.assertEqual(len(paths), 1)


class HealthCheckTests(unittest.TestCase):
    def test_model_available(self):
        from unittest.mock import MagicMock, patch
        from src.monitoring.health import check_model_available
        fake_dc = MagicMock()
        fake_models = {"dixon_coles": fake_dc}
        with patch("src.models.score_models.load_score_models", return_value=fake_models):
            result = check_model_available()
            self.assertTrue(result["ok"])

    def test_cache_writable_success(self):
        from unittest.mock import patch, MagicMock
        from src.monitoring.health import check_cache_writable
        good_cm = MagicMock()
        good_cm.__enter__.return_value = MagicMock()  # f.write() no-op
        with patch("tempfile.NamedTemporaryFile", return_value=good_cm):
            result = check_cache_writable()
            self.assertTrue(result["ok"], f"should pass: {result}")

    def test_cache_writable_failure_returns_false(self):
        from unittest.mock import patch, MagicMock
        from src.monitoring.health import check_cache_writable
        # Both tempfile probes fail (cache dir + system fallback)
        bad_cm = MagicMock()
        bad_cm.__enter__.side_effect = OSError("denied")
        with patch("tempfile.NamedTemporaryFile", return_value=bad_cm):
            result = check_cache_writable()
            self.assertFalse(result["ok"], f"should fail: {result}")

    def test_check_all_returns_structure(self):
        from unittest.mock import MagicMock, patch
        from src.monitoring.health import check_all
        fake_checks = {
            "model": {"ok": True, "detail": "mocked"},
            "cache": {"ok": True, "detail": "mocked"},
            "api": {"ok": True, "detail": "mocked"},
            "prediction_age": {"ok": True, "detail": "mocked"},
            "refresh_jobs": {"ok": True, "detail": "mocked"},
            "runtime": {"status": "ok", "app_env": "dev"},
            "storage": {"status": "ok", "paths": {}},
        }
        with patch.multiple(
            "src.monitoring.health",
            check_model_available=MagicMock(return_value=fake_checks["model"]),
            check_cache_writable=MagicMock(return_value=fake_checks["cache"]),
            check_sportmonks_available=MagicMock(return_value=fake_checks["api"]),
            check_latest_prediction_age=MagicMock(return_value=fake_checks["prediction_age"]),
            check_refresh_jobs=MagicMock(return_value=fake_checks["refresh_jobs"]),
            check_runtime=MagicMock(return_value=fake_checks["runtime"]),
            check_storage_paths=MagicMock(return_value=fake_checks["storage"]),
        ):
            result = check_all()
            self.assertIn("healthy", result)
            self.assertIn("status", result)
            self.assertIn("checks", result)
            self.assertIn("model", result["checks"])


class LiveHealthIsolationTests(unittest.TestCase):
    def test_check_all_live_does_not_import_prediction_service(self):
        """Regression: importing check_all_live must not pull in the model stack."""
        import sys
        sys.modules.pop("src.services.prediction_service", None)
        from src.monitoring.health import check_all_live  # noqa: F811
        self.assertNotIn("src.services.prediction_service", sys.modules,
                         "check_all_live import pulled in prediction_service — live health is miswired")

    def test_check_all_live_is_fast_path(self):
        """check_all_live must not call check_model_available or check_sportmonks_available."""
        from unittest.mock import patch, MagicMock
        with patch("src.monitoring.health.check_cache_writable", return_value={"ok": True, "detail": "ok"}) as mc:
            with patch("src.monitoring.health.check_runtime", return_value={"status": "ok", "app_env": "dev"}):
                with patch("src.monitoring.health.check_storage_paths", return_value={"status": "ok", "paths": {}}):
                    with patch("src.monitoring.health.check_latest_prediction_age", return_value={"ok": True, "detail": ""}):
                        with patch("src.monitoring.health.check_refresh_jobs", return_value={"ok": True, "detail": ""}):
                            from src.monitoring.health import check_all_live
                            result = check_all_live()
                            self.assertTrue(result["healthy"])
                            mc.assert_called_once_with(probe_cache_dir=False)



    def test_check_all_live_never_touches_cache_dir(self):
        """Regression: live health must not probe the business cache directory."""
        from unittest.mock import MagicMock, patch
        from src.monitoring.health import check_all_live

        calls = []

        def fake_tmp(*args, **kwargs):
            calls.append(kwargs.copy())
            cm = MagicMock()
            cm.__enter__.return_value = MagicMock()
            return cm

        with patch("pathlib.Path.mkdir", return_value=None):
            with patch("tempfile.NamedTemporaryFile", side_effect=fake_tmp):
                with patch("src.monitoring.health.check_runtime", return_value={"status": "ok", "app_env": "dev"}):
                    with patch("src.monitoring.health.check_storage_paths", return_value={"status": "ok", "paths": {}}):
                        with patch("src.monitoring.health.check_latest_prediction_age", return_value={"ok": True, "detail": ""}):
                            with patch("src.monitoring.health.check_refresh_jobs", return_value={"ok": True, "detail": ""}):
                                result = check_all_live()
        self.assertTrue(result["healthy"])
        self.assertTrue(calls)
        self.assertTrue(all("dir" not in kwargs for kwargs in calls), calls)


    def test_check_all_live_storage_is_fast_path(self):
        """Regression: live health must not perform per-directory write probes."""
        from unittest.mock import patch
        from src.monitoring.health import check_all_live

        with patch("src.monitoring.health.check_cache_writable", return_value={"ok": True, "detail": "ok"}):
            with patch("src.monitoring.health.check_runtime", return_value={"status": "ok", "app_env": "dev"}):
                with patch("src.monitoring.health.check_storage_paths", return_value={"status": "ok", "paths": {}}) as storage:
                    with patch("src.monitoring.health.check_latest_prediction_age", return_value={"ok": True, "detail": ""}):
                        with patch("src.monitoring.health.check_refresh_jobs", return_value={"ok": True, "detail": ""}):
                            result = check_all_live()
        self.assertTrue(result["healthy"])
        storage.assert_called_once_with(probe_writable=False)

class RuntimeHealthTests(unittest.TestCase):
    def test_runtime_health_reports_environment(self):
        from src.monitoring.health import check_runtime

        result = check_runtime()
        self.assertIn("status", result)
        self.assertIn("app_env", result)
        self.assertIn(result["status"], {"ok", "warning", "failed"})

    def test_storage_health_reports_required_directories(self):
        from unittest.mock import MagicMock, patch
        from src.monitoring.health import check_storage_paths

        # Mock filesystem I/O; let the real function logic run
        good_cm = MagicMock()
        good_cm.__enter__.return_value = MagicMock()  # f.write() no-op
        with patch("tempfile.NamedTemporaryFile", return_value=good_cm):
            with patch("pathlib.Path.mkdir", return_value=None):
                result = check_storage_paths()
                self.assertIn("status", result)
                self.assertIn("paths", result)
                self.assertIsInstance(result["paths"], dict)


class RefreshJobHealthTests(unittest.TestCase):
    def test_refresh_job_health_no_runs_ok(self):
        from unittest.mock import MagicMock, patch
        from src.monitoring.health import check_refresh_jobs
        fake_store = MagicMock()
        fake_store.latest_run.return_value = None
        with patch("src.jobs.job_store.RefreshJobStore", return_value=fake_store):
            result = check_refresh_jobs()
            self.assertTrue(result["ok"])

    def test_refresh_job_health_failed_latest_degraded(self):
        from unittest.mock import MagicMock, patch
        from src.jobs.models import RefreshJobRun
        from src.monitoring.health import check_refresh_jobs
        fake_store = MagicMock()
        fake_store.latest_run.return_value = RefreshJobRun(
            run_id="run_1", status="failed",
            started_at="2026-06-29T00:00:00+00:00", finished_at="2026-06-29T00:01:00+00:00",
            window_hours=48, total=1, refreshed=0, skipped=0, failed=1,
        )
        with patch("src.jobs.job_store.RefreshJobStore", return_value=fake_store):
            result = check_refresh_jobs()
            self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()






