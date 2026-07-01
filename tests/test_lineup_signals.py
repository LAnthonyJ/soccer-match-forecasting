import tests.warnings_filter  # noqa: F401 — suppress pickle warnings

import unittest
from datetime import datetime, timezone

from src.data.provider_schema import (
    InjuryRecord,
    LineupPlayerRecord,
    MatchLineupSignal,
    MatchRecord,
    PlayerStrengthRecord,
)
from src.features.lineup_signals import build_lineup_signals, compute_importance_score


class ImportanceScoreTests(unittest.TestCase):
    def test_market_value_drives_score(self):
        p = PlayerStrengthRecord(player_id="1", player_name="Star", team="A",
                                 market_value_eur=100_000_000)
        score = compute_importance_score(p)
        self.assertAlmostEqual(score, 1.0, places=1)

    def test_recent_minutes_drives_score(self):
        p = PlayerStrengthRecord(player_id="2", player_name="Regular", team="A",
                                 recent_minutes=2000)
        score = compute_importance_score(p)
        self.assertAlmostEqual(score, 1.0, places=1)

    def test_caps_and_goals_fallback(self):
        p = PlayerStrengthRecord(player_id="3", player_name="Veteran", team="A",
                                 caps=80, goals=20, position="FW")
        score = compute_importance_score(p)
        self.assertGreater(score, 0.5)

    def test_min_importance_nonzero(self):
        p = PlayerStrengthRecord(player_id="4", player_name="Unknown", team="A")
        score = compute_importance_score(p)
        self.assertGreater(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_explicit_importance_overrides(self):
        p = PlayerStrengthRecord(player_id="5", player_name="Custom", team="A",
                                 importance_score=0.75)
        self.assertEqual(compute_importance_score(p), 0.75)


class LineupSignalsTests(unittest.TestCase):
    def setUp(self):
        self.match = MatchRecord(
            provider="test", provider_match_id="1", match_id="test-1",
            kickoff_utc=datetime(2026, 6, 28, tzinfo=timezone.utc),
            home_team="Spain", away_team="Brazil",
            tournament="World Cup", neutral=True,
            status="scheduled",
        )

    def test_no_data_returns_empty_signals(self):
        signals = build_lineup_signals(self.match)
        for side in ("home", "away"):
            s = signals[side]
            self.assertEqual(s.data_quality, "none")
            self.assertFalse(s.has_confirmed_lineup)
            self.assertIsNone(s.starting_xi_strength)
            self.assertIsNone(s.unavailable_strength_loss)

    def test_injuries_create_unavailable_signal(self):
        injuries = [
            InjuryRecord(provider="test", match_id="test-1", team="Spain",
                         player_name="Star Forward", status="injured", severity="high"),
        ]
        signals = build_lineup_signals(self.match, injuries=injuries)

        self.assertEqual(signals["home"].data_quality, "partial")
        self.assertGreater(signals["home"].unavailable_strength_loss, 0.0)
        self.assertIn("Star Forward", signals["home"].missing_players)

    def test_doubtful_player_weighs_less(self):
        injuries = [
            InjuryRecord(provider="test", match_id="test-1", team="Spain",
                         player_name="Mid Player", status="doubtful", severity="low"),
        ]
        signals = build_lineup_signals(self.match, injuries=injuries)
        self.assertGreater(signals["home"].doubtful_strength_loss or 0.0, 0.0)

    def test_starting_xi_with_strengths(self):
        lineups = {
            "Spain": [
                LineupPlayerRecord(provider="test", match_id="test-1", team="Spain",
                                   player_name=f"Player{i}", is_starting=True, position="MF")
                for i in range(11)
            ],
        }
        strengths = [
            PlayerStrengthRecord(player_id=str(i), player_name=f"Player{i}",
                                 team="Spain", market_value_eur=30_000_000)
            for i in range(11)
        ]
        signals = build_lineup_signals(self.match, lineups=lineups, player_strengths=strengths)
        self.assertEqual(signals["home"].data_quality, "confirmed")
        self.assertIsNotNone(signals["home"].starting_xi_strength)
        self.assertGreater(signals["home"].starting_xi_strength or 0, 0.0)

    def test_goalkeeper_missing_detected(self):
        injuries = [
            InjuryRecord(provider="test", match_id="test-1", team="Brazil",
                         player_name="GK Star", status="injured", severity="high"),
        ]
        strengths = [
            PlayerStrengthRecord(player_id="g1", player_name="GK Star", team="Brazil",
                                 position="GK", importance_score=0.9),
        ]
        signals = build_lineup_signals(self.match, injuries=injuries, player_strengths=strengths)
        self.assertTrue(signals["away"].goalkeeper_missing)

    def test_key_attacker_missing_detected(self):
        injuries = [
            InjuryRecord(provider="test", match_id="test-1", team="Spain",
                         player_name="Top Striker", status="injured", severity="high"),
        ]
        strengths = [
            PlayerStrengthRecord(player_id="a1", player_name="Top Striker", team="Spain",
                                 position="FW", importance_score=0.95),
        ]
        signals = build_lineup_signals(self.match, injuries=injuries, player_strengths=strengths)
        self.assertTrue(signals["home"].key_attacker_missing)

    def test_player_in_lineup_not_counted_as_missing(self):
        lineups = {
            "Spain": [
                LineupPlayerRecord(provider="test", match_id="test-1", team="Spain",
                                   player_name="Dual Player", is_starting=True, position="MF"),
            ],
        }
        injuries = [
            InjuryRecord(provider="test", match_id="test-1", team="Spain",
                         player_name="Dual Player", status="injured"),
        ]
        signals = build_lineup_signals(self.match, lineups=lineups, injuries=injuries)
        self.assertNotIn("Dual Player", signals["home"].missing_players)

    def test_expected_lineup_not_confirmed(self):
        lineups = {
            "Spain": [
                LineupPlayerRecord(provider="test", match_id="test-1", team="Spain",
                                   player_name="P1", is_starting=True, position="MF"),
            ],
        }
        signals = build_lineup_signals(self.match, lineups=lineups, lineup_type="expected")
        self.assertFalse(signals["home"].has_confirmed_lineup)
        self.assertTrue(signals["home"].has_expected_lineup)
        self.assertEqual(signals["home"].data_quality, "expected")

    def test_confirmed_lineup_type(self):
        lineups = {
            "Spain": [
                LineupPlayerRecord(provider="test", match_id="test-1", team="Spain",
                                   player_name="P1", is_starting=True, position="MF"),
            ],
        }
        signals = build_lineup_signals(self.match, lineups=lineups, lineup_type="confirmed")
        self.assertTrue(signals["home"].has_confirmed_lineup)
        self.assertFalse(signals["home"].has_expected_lineup)
        self.assertEqual(signals["home"].data_quality, "confirmed")

    def test_away_team_signals_independent(self):
        injuries_home = [
            InjuryRecord(provider="test", match_id="test-1", team="Spain",
                         player_name="Home Star", status="injured"),
        ]
        signals = build_lineup_signals(self.match, injuries=injuries_home)
        self.assertEqual(signals["home"].data_quality, "partial")
        self.assertEqual(signals["away"].data_quality, "none")


if __name__ == "__main__":
    unittest.main()
