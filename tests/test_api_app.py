"""Flask API endpoint tests."""
import tests.warnings_filter  # noqa: F401 - suppress pickle warnings

import importlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.api.app import app
from src.models.prediction_snapshot import build_prediction_snapshot
from src.models.score_models import ScoreMatrix


def _make_snapshot():
    sm = ScoreMatrix.from_independent_poisson(1.4, 1.1)
    return build_prediction_snapshot(
        match_id="sportmonks-123",
        home_team="A",
        away_team="B",
        score_matrix=sm,
        final_probs=sm.match_outcome_probs(),
        model_version="dc-v1",
        data_version="test",
        confidence=0.7,
        market_probs={"home": 0.5, "draw": 0.27, "away": 0.23},
        lineup_data_quality="none",
        data_updated_at="2026-06-28T12:00:00Z",
        prematch_snapshot_id="snap_123",
        model_pipeline={"base": "dc"},
    )


def _data(r):
    body = r.get_json()
    return body["data"] if body.get("ok") else body


def _mock_billing(tier="free"):
    fake = MagicMock()
    fake.resolve_tier.return_value = tier
    return fake


AUTH = {"X-User-Id": "basic_user"}


class ApiPredictionsTests(unittest.TestCase):
    def _cache_payload(self):
        return _make_snapshot().to_public_dict(tier="internal")

    def test_invalid_fixture_id_returns_400(self):
        with app.test_client() as c:
            r = c.get("/api/predictions/not_a_number", headers=AUTH)
            self.assertEqual(r.status_code, 400)

    def test_prediction_requires_authenticated_user(self):
        cached = self._cache_payload()
        with patch("src.api.app.load_latest_prediction", return_value=cached):
            with app.test_client() as c:
                r = c.get("/api/predictions/123")
                self.assertEqual(r.status_code, 401)

    def test_cache_hit_returns_200(self):
        cached = self._cache_payload()
        with patch("src.api.app._billing_service", _mock_billing("basic")):
            with patch("src.api.app.load_latest_prediction", return_value=cached):
                with app.test_client() as c:
                    r = c.get("/api/predictions/123", headers=AUTH)
                    self.assertEqual(r.status_code, 200)
                    self.assertEqual(r.get_json()["meta"]["source"], "cache")


class ApiFixtureSearchTests(unittest.TestCase):
    def _fixture(self, home="Brazil", away="Argentina", fixture_id=12345):
        return {
            "id": fixture_id,
            "starting_at": "2026-06-20 18:00:00",
            "participants": [
                {"name": home, "meta": {"location": "home"}},
                {"name": away, "meta": {"location": "away"}},
            ],
            "league": {"name": "World Cup"},
            "league_id": 732,
        }

    def test_search_fixtures_requires_authenticated_user(self):
        with app.test_client() as c:
            r = c.get("/api/search/fixtures?home=Brazil&away=Argentina")
            self.assertEqual(r.status_code, 401)

    def test_search_fixtures_prefers_world_cup_fixture_cache(self):
        fixture = self._fixture("Paraguay", "Spain")
        with patch("src.api.app._get_world_cup_fixtures", return_value=([fixture], "hit")) as wc_mock:
            with patch("src.data.sportmonks_client.get_fixtures_between") as slow_scan_mock:
                with app.test_client() as c:
                    r = c.get("/api/search/fixtures?home=Paraguay&away=Spain", headers=AUTH)
                    self.assertEqual(r.status_code, 200)
                    self.assertEqual(_data(r)["count"], 1)
                    self.assertEqual(r.get_json()["meta"]["cache"], "hit")
                    wc_mock.assert_called_once()
                    slow_scan_mock.assert_not_called()

    def test_world_cup_window_is_clamped_to_july_21(self):
        from src.api import app as app_module

        with patch("src.data.sportmonks_client.get_current_season", return_value={
            "starting_at": "2026-05-15 00:00:00",
            "ending_at": "2026-08-05 00:00:00",
        }):
            from_date, to_date = app_module._world_cup_window()
            self.assertEqual(from_date, datetime(2026, 6, 1, tzinfo=timezone.utc))
            self.assertEqual(to_date, datetime(2026, 7, 21, 23, 59, tzinfo=timezone.utc))


class ApiTeamSearchTests(unittest.TestCase):
    def test_team_search_returns_chinese_match(self):
        with app.test_client() as c:
            r = c.get("/api/teams/search?q=西班")
            self.assertEqual(r.status_code, 200)
            first = _data(r)["teams"][0]
            self.assertEqual(first["display_name"], "Spain")
            self.assertEqual(first["sportmonks_name"], "Spain")

    def test_team_search_matches_paraguay_chinese_and_english(self):
        with app.test_client() as c:
            zh = c.get("/api/teams/search?q=巴拉")
            en = c.get("/api/teams/search?q=Paraguay")
            self.assertEqual(zh.status_code, 200)
            self.assertEqual(en.status_code, 200)
            self.assertEqual(_data(zh)["teams"][0]["display_name"], "Paraguay")
            self.assertEqual(_data(en)["teams"][0]["sportmonks_name"], "Paraguay")

    def test_team_search_includes_world_cup_fixture_participants(self):
        fixtures = []
        for i in range(64):
            fixtures.append(self._fixture(home=f"Team {i}", away=f"Opponent {i}", fixture_id=20000 + i))
        with patch("src.api.app._get_world_cup_fixtures", return_value=(fixtures, "hit")):
            with app.test_client() as c:
                r = c.get("/api/teams/search?q=Team 63")
                self.assertEqual(r.status_code, 200)
                self.assertEqual(_data(r)["teams"][0]["sportmonks_name"], "Team 63")

    @staticmethod
    def _fixture(home="Brazil", away="Argentina", fixture_id=12345):
        return {
            "id": fixture_id,
            "starting_at": "2026-06-20 18:00:00",
            "participants": [
                {"name": home, "meta": {"location": "home"}},
                {"name": away, "meta": {"location": "away"}},
            ],
            "league": {"name": "World Cup"},
            "league_id": 732,
        }


class ApiRuntimeWiringTests(unittest.TestCase):
    def test_runtime_data_dir_wires_app_and_cache_paths(self):
        import src.api.app as app_module
        import src.monitoring.health as health_module
        import src.services.prediction_service as prediction_service_module

        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "APP_ENV": "dev",
                "DATA_DIR": tmp,
                "BETA_AUTH_SECRET": "beta-secret",
                "PAYMENT_WEBHOOK_SECRET": "webhook-secret",
            }
            with patch.dict("os.environ", env, clear=False):
                prediction_service_module = importlib.reload(prediction_service_module)
                health_module = importlib.reload(health_module)
                app_module = importlib.reload(app_module)
                self.assertEqual(app_module._BILLING_DIR, Path(tmp) / "billing")
                self.assertEqual(app_module._JOBS_DIR, Path(tmp) / "jobs")
                self.assertEqual(prediction_service_module.CACHE_DIR, Path(tmp) / "prediction_snapshots")
                self.assertEqual(health_module._CACHE_DIR, Path(tmp) / "prediction_snapshots")


if __name__ == "__main__":
    unittest.main()
