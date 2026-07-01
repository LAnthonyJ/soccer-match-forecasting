"""Tests for Sportmonks adapter and ingestion pipeline."""
import tests.warnings_filter  # noqa: F401 — suppress pickle warnings

import unittest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from src.data.provider_schema import (
    MatchRecord,
    OddsSnapshot,
    LineupPlayerRecord,
    InjuryRecord,
)
from src.data.sportmonks_adapter import (
    SportmonksAdapter,
    normalize_team_name,
    _extract_final_score,
    _find_participant,
)
from src.data.sportmonks_ingestion import SportmonksIngestion, _build_match_context


# -- Sample fixtures ------------------------------------------------------------

SAMPLE_FIXTURE = {
    "id": 19609197,
    "sport_id": 1,
    "league_id": 732,
    "season_id": 20001,
    "stage_id": 12345,
    "group_id": None,
    "aggregate_id": None,
    "round_id": None,
    "state_id": 5,
    "venue_id": 456,
    "name": "Algeria vs Austria",
    "starting_at": "2026-06-28 02:00:00",
    "result_info": "Game ended in draw.",
    "leg": "1/1",
    "length": 90,
    "placeholder": False,
    "has_odds": True,
    "has_premium_odds": True,
    "starting_at_timestamp": 1780272000,
    "participants": [
        {
            "id": 18555,
            "name": "Algeria",
            "short_code": "ALG",
            "type": "national",
            "country_id": 614,
            "meta": {"location": "home", "winner": False},
        },
        {
            "id": 18556,
            "name": "Austria",
            "short_code": "AUT",
            "type": "national",
            "country_id": 143,
            "meta": {"location": "away", "winner": False},
        },
    ],
    "scores": [
        {"id": 1, "fixture_id": 19609197, "type_id": 1, "participant_id": 18555, "score": {"goals": 1, "participant": "home"}, "description": "1ST_HALF"},
        {"id": 2, "fixture_id": 19609197, "type_id": 1, "participant_id": 18556, "score": {"goals": 1, "participant": "away"}, "description": "1ST_HALF"},
        {"id": 3, "fixture_id": 19609197, "type_id": 1525, "participant_id": 18555, "score": {"goals": 3, "participant": "home"}, "description": "CURRENT"},
        {"id": 4, "fixture_id": 19609197, "type_id": 1525, "participant_id": 18556, "score": {"goals": 3, "participant": "away"}, "description": "CURRENT"},
    ],
    "league": {"id": 732, "name": "World Cup"},
}

SAMPLE_ODDS_DATA = {
    "match_id": 19609197,
    "bookmaker_id": 2,
    "home": 3.50,
    "draw": 2.10,
    "away": 3.60,
}

SAMPLE_SQUAD_ENTRY = {
    "player_id": 37603157,
    "team_id": 18554,
    "position_id": 27,
    "detailed_position_id": 151,
    "jersey_number": 9,
    "captain": False,
    "_player": {"display_name": "Hazem Mastouri", "name": "Hazem Mastouri"},
    "_team_name": "Tunisia",
}


class SportmonksAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = SportmonksAdapter()

    def test_parse_match_extracts_fields_correctly(self):
        match = self.adapter.parse_match(SAMPLE_FIXTURE)

        self.assertIsInstance(match, MatchRecord)
        self.assertEqual(match.provider, "sportmonks")
        self.assertEqual(match.home_team, "Algeria")
        self.assertEqual(match.away_team, "Austria")
        self.assertEqual(match.tournament, "World Cup")
        self.assertEqual(match.status, "finished")
        self.assertEqual(match.home_score, 3)
        self.assertEqual(match.away_score, 3)
        self.assertTrue(match.neutral)

    def test_parse_match_scheduled_status(self):
        fixture = dict(SAMPLE_FIXTURE, state_id=1, scores=[])
        match = self.adapter.parse_match(fixture)
        self.assertEqual(match.status, "scheduled")
        self.assertIsNone(match.home_score)

    def test_parse_odds_snapshot_converts_properly(self):
        snap = self.adapter.parse_odds_snapshot(SAMPLE_ODDS_DATA)

        self.assertIsInstance(snap, OddsSnapshot)
        self.assertEqual(snap.provider, "sportmonks")
        self.assertEqual(snap.home_odds, 3.50)
        self.assertEqual(snap.draw_odds, 2.10)
        self.assertEqual(snap.away_odds, 3.60)
        self.assertEqual(snap.bookmaker, "2")
        self.assertEqual(snap.label, "latest")

    def test_parse_lineup_player_maps_position(self):
        player = self.adapter.parse_lineup_player(SAMPLE_SQUAD_ENTRY)

        self.assertIsInstance(player, LineupPlayerRecord)
        self.assertEqual(player.player_name, "Hazem Mastouri")
        self.assertEqual(player.team, "Tunisia")
        self.assertEqual(player.position, "MF")
        self.assertTrue(player.is_starting)

    def test_parse_injury_basic(self):
        injury_data = {
            "player_id": 12345,
            "_player_name": "Test Player",
            "_team_name": "Test Team",
            "_match_id": "test-001",
            "status": "out",
            "severity": "high",
        }
        injury = self.adapter.parse_injury(injury_data)

        self.assertIsInstance(injury, InjuryRecord)
        self.assertEqual(injury.player_name, "Test Player")
        self.assertEqual(injury.status, "out")
        self.assertEqual(injury.severity, "high")

    def test_normalize_team_name(self):
        self.assertEqual(normalize_team_name("Congo DR"), "DR Congo")
        self.assertEqual(normalize_team_name("Türkiye"), "Turkey")
        self.assertEqual(normalize_team_name("Brazil"), "Brazil")  # unchanged

    def test_extract_final_score(self):
        home, away = _extract_final_score(SAMPLE_FIXTURE["scores"])
        self.assertEqual(home, 3)
        self.assertEqual(away, 3)

    def test_extract_final_score_empty(self):
        home, away = _extract_final_score([])
        self.assertIsNone(home)
        self.assertIsNone(away)

    def test_find_participant(self):
        home = _find_participant(SAMPLE_FIXTURE["participants"], "home")
        away = _find_participant(SAMPLE_FIXTURE["participants"], "away")
        self.assertEqual(home["name"], "Algeria")
        self.assertEqual(away["name"], "Austria")


class IngestionPipelineTests(unittest.TestCase):
    def test_build_match_context_empty_squads(self):
        ctx = _build_match_context({}, [], [], None, None)
        self.assertEqual(ctx, {})

    def test_build_match_context_squad_imbalance(self):
        home_squad = [{"player_id": i} for i in range(20)]
        away_squad = [{"player_id": i} for i in range(10)]

        ctx = _build_match_context({}, home_squad, away_squad, 1, 2)

        self.assertIn("home_attack_delta", ctx)
        self.assertGreater(ctx["home_attack_delta"], 0)

    def test_build_match_context_equal_squads(self):
        squad = [{"player_id": i} for i in range(15)]
        ctx = _build_match_context({}, squad, squad, 1, 2)
        # No attack delta for equal squads
        self.assertNotIn("home_attack_delta", ctx)
        self.assertNotIn("away_attack_delta", ctx)


