import tests.warnings_filter  # noqa: F401 — suppress pickle warnings
import unittest
from datetime import datetime, timedelta, timezone

import numpy as np

from src.models.score_models import ScoreMatrix
from src.models.market_fusion import (
    MarketFusionConfig,
    blend_with_market,
    confidence_from_inputs,
    normalize_three_way_odds,
)
from src.models.total_goals_calibration import TotalGoalsCalibrator
from src.models.dynamic_dc import DynamicDixonColesConfig, DynamicDixonColesService, TeamAdjustment
from src.models.prediction_snapshot import PredictionSnapshot, build_prediction_snapshot


class FakeDixonColes:
    def __init__(self):
        self.rho = -0.05
        self._team_attack = {"Spain": 0.2, "Brazil": 0.1, "France": 0.1, "Germany": 0.05, "Argentina": 0.0}
        self._team_defense = {"Spain": -0.1, "Brazil": 0.0, "France": 0.0, "Germany": 0.05, "Argentina": 0.0}

    def predict_lambdas(self, home_team, away_team, neutral=False):
        import math
        a_home = self._team_attack.get(home_team, 0.0)
        b_away = self._team_defense.get(away_team, 0.0)
        a_away = self._team_attack.get(away_team, 0.0)
        b_home = self._team_defense.get(home_team, 0.0)
        gamma = 0.2 if not neutral else 0.0
        lh = math.exp(gamma + a_home + b_away)
        la = math.exp(a_away + b_home)
        return max(lh, 0.02), max(la, 0.02)


