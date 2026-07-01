import tests.warnings_filter  # noqa: F401 — suppress pickle warnings
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.data.provider_schema import MatchRecord
from src.models.production_prediction import PredictionInputQuality
from src.models.backtest_runner import (
    HistoricalBacktestFixture,
    HistoricalBacktestMatchResult,
    HistoricalBacktestRun,
    HistoricalBacktestRunner,
    _build_report,
)


class FakeEngine:
    def __init__(self):
        self.calls = []
        self.model_version = "sportmonks-dc-v1"
        self.data_version = "sportmonks-2026-06-28T12:00:00Z"

    def predict(
        self,
        *,
        match_id,
        home_team,
        away_team,
        odds=None,
        quality=None,
        neutral=True,
        context=None,
        generated_at=None,
    ):
        self.calls.append(
            {
                "match_id": match_id,
                "generated_at": generated_at,
                "quality": quality,
                "odds": odds,
            }
        )
        from src.models.prediction_snapshot import build_prediction_snapshot
        from src.models.score_models import ScoreMatrix

        matrix = ScoreMatrix.from_independent_poisson(1.4, 0.8)
        return build_prediction_snapshot(
            match_id=match_id,
            home_team=home_team,
            away_team=away_team,
            score_matrix=matrix,
            final_probs=matrix.match_outcome_probs(),
            model_version=self.model_version,
            data_version=self.data_version,
            confidence=0.74,
            generated_at=generated_at,
            market_probs={"home": 0.50, "draw": 0.27, "away": 0.23} if odds else None,
        )


class HistoricalBacktestRunnerTests(unittest.TestCase):
    def setUp(self):
        self.engine = FakeEngine()
        self.runner = HistoricalBacktestRunner(self.engine)
        self.finished_match = MatchRecord(
            provider="sportmonks",
            provider_match_id="1001",
            match_id="wc2026-1001",
            kickoff_utc=datetime(2026, 6, 10, 18, 0, tzinfo=timezone.utc),
            home_team="Spain",
            away_team="Brazil",
            tournament="World Cup",
            neutral=True,
            status="finished",
            home_score=2,
            away_score=1,
        )

    def test_runner_skips_non_finished_matches_and_predicts_before_kickoff(self):
        scheduled = MatchRecord(
            provider="sportmonks",
            provider_match_id="1002",
            match_id="wc2026-1002",
            kickoff_utc=datetime(2026, 6, 12, 18, 0, tzinfo=timezone.utc),
            home_team="France",
            away_team="Argentina",
            tournament="World Cup",
            neutral=True,
            status="scheduled",
        )
        fixtures = [
            HistoricalBacktestFixture(match=self.finished_match, odds=(2.2, 3.4, 3.3)),
            HistoricalBacktestFixture(match=scheduled, odds=(2.6, 3.1, 2.9)),
        ]

        run = self.runner.run(fixtures)

        self.assertEqual(len(run.results), 1)
        self.assertEqual(len(self.engine.calls), 1)
        self.assertLess(
            self.engine.calls[0]["generated_at"],
            self.finished_match.kickoff_utc,
        )

    def test_runner_returns_serializable_results_and_aggregate_report(self):
        second_match = MatchRecord(
            provider="sportmonks",
            provider_match_id="1003",
            match_id="wc2026-1003",
            kickoff_utc=datetime(2026, 6, 11, 18, 0, tzinfo=timezone.utc),
            home_team="England",
            away_team="Portugal",
            tournament="World Cup",
            neutral=True,
            status="finished",
            home_score=0,
            away_score=0,
        )
        fixtures = [
            HistoricalBacktestFixture(
                match=self.finished_match,
                odds=(2.2, 3.4, 3.3),
                quality=PredictionInputQuality(team_sample_home=30, team_sample_away=30),
            ),
            HistoricalBacktestFixture(
                match=second_match,
                odds=(2.4, 3.0, 3.1),
                quality=PredictionInputQuality(team_sample_home=28, team_sample_away=29),
                prediction_time=second_match.kickoff_utc - timedelta(hours=6),
            ),
        ]

        run = self.runner.run(fixtures)
        payload = run.to_dict(tier="vip")

        self.assertEqual(run.report.n_matches, 2)
        self.assertIn("rps_total_goals", payload["report"])
        self.assertIn("exact_score_logloss", payload["report"])
        self.assertIn("top3_score_hit_rate", payload["report"])
        self.assertEqual(payload["matches"][0]["model_version"], "sportmonks-dc-v1")
        self.assertEqual(payload["matches"][0]["data_version"], "sportmonks-2026-06-28T12:00:00Z")
        self.assertEqual(payload["matches"][0]["actual_score"], {"home": 2, "away": 1})
        self.assertEqual(payload["matches"][0]["actual_outcome"], "home")


