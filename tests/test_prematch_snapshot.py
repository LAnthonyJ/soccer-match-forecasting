import tests.warnings_filter  # noqa: F401 — suppress pickle warnings

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.data.prematch_snapshot import (
    PrematchDataSnapshot,
    build_prematch_snapshot,
)
from src.data.provider_schema import (
    InjuryRecord,
    LineupPlayerRecord,
    MatchRecord,
    OddsSnapshot,
    PlayerStrengthRecord,
)


class PrematchSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.match = MatchRecord(
            provider="test", provider_match_id="1", match_id="test-1",
            kickoff_utc=datetime(2026, 6, 28, tzinfo=timezone.utc),
            home_team="Spain", away_team="Brazil",
            tournament="World Cup", neutral=True, status="scheduled",
        )

    def test_empty_snapshot(self):
        snap = PrematchDataSnapshot.empty(self.match)
        self.assertEqual(snap.data_quality, "none")
        self.assertFalse(snap.has_lineups())
        self.assertFalse(snap.has_injuries())
        self.assertIsNotNone(snap.snapshot_id)

    def test_save_and_load_roundtrip(self):
        snap = PrematchDataSnapshot(
            match_id="test-1", home_team="A", away_team="B",
            data_source="test", fetched_at=datetime(2026, 6, 28, tzinfo=timezone.utc),
            lineups=[
                LineupPlayerRecord(provider="t", match_id="test-1", team="A",
                                   player_name="Player1", is_starting=True, position="FW"),
            ],
            injuries=[
                InjuryRecord(provider="t", match_id="test-1", team="B",
                             player_name="Injured1", status="injured", severity="high"),
            ],
            data_quality="confirmed",
            lineup_type="confirmed",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = snap.save(f"{tmpdir}/test.json")
            self.assertTrue(Path(path).exists())

            loaded = PrematchDataSnapshot.load(path)
            self.assertEqual(loaded.match_id, snap.match_id)
            self.assertEqual(loaded.data_quality, "confirmed")
            self.assertEqual(loaded.lineup_type, "confirmed")
            self.assertEqual(len(loaded.lineups), 1)
            self.assertEqual(len(loaded.injuries), 1)
            self.assertEqual(loaded.lineups[0].player_name, "Player1")
            self.assertEqual(loaded.injuries[0].player_name, "Injured1")

    def test_build_snapshot_from_manual_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            lineups_path = f"{tmpdir}/lineups.json"
            injuries_path = f"{tmpdir}/injuries.json"
            Path(lineups_path).write_text(json.dumps([
                {"provider": "manual", "match_id": "test-1", "team": "Spain",
                 "player_name": "Star", "is_starting": True, "position": "FW"},
            ]))
            Path(injuries_path).write_text(json.dumps([
                {"provider": "manual", "match_id": "test-1", "team": "Brazil",
                 "player_name": "Injured", "status": "injured", "severity": "high"},
            ]))

            snap = build_prematch_snapshot(
                self.match,
                manual_lineups_path=lineups_path,
                manual_injuries_path=injuries_path,
            )
            self.assertEqual(snap.data_quality, "confirmed")
            self.assertEqual(len(snap.lineups), 1)
            self.assertEqual(len(snap.injuries), 1)

    def test_build_snapshot_empty_on_missing_files(self):
        snap = build_prematch_snapshot(
            self.match,
            manual_lineups_path="/nonexistent/path.json",
        )
        self.assertEqual(snap.data_quality, "none")

    def test_build_snapshot_filters_by_match_id(self):
        """Records from other matches in the same JSON must be excluded."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/multi_match_lineups.json"
            Path(path).write_text(json.dumps([
                {"provider": "m", "match_id": "test-1", "team": "Spain",
                 "player_name": "PlayerA", "is_starting": True, "position": "FW"},
                {"provider": "m", "match_id": "OTHER-MATCH", "team": "Spain",
                 "player_name": "WrongPlayer", "is_starting": True, "position": "FW"},
                {"provider": "m", "match_id": "", "team": "Spain",
                 "player_name": "NoMatchPlayer", "is_starting": True, "position": "MF"},
            ]))

            snap = build_prematch_snapshot(self.match, manual_lineups_path=path)
            self.assertEqual(len(snap.lineups), 1)
            self.assertEqual(snap.lineups[0].player_name, "PlayerA")

    def test_build_snapshot_expected_vs_confirmed_lineup(self):
        """Expected lineups must NOT be marked as confirmed."""
        # Lineup without is_starting → expected
        snap = build_prematch_snapshot(
            self.match,
            lineups=[
                LineupPlayerRecord(provider="t", match_id="test-1", team="Spain",
                                   player_name="P1", is_starting=False, position="MF"),
            ],
        )
        self.assertEqual(snap.data_quality, "expected")
        self.assertEqual(snap.lineup_type, "expected")

        # Empty lineups + only injuries → partial
        snap2 = build_prematch_snapshot(
            self.match,
            injuries=[
                InjuryRecord(provider="t", match_id="test-1", team="Brazil",
                             player_name="Inj", status="injured"),
            ],
        )
        self.assertEqual(snap2.data_quality, "partial")
        self.assertEqual(snap2.lineup_type, "none")

    def test_player_strengths_retained_without_match_id(self):
        """Strengths without match_id must still pass filter (per-team, not per-match)."""
        strengths = [
            PlayerStrengthRecord(player_id="s1", player_name="Star", team="Spain",
                                 market_value_eur=50_000_000),
            PlayerStrengthRecord(player_id="s2", player_name="Other", team="France",
                                 market_value_eur=10_000_000),  # wrong team
        ]
        snap = build_prematch_snapshot(self.match, player_strengths=strengths)
        self.assertEqual(len(snap.player_strengths), 1)
        self.assertEqual(snap.player_strengths[0].player_name, "Star")

    def test_manual_strengths_json_filters_by_team(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/strengths.json"
            Path(path).write_text(json.dumps([
                {"player_id": "s1", "player_name": "Star", "team": "Spain",
                 "market_value_eur": 50_000_000},
                {"player_id": "s2", "player_name": "Other", "team": "France",
                 "market_value_eur": 10_000_000},
            ]))
            snap = build_prematch_snapshot(self.match, manual_strengths_path=path)
            self.assertEqual(len(snap.player_strengths), 1)
            self.assertEqual(snap.player_strengths[0].player_name, "Star")

    def test_player_strengths_filter_uses_team_aliases(self):
        match = MatchRecord(
            provider="test", provider_match_id="2", match_id="test-2",
            kickoff_utc=datetime(2026, 6, 28, tzinfo=timezone.utc),
            home_team="USA", away_team="Brazil",
            tournament="World Cup", neutral=True, status="scheduled",
        )
        strengths = [
            PlayerStrengthRecord(player_id="s1", player_name="US Star", team="United States",
                                 market_value_eur=40_000_000),
            PlayerStrengthRecord(player_id="s2", player_name="Wrong", team="France",
                                 market_value_eur=10_000_000),
        ]
        snap = build_prematch_snapshot(match, player_strengths=strengths)
        self.assertEqual(len(snap.player_strengths), 1)
        self.assertEqual(snap.player_strengths[0].player_name, "US Star")

    def test_build_snapshot_prioritizes_explicit_params(self):
        explicit = [
            LineupPlayerRecord(provider="api", match_id="test-1", team="Spain",
                               player_name="API Player", is_starting=True, position="MF"),
        ]
        snap = build_prematch_snapshot(self.match, lineups=explicit)
        self.assertEqual(len(snap.lineups), 1)
        self.assertEqual(snap.lineups[0].player_name, "API Player")


class PredictionSnapshotTierTests(unittest.TestCase):
    def test_basic_tier_excludes_vip_lineup_fields(self):
        from src.models.prediction_snapshot import build_prediction_snapshot
        from src.models.score_models import ScoreMatrix

        sm = ScoreMatrix.from_independent_poisson(1.4, 1.1)
        snap = build_prediction_snapshot(
            match_id="test", home_team="A", away_team="B",
            score_matrix=sm,
            final_probs=sm.match_outcome_probs(),
            model_version="dynamic-dc-v1", data_version="test",
            confidence=0.7,
            lineup_data_quality="confirmed",
            lineup_adjustment={"home_lineup_delta": -0.05},
            missing_key_players=["home missing: Star FW"],
            prematch_snapshot_id="test_20260628T120000",
            data_updated_at="2026-06-28T12:00:00Z",
            model_pipeline={"base_model": "dynamic-dc-v1", "market_fusion": True},
        )

        basic = snap.to_public_dict(tier="basic")
        vip = snap.to_public_dict(tier="vip")

        # Basic has data status but not detailed adjustment
        self.assertIn("lineup_data_status", basic)
        self.assertEqual(basic["lineup_data_status"], "confirmed")
        self.assertIn("data_updated_at", basic)
        self.assertNotIn("lineup_adjustment", basic)
        self.assertNotIn("missing_key_players", basic)
        self.assertNotIn("prematch_snapshot_id", basic)
        self.assertNotIn("model_pipeline", basic)

        # VIP has everything
        self.assertIn("lineup_adjustment", vip)
        self.assertIn("missing_key_players", vip)
        self.assertIn("prematch_snapshot_id", vip)
        self.assertIn("model_pipeline", vip)
        self.assertEqual(vip["lineup_adjustment"]["home_lineup_delta"], -0.05)
        self.assertEqual(vip["prematch_snapshot_id"], "test_20260628T120000")

    def test_basic_tier_no_lineup_data_shows_none(self):
        from src.models.prediction_snapshot import build_prediction_snapshot
        from src.models.score_models import ScoreMatrix

        sm = ScoreMatrix.from_independent_poisson(1.4, 1.1)
        snap = build_prediction_snapshot(
            match_id="test", home_team="A", away_team="B",
            score_matrix=sm,
            final_probs=sm.match_outcome_probs(),
            model_version="test", data_version="test", confidence=0.7,
        )
        basic = snap.to_public_dict(tier="basic")
        self.assertEqual(basic["lineup_data_status"], "none")
        self.assertNotIn("lineup_adjustment", basic)


class SportmonksApiIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.match = MatchRecord(
            provider="sportmonks", provider_match_id="12345", match_id="sportmonks-12345",
            kickoff_utc=datetime(2026, 6, 28, tzinfo=timezone.utc),
            home_team="Spain", away_team="Brazil",
            tournament="World Cup", neutral=True, status="scheduled",
        )

    def test_build_sportmonks_prematch_snapshot_no_api_returns_empty(self):
        """Without fixture_id, should return empty snapshot gracefully."""
        from src.data.prematch_snapshot import build_sportmonks_prematch_snapshot

        snap = build_sportmonks_prematch_snapshot(self.match)
        self.assertEqual(snap.data_quality, "none")
        self.assertEqual(snap.lineup_type, "none")

    def test_build_sportmonks_prematch_snapshot_with_manual_fallback(self):
        """Sportmonks API empty + manual JSON -> manual data used."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/fallback_lineups.json"
            Path(path).write_text(json.dumps([
                {"provider": "manual", "match_id": "sportmonks-12345", "team": "Spain",
                 "player_name": "Fallback Player", "is_starting": True, "position": "FW"},
            ]))

            from src.data.prematch_snapshot import build_sportmonks_prematch_snapshot
            snap = build_sportmonks_prematch_snapshot(
                self.match, manual_lineups_path=path,
            )
            self.assertEqual(snap.data_quality, "confirmed")
            self.assertEqual(len(snap.lineups), 1)
            self.assertEqual(snap.lineups[0].player_name, "Fallback Player")

    def test_adapter_parse_lineup_from_sportmonks(self):
        from src.data.sportmonks_adapter import SportmonksAdapter
        adapter = SportmonksAdapter()

        payload = {
            "player": {
                "id": 123, "player_id": 123,
                "display_name": "Test Player", "name": "Test Player",
                "position_id": 27,
            },
            "is_starting": True,
        }
        result = adapter.parse_lineup_from_sportmonks(payload, "sportmonks-1", "Spain")
        self.assertEqual(result.player_name, "Test Player")
        self.assertEqual(result.team, "Spain")
        self.assertEqual(result.position, "MF")
        self.assertTrue(result.is_starting)

    def test_adapter_parse_strength_from_sportmonks(self):
        from src.data.sportmonks_adapter import SportmonksAdapter
        adapter = SportmonksAdapter()

        squad_entry = {
            "player_id": 456,
            "position_id": 25,
            "_player": {"display_name": "Defender One", "name": "Defender One"},
        }
        result = adapter.parse_strength_from_sportmonks(squad_entry, "Brazil")
        self.assertEqual(result.player_name, "Defender One")
        self.assertEqual(result.position, "DF")
        self.assertEqual(result.team, "Brazil")

    def test_api_failure_does_not_block_prediction(self):
        """When API raises, build_sportmonks_prematch_snapshot must not crash."""
        from unittest.mock import patch
        from src.data.prematch_snapshot import build_sportmonks_prematch_snapshot

        with patch(
            "src.data.sportmonks_adapter.SportmonksAdapter.fetch_fixture_lineups",
            side_effect=Exception("API down"),
        ):
            snap = build_sportmonks_prematch_snapshot(self.match, fixture_id=12345)
            self.assertEqual(snap.data_quality, "none")

    def test_expected_lineups_fallback_for_scheduled_fixture(self):
        """When confirmed lineups return [], use expected lineups."""
        from unittest.mock import patch
        from src.data.prematch_snapshot import build_sportmonks_prematch_snapshot

        mock_player = LineupPlayerRecord(
            provider="sportmonks", match_id="sportmonks-12345", team="Spain",
            player_name="Expected Starter", is_starting=True, position="FW",
        )
        with patch(
            "src.data.sportmonks_adapter.SportmonksAdapter.fetch_fixture_lineups",
            return_value=[],  # no confirmed lineups
        ), patch(
            "src.data.sportmonks_adapter.SportmonksAdapter.fetch_expected_lineup_players",
            return_value=[mock_player],
        ):
            snap = build_sportmonks_prematch_snapshot(self.match, fixture_id=12345)
            self.assertEqual(len(snap.lineups), 1)
            self.assertEqual(snap.lineups[0].player_name, "Expected Starter")
            self.assertEqual(snap.lineup_type, "confirmed")  # is_starting=True
            self.assertIn(snap.data_quality, ("confirmed", "expected"))

    def test_squad_strengths_fetched_when_team_ids_provided(self):
        """When team IDs are given, strengths are fetched from squad API."""
        from unittest.mock import patch
        from src.data.prematch_snapshot import build_sportmonks_prematch_snapshot
        from src.data.provider_schema import PlayerStrengthRecord

        mock_strength = PlayerStrengthRecord(
            player_id="s1", player_name="Spain Star", team="Spain",
            position="FW", market_value_eur=50_000_000,
        )
        with patch(
            "src.data.sportmonks_adapter.SportmonksAdapter.fetch_fixture_lineups",
            return_value=[],
        ), patch(
            "src.data.sportmonks_adapter.SportmonksAdapter.fetch_expected_lineup_players",
            return_value=[],
        ), patch(
            "src.data.sportmonks_adapter.SportmonksAdapter.fetch_squad_strengths",
            return_value=[mock_strength],
        ):
            snap = build_sportmonks_prematch_snapshot(
                self.match, fixture_id=12345,
                home_team_id=100, away_team_id=200,
            )
            self.assertGreater(len(snap.player_strengths), 0)
            self.assertEqual(snap.player_strengths[0].player_name, "Spain Star")


if __name__ == "__main__":
    unittest.main()