class MarketFusionTests(unittest.TestCase):
    def test_normalize_three_way_odds_removes_vig_and_preserves_order(self):
        probs = normalize_three_way_odds(2.0, 3.5, 4.2)

        self.assertAlmostEqual(sum(probs.values()), 1.0, places=9)
        self.assertGreater(probs["home"], probs["draw"])
        self.assertGreater(probs["draw"], probs["away"])

    def test_blend_with_market_respects_confidence_extremes(self):
        model = {"home": 0.60, "draw": 0.20, "away": 0.20}
        market = {"home": 0.40, "draw": 0.30, "away": 0.30}

        self.assertEqual(blend_with_market(model, market, 0.0), market)
        self.assertEqual(blend_with_market(model, market, 1.0), model)

        blended = blend_with_market(model, market, 0.25)
        self.assertAlmostEqual(sum(blended.values()), 1.0, places=9)
        self.assertAlmostEqual(blended["home"], 0.45, places=9)

    def test_confidence_from_inputs_penalizes_missing_and_unstable_data(self):
        config = MarketFusionConfig()

        strong = confidence_from_inputs(
            team_sample_home=80,
            team_sample_away=75,
            has_lineups=True,
            has_injuries=True,
            odds_snapshots=4,
            odds_volatility=0.03,
            config=config,
        )
        weak = confidence_from_inputs(
            team_sample_home=8,
            team_sample_away=5,
            has_lineups=False,
            has_injuries=False,
            odds_snapshots=1,
            odds_volatility=0.30,
            config=config,
        )

        self.assertGreater(strong, weak)
        self.assertGreaterEqual(weak, config.min_confidence)
        self.assertLessEqual(strong, config.max_confidence)

    def test_de_vigged_probs_sum_to_one_with_extreme_odds(self):
        """Large underdog odds (50.0) must not break normalization."""
        probs = normalize_three_way_odds(1.15, 8.0, 50.0)
        self.assertAlmostEqual(sum(probs.values()), 1.0, places=9)
        self.assertGreater(probs["home"], probs["draw"])
        self.assertGreater(probs["draw"], probs["away"])

    def test_de_vigged_probs_sum_to_one_with_close_odds(self):
        """Near-even odds (all ~3.0) must normalize to sum=1."""
        probs = normalize_three_way_odds(2.8, 3.1, 2.9)
        self.assertAlmostEqual(sum(probs.values()), 1.0, places=9)
        # Home (lowest odds) should be highest probability
        self.assertGreater(probs["home"], probs["draw"])
        self.assertGreater(probs["home"], probs["away"])

    def test_de_vigged_probs_identical_odds(self):
        """All odds equal → all probs equal."""
        probs = normalize_three_way_odds(3.0, 3.0, 3.0)
        self.assertAlmostEqual(probs["home"], 1.0 / 3.0, places=5)
        self.assertAlmostEqual(probs["draw"], 1.0 / 3.0, places=5)
        self.assertAlmostEqual(probs["away"], 1.0 / 3.0, places=5)

    def test_average_bookmaker_probs_preserves_prob_sum(self):
        from src.models.market_fusion import average_bookmaker_probs

        prob_probs = average_bookmaker_probs([
            (2.0, 3.5, 4.2),
            (2.2, 3.3, 3.8),
            (1.9, 3.7, 4.5),
        ])
        self.assertIsNotNone(prob_probs)
        self.assertAlmostEqual(sum(prob_probs.values()), 1.0, places=9)

    def test_average_bookmaker_probs_avoids_decimal_average_skew(self):
        """
        Prob-average treats all bookmakers equally regardless of odds magnitude.

        With BK1 (2.0, 3.0, 5.0) and BK2 (1.5, 4.0, 12.0):
        Raw prob avg: H≈0.583 D≈0.292 A≈0.142 → de-vigged A≈0.139
        Decimal avg (wrong): H=1.75 D=3.5 A=8.5 → probs H≈0.487 D≈0.294 A≈0.219
        The 12.0 underdog DOES get down-weighted vs naive decimal averaging.
        """
        from src.models.market_fusion import average_bookmaker_probs

        result = average_bookmaker_probs([
            (2.0, 3.0, 5.0),
            (1.5, 4.0, 12.0),
        ])
        self.assertIsNotNone(result)
        self.assertAlmostEqual(sum(result.values()), 1.0, places=9)
        # Away prob from prob-avg ≈ 0.139, from decimal-avg would be ≈ 0.219
        self.assertLess(result["away"], 0.15,
                        "prob-average must down-weight extreme underdog vs decimal-avg")
        self.assertGreater(result["home"], result["away"])

    def test_average_bookmaker_probs_empty_returns_none(self):
        from src.models.market_fusion import average_bookmaker_probs
        self.assertIsNone(average_bookmaker_probs([]))

    def test_average_bookmaker_probs_skips_invalid_odds(self):
        """Bookmakers with odds <= 1.0 must be skipped without crashing."""
        from src.models.market_fusion import average_bookmaker_probs

        # 2 valid + 3 invalid → valid ones averaged, sum still 1.0
        result = average_bookmaker_probs([
            (2.0, 3.5, 4.0),
            (0.5, 3.0, 4.0),   # invalid (home <= 1.0)
            (2.1, 3.4, 3.9),
            (1.0, 3.0, 4.0),   # invalid (exactly 1.0)
            (-0.5, 3.0, 4.0),  # invalid (negative)
        ])
        self.assertIsNotNone(result)
        self.assertAlmostEqual(sum(result.values()), 1.0, places=9)

    def test_average_bookmaker_probs_all_invalid_returns_none(self):
        """When ALL bookmakers are invalid, return None (not division by zero)."""
        from src.models.market_fusion import average_bookmaker_probs

        result = average_bookmaker_probs([
            (0.5, 3.0, 4.0),
            (1.0, 3.0, 4.0),
        ])
        self.assertIsNone(result)

    def test_build_market_baseline_snapshot(self):
        from src.models.market_fusion import build_market_baseline_snapshot

        sm = ScoreMatrix.from_independent_poisson(1.4, 1.1)
        snap = build_market_baseline_snapshot(
            match_id="test",
            home_team="A",
            away_team="B",
            market_probs={"home": 0.45, "draw": 0.27, "away": 0.28},
            score_matrix=sm,
        )

        self.assertEqual(snap.model_version, "market-baseline")
        self.assertEqual(snap.final_probs, {"home": 0.45, "draw": 0.27, "away": 0.28})
        self.assertEqual(snap.market_probs, {"home": 0.45, "draw": 0.27, "away": 0.28})


