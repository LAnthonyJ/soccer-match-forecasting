import tests.warnings_filter  # noqa: F401 — suppress pickle warnings

import unittest
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
