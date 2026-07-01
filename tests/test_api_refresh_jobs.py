import tests.warnings_filter  # noqa: F401 — suppress pickle warnings

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
            run_id="run_1", status="success",
            started_at="2026-06-29T00:00:00+00:00", finished_at="2026-06-29T00:01:00+00:00",
            window_hours=48, total=1, refreshed=1, skipped=0, failed=0,
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
                    r = c.post("/api/admin/jobs/refresh/run",
                               headers={"X-User-Id": "admin"},
                               json={"window_hours": 24, "force": False})
                    body = r.get_json()
                    self.assertTrue(body["ok"])
                    fake_runner.run.assert_called_with(window_hours=24, force=False)


    def test_run_bad_window_hours_returns_400(self):
        fake_runner = MagicMock()
        with patch("src.api.app._billing_service", self._admin_billing()):
            with patch("src.api.app._refresh_runner", fake_runner):
                with app.test_client() as c:
                    r = c.post("/api/admin/jobs/refresh/run",
                               headers={"X-User-Id": "admin"},
                               json={"window_hours": "bad"})
                    body = r.get_json()
                    self.assertEqual(r.status_code, 400)
                    self.assertFalse(body["ok"])

    def test_run_zero_window_hours_returns_400(self):
        fake_runner = MagicMock()
        with patch("src.api.app._billing_service", self._admin_billing()):
            with patch("src.api.app._refresh_runner", fake_runner):
                with app.test_client() as c:
                    r = c.post("/api/admin/jobs/refresh/run",
                               headers={"X-User-Id": "admin"},
                               json={"window_hours": 0})
                    self.assertEqual(r.status_code, 400)

    def test_run_string_force_returns_400(self):
        fake_runner = MagicMock()
        with patch("src.api.app._billing_service", self._admin_billing()):
            with patch("src.api.app._refresh_runner", fake_runner):
                with app.test_client() as c:
                    r = c.post("/api/admin/jobs/refresh/run",
                               headers={"X-User-Id": "admin"},
                               json={"window_hours": 24, "force": "false"})
                    self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