class TotalGoalsCalibrationTests(unittest.TestCase):
    def test_goal_inflation_increases_expected_total_and_over_probability(self):
        matrix = ScoreMatrix.from_independent_poisson(1.0, 1.0)
        before_total = sum(matrix.expected_goals())
        before_over, _ = matrix.over_under_probability(2.5)

        calibrated = TotalGoalsCalibrator(lambda_multiplier=1.20).apply(matrix)
        after_total = sum(calibrated.expected_goals())
        after_over, _ = calibrated.over_under_probability(2.5)

        self.assertGreater(after_total, before_total)
        self.assertGreater(after_over, before_over)
        self.assertAlmostEqual(calibrated.matrix.sum(), 1.0, places=9)

    def test_multiplier_below_one_decreases_expected_goals(self):
        matrix = ScoreMatrix.from_independent_poisson(1.0, 1.0)
        before_total = sum(matrix.expected_goals())

        calibrated = TotalGoalsCalibrator(lambda_multiplier=0.85).apply(matrix)
        after_total = sum(calibrated.expected_goals())

        self.assertLess(after_total, before_total)
        self.assertAlmostEqual(calibrated.matrix.sum(), 1.0, places=9)

    def test_multiplier_one_is_noop(self):
        matrix = ScoreMatrix.from_independent_poisson(1.0, 1.0)
        calibrated = TotalGoalsCalibrator(lambda_multiplier=1.0).apply(matrix)
        self.assertTrue(np.allclose(matrix.matrix, calibrated.matrix))

    def test_hda_probs_consistent_after_calibration(self):
        """HDA probs recomputed from calibrated matrix must still be valid."""
        matrix = ScoreMatrix.from_independent_poisson(1.4, 1.1)
        orig_hda = matrix.match_outcome_probs()

        calibrated = TotalGoalsCalibrator(lambda_multiplier=1.20).apply(matrix)
        cal_hda = calibrated.match_outcome_probs()

        # HDA still sums to 1
        self.assertAlmostEqual(sum(cal_hda.values()), 1.0, places=9)
        # Order is roughly preserved (home still favorite)
        self.assertGreater(cal_hda["home"], cal_hda["away"])

    def test_learned_calibrator_fallback_to_default(self):
        from src.data.provider_schema import MatchRecord
        from src.models.total_goals_calibration import (
            LearnedTotalGoalsCalibrator,
            TotalGoalsCalibrationConfig,
        )

        config = TotalGoalsCalibrationConfig(
            default_multiplier=1.05,
            min_group_matches=50,  # very high → no group meets threshold
        )
        cal = LearnedTotalGoalsCalibrator(config)
        cal._fitted = True

        match = MatchRecord(
            provider="t", provider_match_id="1", match_id="1",
            kickoff_utc=datetime(2026, 6, 10, tzinfo=timezone.utc),
            home_team="A", away_team="B",
            tournament="World Cup", neutral=True, status="finished",
            home_score=2, away_score=1,
        )
        self.assertEqual(cal.multiplier_for(match), 1.05)

    def test_learned_calibrator_fit_estimates_multiplier(self):
        from src.data.provider_schema import MatchRecord
        from src.models.total_goals_calibration import (
            LearnedTotalGoalsCalibrator,
            TotalGoalsCalibrationConfig,
        )

        config = TotalGoalsCalibrationConfig(min_group_matches=2)
        cal = LearnedTotalGoalsCalibrator(config)

        # Create matches where actual goals > predicted → multiplier > 1
        matches = [
            MatchRecord(
                provider="t", provider_match_id=str(i), match_id=str(i),
                kickoff_utc=datetime(2026, 6, i + 1, tzinfo=timezone.utc),
                home_team="A", away_team="B",
                tournament="World Cup", neutral=True, status="finished",
                home_score=2, away_score=1,  # total=3
            )
            for i in range(5)
        ]

        def predict_fn(m):
            return ScoreMatrix.from_independent_poisson(0.8, 0.7)  # total≈1.5

        cal.fit(matches, predict_fn)
        m = cal.multiplier_for(matches[0])
        # actual=3, pred≈1.5 → ratio≈2.0 clipped to max_multiplier=1.30
        self.assertGreater(m, 1.0)
        self.assertLessEqual(m, config.max_multiplier)

    def test_compute_goal_calibration_metrics(self):
        from src.models.backtest_runner import HistoricalBacktestMatchResult
        from src.models.total_goals_calibration import compute_goal_calibration_metrics
        from src.models.prediction_snapshot import build_prediction_snapshot

        sm = ScoreMatrix.from_independent_poisson(1.4, 1.1)
        snap = build_prediction_snapshot(
            match_id="t1", home_team="A", away_team="B",
            score_matrix=sm,
            final_probs=sm.match_outcome_probs(),
            model_version="test", data_version="test",
            confidence=0.7,
        )

        results = [
            HistoricalBacktestMatchResult(
                snapshot=snap,
                actual_home_goals=2, actual_away_goals=1,
                actual_outcome="home",
                kickoff_utc=datetime(2026, 6, 10, tzinfo=timezone.utc),
                prediction_time=datetime(2026, 6, 10, 17, 0, tzinfo=timezone.utc),
            ),
            HistoricalBacktestMatchResult(
                snapshot=snap,
                actual_home_goals=0, actual_away_goals=0,
                actual_outcome="draw",
                kickoff_utc=datetime(2026, 6, 11, tzinfo=timezone.utc),
                prediction_time=datetime(2026, 6, 11, 17, 0, tzinfo=timezone.utc),
            ),
        ]

        metrics = compute_goal_calibration_metrics(results)
        self.assertIsNotNone(metrics.mean_predicted_total)
        self.assertIsNotNone(metrics.mean_actual_total)
        self.assertIsNotNone(metrics.over25_ece)
        self.assertIsNotNone(metrics.btts_ece)

    def test_learn_global_multiplier_below_default_with_strong_model(self):
        """When predictions match actuals closely, multiplier stays near 1.0."""
        from src.data.provider_schema import MatchRecord
        from src.models.total_goals_calibration import (
            TotalGoalsCalibrationConfig,
            learn_global_multiplier,
        )

        matches = [
            MatchRecord(
                provider="t", provider_match_id=str(i + 1), match_id=str(i + 1),
                kickoff_utc=datetime(2026, 6, i + 1, tzinfo=timezone.utc),
                home_team="A", away_team="B",
                tournament="World Cup", neutral=True, status="finished",
                home_score=1, away_score=1,  # total=2
            )
            for i in range(20)
        ]

        def predict_fn(m):
            return ScoreMatrix.from_independent_poisson(1.0, 1.0)  # total≈2.0

        mlt = learn_global_multiplier(matches, predict_fn)
        # actual≈2.0, pred≈2.0 → multiplier≈1.0
        self.assertAlmostEqual(mlt, 1.0, places=1)

    def test_learn_global_multiplier_above_one_when_model_underpredicts(self):
        """When model systematically underpredicts, multiplier > 1.0."""
        from src.data.provider_schema import MatchRecord
        from src.models.total_goals_calibration import (
            TotalGoalsCalibrationConfig,
            learn_global_multiplier,
        )

        config = TotalGoalsCalibrationConfig(min_multiplier=0.5, max_multiplier=2.0)
        matches = [
            MatchRecord(
                provider="t", provider_match_id=str(i + 1), match_id=str(i + 1),
                kickoff_utc=datetime(2026, 6, i + 1, tzinfo=timezone.utc),
                home_team="A", away_team="B",
                tournament="World Cup", neutral=True, status="finished",
                home_score=1, away_score=2,  # total=3
            )
            for i in range(20)
        ]

        def predict_fn(m):
            return ScoreMatrix.from_independent_poisson(0.5, 0.5)  # total≈1.0

        mlt = learn_global_multiplier(matches, predict_fn, config)
        # actual=3, pred≈1 → ratio≈3 clipped to max 2.0
        self.assertGreater(mlt, 1.0)

    def test_learn_global_multiplier_too_few_matches_returns_default(self):
        from src.data.provider_schema import MatchRecord
        from src.models.total_goals_calibration import (
            TotalGoalsCalibrationConfig,
            learn_global_multiplier,
        )

        config = TotalGoalsCalibrationConfig(default_multiplier=1.05)
        matches = [
            MatchRecord(
                provider="t", provider_match_id="1", match_id="1",
                kickoff_utc=datetime(2026, 6, 10, tzinfo=timezone.utc),
                home_team="A", away_team="B",
                tournament="World Cup", neutral=True, status="finished",
                home_score=2, away_score=1,
            )
        ]  # only 1 match, < 5

        def predict_fn(m):
            return ScoreMatrix.from_independent_poisson(1.0, 1.0)

        mlt = learn_global_multiplier(matches, predict_fn, config)
        self.assertEqual(mlt, 1.05)