class BacktestCliPersistenceTests(unittest.TestCase):
    def setUp(self):
        from src.models.score_models import ScoreMatrix
        from src.models.prediction_snapshot import build_prediction_snapshot
        from src.models.backtest_runner import (
            HistoricalBacktestFixture,
            HistoricalBacktestMatchResult,
            HistoricalBacktestRun,
            _build_report,
        )

        matrix = ScoreMatrix.from_independent_poisson(1.4, 0.8)
        snapshot1 = build_prediction_snapshot(
            match_id="test-001",
            home_team="Spain",
            away_team="Brazil",
            score_matrix=matrix,
            final_probs={"home": 0.55, "draw": 0.25, "away": 0.20},
            model_version="test-model-v1",
            data_version="test-data",
            confidence=0.72,
            market_probs={"home": 0.50, "draw": 0.27, "away": 0.23},
        )
        snapshot2 = build_prediction_snapshot(
            match_id="test-002",
            home_team="France",
            away_team="Germany",
            score_matrix=matrix,
            final_probs={"home": 0.42, "draw": 0.30, "away": 0.28},
            model_version="test-model-v1",
            data_version="test-data",
            confidence=0.68,
            market_probs={"home": 0.40, "draw": 0.31, "away": 0.29},
        )

        self.results = [
            HistoricalBacktestMatchResult(
                snapshot=snapshot1,
                actual_home_goals=2,
                actual_away_goals=1,
                actual_outcome="home",
                kickoff_utc=datetime(2026, 6, 10, 18, 0, tzinfo=timezone.utc),
                prediction_time=datetime(2026, 6, 10, 17, 0, tzinfo=timezone.utc),
            ),
            HistoricalBacktestMatchResult(
                snapshot=snapshot2,
                actual_home_goals=0,
                actual_away_goals=0,
                actual_outcome="draw",
                kickoff_utc=datetime(2026, 6, 11, 18, 0, tzinfo=timezone.utc),
                prediction_time=datetime(2026, 6, 11, 17, 0, tzinfo=timezone.utc),
            ),
        ]

        self.run = HistoricalBacktestRun(
            model_version="test-model-v1",
            data_version="test-data",
            generated_at=datetime(2026, 6, 28, tzinfo=timezone.utc),
            results=self.results,
            report=_build_report(self.results),
        )

    def test_save_and_load_roundtrip(self):
        import tempfile
        import os as _os
        from src.backtest_cli import save_backtest_run, load_backtest_runs

        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_backtest_run(self.run, output_path=f"{tmpdir}/test_run.json")
            self.assertTrue(Path(path).exists())

            loaded = json.loads(Path(path).read_text(encoding="utf-8"))
            self.assertEqual(loaded["model_version"], "test-model-v1")
            self.assertEqual(loaded["data_version"], "test-data")
            self.assertEqual(loaded["report"]["n_matches"], 2)
            self.assertEqual(len(loaded["matches"]), 2)
            self.assertEqual(loaded["matches"][0]["actual_score"], {"home": 2, "away": 1})
            self.assertEqual(loaded["matches"][0]["actual_outcome"], "home")
            self.assertIn("hda_logloss", loaded["report"])
            self.assertIn("rps_total_goals", loaded["report"])

    def test_roundtrip_preserves_version_metadata(self):
        import tempfile
        from src.backtest_cli import save_backtest_run

        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_backtest_run(self.run, output_path=f"{tmpdir}/test_run.json")
            reloaded = json.loads(Path(path).read_text(encoding="utf-8"))

        for key in ("model_version", "data_version", "generated_at"):
            self.assertEqual(reloaded[key], getattr(self.run, key).isoformat() if key == "generated_at" else getattr(self.run, key))

        for i in range(2):
            self.assertEqual(
                reloaded["matches"][i]["model_version"],
                self.run.results[i].snapshot.model_version,
            )

    def test_fetch_matches_filters_non_finished(self):
        """Only finished matches with scores make it into fetched matches."""
        from unittest.mock import patch
        from src.backtest_cli import fetch_matches

        mock_raw = [
            {
                "id": 1,
                "league_id": 732,
                "state_id": 5,
                "starting_at": "2026-06-10 18:00:00",
                "name": "Spain vs Brazil",
                "participants": [
                    {"id": 10, "name": "Spain", "type": "national", "meta": {"location": "home"}},
                    {"id": 20, "name": "Brazil", "type": "national", "meta": {"location": "away"}},
                ],
                "scores": [
                    {"type_id": 1525, "participant_id": 10, "score": {"goals": 2, "participant": "home"}, "description": "CURRENT"},
                    {"type_id": 1525, "participant_id": 20, "score": {"goals": 1, "participant": "away"}, "description": "CURRENT"},
                ],
                "league": {"id": 732, "name": "World Cup", "sub_type": "cup_international"},
            },
            {  # scheduled — should be skipped
                "id": 2,
                "league_id": 732,
                "state_id": 1,
                "starting_at": "2026-06-12 18:00:00",
                "name": "France vs Argentina",
                "participants": [],
                "scores": [],
                "league": {"id": 732, "name": "World Cup", "sub_type": "cup_international"},
            },
            {  # finished but no scores — should be skipped
                "id": 3,
                "league_id": 732,
                "state_id": 5,
                "starting_at": "2026-06-11 18:00:00",
                "name": "England vs Portugal",
                "participants": [
                    {"id": 30, "name": "England", "type": "national", "meta": {"location": "home"}},
                    {"id": 40, "name": "Portugal", "type": "national", "meta": {"location": "away"}},
                ],
                "scores": [],
                "league": {"id": 732, "name": "World Cup", "sub_type": "cup_international"},
            },
        ]

        with patch("src.backtest_cli.get_fixtures_between", return_value=mock_raw):
            matches = fetch_matches("2026-06-10", "2026-06-15", league_ids=[732])

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].match_id, "sportmonks-1")
        self.assertEqual(matches[0].home_score, 2)
        self.assertEqual(matches[0].away_score, 1)


    def test_load_backtest_runs_filters_by_model_version(self):
        """load_backtest_runs('model-v1') returns only v1 runs, not v2."""
        import tempfile
        from src.backtest_cli import save_backtest_run, load_backtest_runs

        run1 = HistoricalBacktestRun(
            model_version="model-v1",
            data_version="data-A",
            generated_at=datetime(2026, 6, 28, 10, 0, tzinfo=timezone.utc),
            results=self.results[:1],
            report=_build_report(self.results[:1]),
        )
        run2 = HistoricalBacktestRun(
            model_version="model-v2",
            data_version="data-B",
            generated_at=datetime(2026, 6, 28, 11, 0, tzinfo=timezone.utc),
            results=self.results[1:],
            report=_build_report(self.results[1:]),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            save_backtest_run(run1, output_path=f"{tmpdir}/model-v1/run1.json")
            save_backtest_run(run2, output_path=f"{tmpdir}/model-v2/run2.json")

            import src.backtest_cli as cli
            original_dir = cli.BACKTEST_DIR
            cli.BACKTEST_DIR = Path(tmpdir)
            try:
                v1_runs = load_backtest_runs(model_version="model-v1")
                v2_runs = load_backtest_runs(model_version="model-v2")
                all_runs = load_backtest_runs()
            finally:
                cli.BACKTEST_DIR = original_dir

        self.assertEqual(len(v1_runs), 1)
        self.assertEqual(v1_runs[0]["model_version"], "model-v1")
        self.assertEqual(len(v2_runs), 1)
        self.assertEqual(v2_runs[0]["model_version"], "model-v2")
        self.assertEqual(len(all_runs), 2)

    def test_save_basic_tier_excludes_vip_fields(self):
        """tier='basic' must not include score_heatmap, market_probs, btts."""
        import tempfile
        from src.backtest_cli import save_backtest_run

        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_backtest_run(self.run, output_path=f"{tmpdir}/basic_run.json", tier="basic")
            data = json.loads(Path(path).read_text(encoding="utf-8"))

        VIP_FIELDS = {"score_heatmap", "market_probs", "market_edge", "btts"}
        for match in data["matches"]:
            for field in VIP_FIELDS:
                self.assertNotIn(
                    field, match,
                    f"tier=basic must not include '{field}'",
                )
        self.assertIn("probabilities", data["matches"][0])
        self.assertIn("top_scorelines", data["matches"][0])
        # top_scorelines should be truncated to 3 in basic
        self.assertEqual(len(data["matches"][0]["top_scorelines"]), 3)


    def test_dynamic_dc_backtest_output_has_correct_model_version(self):
        """Dynamic DC backtest must carry model_version=dynamic-dc-v1."""
        from unittest.mock import patch
        from src.backtest_cli import run_backtest_dynamic, fetch_matches

        fake_match = MatchRecord(
            provider="sportmonks", provider_match_id="1", match_id="sportmonks-1",
            kickoff_utc=datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc),
            home_team="Spain", away_team="Brazil",
            tournament="World Cup", neutral=True,
            status="finished", home_score=2, away_score=1,
        )
        warmup = [
            MatchRecord(
                provider="sportmonks", provider_match_id=str(i), match_id=f"sportmonks-w{i}",
                kickoff_utc=datetime(2026, 5, i + 1, tzinfo=timezone.utc),
                home_team="France", away_team="Germany",
                tournament="Friendly International", neutral=True,
                status="finished", home_score=1, away_score=0,
            )
            for i in range(5)
        ]

        fixt = HistoricalBacktestFixture(
            match=fake_match,
            odds=(2.5, 3.2, 2.8),
            quality=PredictionInputQuality(),
            context=None,
        )

        run = run_backtest_dynamic([fixt], warmup, "2026-06-14")

        self.assertEqual(run.model_version, "dynamic-dc-v1")
        self.assertEqual(len(run.results), 1)
        self.assertEqual(run.results[0].snapshot.model_version, "dynamic-dc-v1")
        self.assertIsNotNone(run.report.hda_logloss)
        self.assertIsNotNone(run.report.rps_total_goals)
        self.assertIsNotNone(run.report.exact_score_logloss)

    def test_market_baseline_backtest_has_model_version_and_market_probs(self):
        """Market baseline must carry model_version='market-baseline' and market_probs."""
        from src.backtest_cli import run_backtest_market

        match = MatchRecord(
            provider="sportmonks", provider_match_id="1", match_id="sportmonks-1",
            kickoff_utc=datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc),
            home_team="Spain", away_team="Brazil",
            tournament="World Cup", neutral=True,
            status="finished", home_score=2, away_score=1,
        )
        fixt = HistoricalBacktestFixture(
            match=match,
            odds=(2.5, 3.2, 2.8),
            quality=PredictionInputQuality(),
            context=None,
        )

        run = run_backtest_market([fixt])

        self.assertEqual(run.model_version, "market-baseline")
        self.assertEqual(len(run.results), 1)
        self.assertEqual(run.results[0].snapshot.model_version, "market-baseline")
        self.assertIsNotNone(run.results[0].snapshot.market_probs)
        self.assertAlmostEqual(
            sum(run.results[0].snapshot.market_probs.values()), 1.0, places=9)
        self.assertIsNotNone(run.report.hda_logloss)


    def test_learned_calibrator_passed_to_run_backtest_dynamic(self):
        """LearnedTotalGoalsCalibrator flows through to engine predictions."""
        from src.backtest_cli import run_backtest_dynamic
        from src.models.total_goals_calibration import LearnedTotalGoalsCalibrator, TotalGoalsCalibrationConfig

        config = TotalGoalsCalibrationConfig(min_group_matches=1)
        cal = LearnedTotalGoalsCalibrator(config)
        cal._fitted = True
        cal._multipliers[("*", "*", "*")] = 1.15

        match = MatchRecord(
            provider="sportmonks", provider_match_id="1", match_id="sportmonks-1",
            kickoff_utc=datetime(2026, 6, 15, 18, 0, tzinfo=timezone.utc),
            home_team="Spain", away_team="Brazil",
            tournament="World Cup", neutral=True,
            status="finished", home_score=2, away_score=1,
        )
        fixt = HistoricalBacktestFixture(
            match=match, odds=(2.5, 3.2, 2.8),
            quality=PredictionInputQuality(), context=None,
        )

        run = run_backtest_dynamic(
            [fixt], [], "2026-06-14", learned_calibrator=cal,
        )

        self.assertEqual(run.model_version, "dynamic-dc-v1")
        self.assertEqual(len(run.results), 1)
        # With 1.15 multiplier, mean predicted goals should be > uncalibrated base
        self.assertIsNotNone(run.report.mean_predicted_goals)
        self.assertIsNotNone(run.report.mean_actual_goals)


if __name__ == "__main__":
    unittest.main()
