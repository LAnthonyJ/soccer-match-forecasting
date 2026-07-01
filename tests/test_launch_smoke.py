import tests.warnings_filter  # noqa: F401 — suppress pickle warnings

import unittest

from src.smoke_test_launch import SmokeCheck, SmokeReport


class LaunchSmokeTests(unittest.TestCase):
    def test_report_success_when_all_checks_pass(self):
        report = SmokeReport([
            SmokeCheck("health", True, "200"),
            SmokeCheck("h5", True, "200"),
        ])
        self.assertTrue(report.ok)
        self.assertEqual(report.failed_count, 0)

    def test_report_failure_when_any_check_fails(self):
        report = SmokeReport([
            SmokeCheck("health", True, "200"),
            SmokeCheck("prediction", False, "503"),
        ])
        self.assertFalse(report.ok)
        self.assertEqual(report.failed_count, 1)

    def test_report_to_dict_is_stable(self):
        report = SmokeReport([SmokeCheck("health", True, "200")])
        payload = report.to_dict()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["checks"][0]["name"], "health")
        self.assertEqual(payload["checks"][0]["detail"], "200")