class NeutralVenueTests(unittest.TestCase):
    def test_world_cup_is_neutral(self):
        fixture = {"league_id": 732, "league": {"sub_type": "cup_international"}}
        from src.data.sportmonks_adapter import _is_neutral_venue
        self.assertTrue(_is_neutral_venue(fixture))

    def test_euro_finals_is_neutral(self):
        fixture = {"league_id": 1326, "league": {"sub_type": "cup_international"}}
        from src.data.sportmonks_adapter import _is_neutral_venue
        self.assertTrue(_is_neutral_venue(fixture))

    def test_wc_qualifier_is_not_neutral(self):
        fixture = {"league_id": 720, "league": {"sub_type": "international"}}
        from src.data.sportmonks_adapter import _is_neutral_venue
        self.assertFalse(_is_neutral_venue(fixture))

    def test_euro_qualification_is_not_neutral(self):
        fixture = {"league_id": 1325, "league": {"sub_type": "international"}}
        from src.data.sportmonks_adapter import _is_neutral_venue
        self.assertFalse(_is_neutral_venue(fixture))

    def test_nations_league_is_not_neutral(self):
        fixture = {"league_id": 1538, "league": {"sub_type": "cup_international"}}
        from src.data.sportmonks_adapter import _is_neutral_venue
        self.assertFalse(_is_neutral_venue(fixture))

    def test_friendly_at_home_venue_is_not_neutral(self):
        """league_id=1082, sub_type='friendly', venue = home country → NOT neutral."""
        fixture = {
            "league_id": 1082,
            "league": {"sub_type": "friendly"},
            "venue": {"country_id": 462},  # England
            "participants": [
                {"id": 1, "name": "England", "country_id": 462, "meta": {"location": "home"}},
                {"id": 2, "name": "Brazil", "country_id": 123, "meta": {"location": "away"}},
            ],
        }
        from src.data.sportmonks_adapter import _is_neutral_venue
        self.assertFalse(_is_neutral_venue(fixture))

    def test_friendly_at_neutral_venue_is_neutral(self):
        """league_id=1082, sub_type='friendly', venue ≠ home country → neutral."""
        fixture = {
            "league_id": 1082,
            "league": {"sub_type": "friendly"},
            "venue": {"country_id": 999},  # neutral site
            "participants": [
                {"id": 1, "name": "England", "country_id": 462, "meta": {"location": "home"}},
                {"id": 2, "name": "Brazil", "country_id": 123, "meta": {"location": "away"}},
            ],
        }
        from src.data.sportmonks_adapter import _is_neutral_venue
        self.assertTrue(_is_neutral_venue(fixture))


class AverageOddsTests(unittest.TestCase):
    def test_average_odds_uses_probability_method(self):
        """Verify average_odds averages probabilities, not raw decimal odds.

        With 2 bookmakers:
          BK1: H=2.0  D=3.0  A=5.0   → probs (0.500, 0.333, 0.200)
          BK2: H=1.5  D=4.0  A=10.0  → probs (0.667, 0.250, 0.100)

        Raw decimal avg: H=(2+1.5)/2=1.75  D=(3+4)/2=3.5  A=(5+10)/2=7.5
        Prob avg: H=1/0.5835=1.714  D=1/0.2917=3.429  A=1/0.15=6.667

        The away odds via prob-avg (6.67) are lower than raw decimal avg (7.5),
        because the extreme 10.0 underdog is properly down-weighted.
        """
        from unittest.mock import patch
        from src.data.sportmonks_adapter import SportmonksAdapter

        mock_odds = {
            "1": {"home": 2.0, "draw": 3.0, "away": 5.0},
            "2": {"home": 1.5, "draw": 4.0, "away": 10.0},
        }

        adapter = SportmonksAdapter()
        with patch(
            "src.data.sportmonks_adapter.get_fixture_odds_1x2",
            return_value=mock_odds,
        ):
            result = adapter.average_odds(99999)

        self.assertIsNotNone(result)
        avg_home, avg_draw, avg_away = result

        # Prob-average home ≈ 1.71 (vs 1.75 raw)
        self.assertAlmostEqual(avg_home, 1.714, places=2)
        # Prob-average draw ≈ 3.43 (vs 3.50 raw)
        self.assertAlmostEqual(avg_draw, 3.429, places=2)
        # Prob-average away ≈ 6.67 (vs 7.50 raw) — key: avoids underdog skew
        self.assertAlmostEqual(avg_away, 6.667, places=2)
        self.assertLess(avg_away, 7.5, "prob-avg away should be less than raw decimal avg")

    def test_average_odds_empty_returns_none(self):
        from unittest.mock import patch
        from src.data.sportmonks_adapter import SportmonksAdapter

        adapter = SportmonksAdapter()
        with patch(
            "src.data.sportmonks_adapter.get_fixture_odds_1x2",
            return_value={},
        ):
            result = adapter.average_odds(99999)

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
