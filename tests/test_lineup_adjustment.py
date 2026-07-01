import tests.warnings_filter  # noqa: F401 — suppress pickle warnings

import unittest

from src.data.provider_schema import MatchLineupSignal
from src.models.lineup_adjustment import (
    LineupAdjustmentConfig,
    LineupLambdaAdjustment,
    build_lineup_lambda_adjustment,
)


class LineupAdjustmentTests(unittest.TestCase):
    def setUp(self):
        self.empty_signal = MatchLineupSignal(
            match_id="test", team="A", data_quality="none",
        )
        self.injured_signal = MatchLineupSignal(
            match_id="test", team="A", data_quality="partial",
            unavailable_strength_loss=0.20,
            doubtful_strength_loss=0.05,
            missing_players=["Star Player"],
        )
        self.gk_signal = MatchLineupSignal(
            match_id="test", team="A", data_quality="partial",
            goalkeeper_missing=True,
            missing_players=["GK Star"],
        )
        self.attacker_signal = MatchLineupSignal(
            match_id="test", team="A", data_quality="partial",
            key_attacker_missing=True,
            missing_players=["Top Striker"],
        )
        self.defender_signal = MatchLineupSignal(
            match_id="test", team="A", data_quality="partial",
            key_defender_missing=True,
            missing_players=["Top CB"],
        )

    def test_empty_signals_produce_zero_adjustment(self):
        adj = build_lineup_lambda_adjustment(self.empty_signal, self.empty_signal)
        self.assertEqual(adj.home_attack_delta, 0.0)
        self.assertEqual(adj.away_defense_delta, 0.0)
        self.assertFalse(adj.has_signal())

    def test_injured_player_reduces_own_attack(self):
        adj = build_lineup_lambda_adjustment(self.injured_signal, self.empty_signal)
        self.assertLess(adj.home_attack_delta, 0.0)
        self.assertEqual(adj.away_attack_delta, 0.0)

    def test_gk_missing_adds_defense_penalty(self):
        adj = build_lineup_lambda_adjustment(self.gk_signal, self.empty_signal)
        self.assertGreater(adj.home_defense_delta, 0.0)

    def test_key_attacker_missing_adds_attack_penalty(self):
        adj = build_lineup_lambda_adjustment(self.attacker_signal, self.empty_signal)
        self.assertLess(adj.home_attack_delta, 0.0)

    def test_key_defender_missing_adds_defense_penalty(self):
        adj = build_lineup_lambda_adjustment(self.defender_signal, self.empty_signal)
        self.assertGreater(adj.home_defense_delta, 0.0)

    def test_confidence_bonus_for_confirmed_lineups(self):
        confirmed = MatchLineupSignal(
            match_id="test", team="A", data_quality="confirmed",
            has_confirmed_lineup=True, starting_xi_strength=0.7,
        )
        adj = build_lineup_lambda_adjustment(confirmed, self.empty_signal)
        self.assertGreater(adj.confidence_bonus, 0.0)

    def test_to_context_format(self):
        adj = LineupLambdaAdjustment(
            home_attack_delta=-0.05, home_defense_delta=0.03,
            away_attack_delta=-0.02, away_defense_delta=0.04,
        )
        ctx = adj.to_context()
        # home_lineup_delta = home_attack_delta + away_defense_delta
        self.assertAlmostEqual(ctx["home_lineup_delta"], -0.05 + 0.04)
        # away_lineup_delta = away_attack_delta + home_defense_delta
        self.assertAlmostEqual(ctx["away_lineup_delta"], -0.02 + 0.03)

    def test_total_delta_capped(self):
        config = LineupAdjustmentConfig(max_total_delta=0.05,
                                         max_attack_delta=1.0,
                                         max_defense_delta=1.0)
        heavy = MatchLineupSignal(
            match_id="test", team="A", data_quality="partial",
            unavailable_strength_loss=0.80, goalkeeper_missing=True,
            key_attacker_missing=True, key_defender_missing=True,
            missing_players=["A", "B", "C", "D"],
        )
        adj = build_lineup_lambda_adjustment(heavy, self.empty_signal, config=config)
        total = abs(adj.home_attack_delta) + abs(adj.home_defense_delta) + \
                abs(adj.away_attack_delta) + abs(adj.away_defense_delta)
        self.assertLessEqual(total, config.max_total_delta + 0.001)

    def test_context_does_not_change_prediction_matrix_sum(self):
        """Lineup context deltas should be small enough to keep score matrix valid."""
        from src.models.dynamic_dc import DynamicDixonColesConfig, DynamicDixonColesService

        class FakeDC:
            rho = -0.05
            def predict_lambdas(self, home, away, neutral=False):
                return 1.2, 0.9

        config = DynamicDixonColesConfig()
        svc = DynamicDixonColesService(FakeDC(), config=config)

        adj = LineupLambdaAdjustment(
            home_attack_delta=-0.08, home_defense_delta=0.06,
            away_attack_delta=-0.04, away_defense_delta=0.05,
        )
        matrix = svc.predict_score_matrix("A", "B", neutral=True, context=adj.to_context())
        self.assertAlmostEqual(matrix.matrix.sum(), 1.0, places=9)

    def test_nonzero_hda_probs(self):
        """Lambda adjustments must not zero out HDA probabilities."""
        from src.models.dynamic_dc import DynamicDixonColesConfig, DynamicDixonColesService

        class FakeDC:
            rho = -0.05
            def predict_lambdas(self, home, away, neutral=False):
                return 1.2, 0.9

        config = DynamicDixonColesConfig()
        svc = DynamicDixonColesService(FakeDC(), config=config)

        adj = LineupLambdaAdjustment(
            home_attack_delta=-0.10, home_defense_delta=0.10,
            away_attack_delta=0.10, away_defense_delta=-0.10,
        )
        matrix = svc.predict_score_matrix("A", "B", neutral=True, context=adj.to_context())
        hda = matrix.match_outcome_probs()
        for k in ("home", "draw", "away"):
            self.assertGreater(hda[k], 0.0)
        self.assertAlmostEqual(sum(hda.values()), 1.0, places=9)


if __name__ == "__main__":
    unittest.main()