class DynamicDixonColesTests(unittest.TestCase):
    def test_online_update_caps_daily_team_adjustment(self):
        config = DynamicDixonColesConfig(max_daily_delta=0.03, smoothing=0.0, learning_rate=0.015)
        service = DynamicDixonColesService(FakeDixonColes(), config=config)

        service.update_from_result("Spain", "Brazil", 5, 0, match_weight=5.0)
        service.update_from_result("Spain", "Brazil", 4, 0, match_weight=5.0)

        self.assertLessEqual(abs(service.team_adjustments["Spain"].attack), 0.03)
        self.assertLessEqual(abs(service.team_adjustments["Brazil"].defense), 0.03)

    def test_predict_score_matrix_applies_context_adjustments(self):
        config = DynamicDixonColesConfig(max_daily_delta=0.03, smoothing=0.0)
        service = DynamicDixonColesService(FakeDixonColes(), config=config)
        baseline = service.predict_score_matrix("Spain", "Brazil", neutral=True)

        adjusted = service.predict_score_matrix(
            "Spain",
            "Brazil",
            neutral=True,
            context={"home_attack_delta": 0.10, "away_defense_delta": 0.05},
        )

        self.assertGreater(adjusted.expected_goals()[0], baseline.expected_goals()[0])
        self.assertAlmostEqual(adjusted.matrix.sum(), 1.0, places=9)

    def test_score_matrix_probability_sums_to_one_after_update(self):
        """Score matrix must always sum to 1.0 regardless of adjustments."""
        config = DynamicDixonColesConfig(max_daily_delta=0.05, smoothing=0.0)
        service = DynamicDixonColesService(FakeDixonColes(), config=config)

        # Apply several updates
        for _ in range(5):
            service.update_from_result("Spain", "Brazil", 3, 1, match_weight=2.0)
            service.update_from_result("Brazil", "Argentina", 2, 2, match_weight=2.0)

        matrix = service.predict_score_matrix("Spain", "Brazil", neutral=True)
        self.assertAlmostEqual(matrix.matrix.sum(), 1.0, places=9)

        matrix2 = service.predict_score_matrix("Brazil", "Argentina", neutral=False)
        self.assertAlmostEqual(matrix2.matrix.sum(), 1.0, places=9)

    def test_time_decay_recent_weighted_more_than_old(self):
        """Matches closer to as_of_date should get higher weight."""
        from src.models.dynamic_dc import _time_decay

        now = datetime(2026, 6, 28, tzinfo=timezone.utc)
        recent = now - timedelta(days=30)
        old = now - timedelta(days=1000)

        w_recent = _time_decay(recent, now, half_life_days=365 * 2)
        w_old = _time_decay(old, now, half_life_days=365 * 2)

        self.assertGreater(w_recent, w_old)
        self.assertGreater(w_recent, 0.9)   # 30 days old → nearly 1.0
        self.assertLess(w_old, 0.5)          # 1000 days → less than half

    def test_tournament_weight_world_cup_higher_than_friendly(self):
        """World Cup matches should carry more weight than friendlies."""
        from src.models.dynamic_dc import _tournament_weight, DynamicDixonColesConfig

        config = DynamicDixonColesConfig()
        w_wc = _tournament_weight("World Cup", config)
        w_friendly = _tournament_weight("Friendly International", config)

        self.assertGreater(w_wc, w_friendly)
        self.assertEqual(w_wc, 5.0)
        self.assertEqual(w_friendly, 0.5)

    def test_tournament_weight_fuzzy_match(self):
        from src.models.dynamic_dc import _tournament_weight, DynamicDixonColesConfig
        config = DynamicDixonColesConfig()

        self.assertAlmostEqual(_tournament_weight("WC Qualification Europe", config), 2.5)
        self.assertAlmostEqual(_tournament_weight("World Cup Qualification", config), 2.5)
        # Unknown tournament — no keyword match, falls back to 1.0
        self.assertAlmostEqual(_tournament_weight("International Tournament", config), 1.0)

    def test_neutral_venue_symmetry(self):
        """Swapping home/away at neutral venue should give symmetric expected goals."""
        config = DynamicDixonColesConfig()
        service = DynamicDixonColesService(FakeDixonColes(), config=config)

        # Predict two UNRELATED teams with no adjustments → must be symmetric
        goals_ab = service.predict_score_matrix("France", "Germany", neutral=True).expected_goals()
        goals_ba = service.predict_score_matrix("Germany", "France", neutral=True).expected_goals()

        # At neutral venue, swapping home/away should swap expected goals
        self.assertAlmostEqual(goals_ab[0], goals_ba[1], places=4)
        self.assertAlmostEqual(goals_ab[1], goals_ba[0], places=4)

    def test_update_from_match_respects_as_of_date(self):
        """Future matches must NOT be applied."""
        from src.data.provider_schema import MatchRecord

        config = DynamicDixonColesConfig()
        service = DynamicDixonColesService(FakeDixonColes(), config=config)
        lambdas_before = service.predict_lambdas("Spain", "Brazil", neutral=True)

        future_match = MatchRecord(
            provider="test", provider_match_id="1", match_id="t1",
            kickoff_utc=datetime(2030, 1, 1, tzinfo=timezone.utc),
            home_team="Spain", away_team="Brazil",
            tournament="World Cup", neutral=True,
            status="finished", home_score=5, away_score=0,
        )
        applied = service.update_from_match(future_match, as_of_date=datetime(2026, 6, 28, tzinfo=timezone.utc))
        self.assertFalse(applied)

        lambdas_after = service.predict_lambdas("Spain", "Brazil", neutral=True)
        self.assertEqual(lambdas_before, lambdas_after)

    def test_update_from_match_applies_past_match(self):
        """Past match with scores changes lambdas for the teams involved."""
        from src.data.provider_schema import MatchRecord

        config = DynamicDixonColesConfig(smoothing=0.0)
        service = DynamicDixonColesService(FakeDixonColes(), config=config)

        # Predict Spain vs Argentina BEFORE update
        lambdas_before = service.predict_lambdas("Spain", "Argentina", neutral=True)

        # Update Spain vs Brazil 5-0 → Spain attack boosted, Brazil defense penalized
        past_match = MatchRecord(
            provider="test", provider_match_id="1", match_id="t1",
            kickoff_utc=datetime(2026, 6, 10, tzinfo=timezone.utc),
            home_team="Spain", away_team="Brazil",
            tournament="World Cup", neutral=True,
            status="finished", home_score=5, away_score=0,
        )
        applied = service.update_from_match(past_match, as_of_date=datetime(2026, 6, 28, tzinfo=timezone.utc))
        self.assertTrue(applied)

        # Spain vs Argentina: Spain's boosted attack is NOT canceled by Argentina's defense
        lambdas_after = service.predict_lambdas("Spain", "Argentina", neutral=True)
        self.assertNotEqual(lambdas_before, lambdas_after)

    def test_update_from_match_handles_naive_kickoff_with_aware_as_of_date(self):
        """Naive kickoff + aware as_of_date must not raise TypeError."""
        from src.data.provider_schema import MatchRecord

        config = DynamicDixonColesConfig()
        service = DynamicDixonColesService(FakeDixonColes(), config=config)

        # naive kickoff (no tzinfo)
        match = MatchRecord(
            provider="test", provider_match_id="1", match_id="t1",
            kickoff_utc=datetime(2026, 6, 10, 10, 0),  # naive
            home_team="Spain", away_team="Brazil",
            tournament="World Cup", neutral=True,
            status="finished", home_score=2, away_score=0,
        )
        # aware as_of_date
        as_of = datetime(2026, 6, 28, tzinfo=timezone.utc)

        # Must not raise
        applied = service.update_from_match(match, as_of_date=as_of)
        self.assertTrue(applied)

    def test_update_from_matches_batch_applies_in_order(self):
        from src.data.provider_schema import MatchRecord

        config = DynamicDixonColesConfig(smoothing=0.0)
        service = DynamicDixonColesService(FakeDixonColes(), config=config)

        matches = [
            MatchRecord(
                provider="test", provider_match_id=str(i), match_id=f"t{i}",
                kickoff_utc=datetime(2026, 6, i + 1, tzinfo=timezone.utc),
                home_team="Spain", away_team="Brazil",
                tournament="World Cup", neutral=True,
                status="finished", home_score=2, away_score=0,
            )
            for i in range(1, 6)
        ]

        n = service.update_from_matches(matches, as_of_date=datetime(2026, 6, 28, tzinfo=timezone.utc))
        self.assertEqual(n, 5)

    def test_smoothing_keeps_params_closer_to_base(self):
        """Smoothing dampens single-update spikes vs raw cap-hit.

        With smoothing=0.0: first update hits daily cap (+0.03), second update
        resets to 0 (cap exhausted, clipped=0, smoothing*old=0 → 0).
        With smoothing=0.9: first update adds 0.003, second update decays to
        0.0027 (0.9*0.003), then accumulates again. After 10 updates, the
        smoothed version has non-zero persistent adjustment while the unsmoothed
        oscillates between cap and zero.
        """
        config_lo = DynamicDixonColesConfig(smoothing=0.0, max_daily_delta=0.03, learning_rate=0.015)
        config_hi = DynamicDixonColesConfig(smoothing=0.9, max_daily_delta=0.03, learning_rate=0.015)

        svc_lo = DynamicDixonColesService(FakeDixonColes(), config=config_lo)
        svc_hi = DynamicDixonColesService(FakeDixonColes(), config=config_hi)

        # Single update — smoothing should produce smaller initial jump
        svc_lo.reset_daily_limits()
        svc_hi.reset_daily_limits()
        svc_lo.update_from_result("Spain", "France", 5, 0, match_weight=1.0)
        svc_hi.update_from_result("Spain", "France", 5, 0, match_weight=1.0)

        lo_first = abs(svc_lo.team_adjustments.get("Spain", TeamAdjustment()).attack)
        hi_first = abs(svc_hi.team_adjustments.get("Spain", TeamAdjustment()).attack)

        # Smoothing=0.9 should reduce the first-update jump vs no smoothing
        self.assertLess(hi_first, lo_first)


class PredictionSnapshotTests(unittest.TestCase):
    def test_prediction_snapshot_keeps_versions_and_tiered_visibility(self):
        score_matrix = ScoreMatrix.from_independent_poisson(1.4, 1.1)
        snapshot = build_prediction_snapshot(
            match_id="wc2026-001",
            home_team="Spain",
            away_team="Brazil",
            score_matrix=score_matrix,
            final_probs={"home": 0.45, "draw": 0.27, "away": 0.28},
            model_version="dynamic-dc-v1",
            data_version="api-football-2026-06-28T10:00:00Z",
            confidence=0.72,
            generated_at=datetime(2026, 6, 28, tzinfo=timezone.utc),
            market_probs={"home": 0.42, "draw": 0.29, "away": 0.29},
        )

        self.assertIsInstance(snapshot, PredictionSnapshot)
        basic = snapshot.to_public_dict(tier="basic")
        vip = snapshot.to_public_dict(tier="vip")

        self.assertEqual(basic["model_version"], "dynamic-dc-v1")
        self.assertEqual(basic["data_version"], "api-football-2026-06-28T10:00:00Z")
        self.assertIn("top_scorelines", basic)
        self.assertNotIn("score_heatmap", basic)
        self.assertIn("score_heatmap", vip)
        self.assertIn("market_probs", vip)


if __name__ == "__main__":
    unittest.main()
