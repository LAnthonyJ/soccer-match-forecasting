import tests.warnings_filter  # noqa: F401 — suppress pickle warnings
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.data.provider_schema import MatchRecord
from src.models.prediction_snapshot import PredictionSnapshot, build_prediction_snapshot
from src.models.score_models import ScoreMatrix


class PredictionServiceTests(unittest.TestCase):
    def setUp(self):
        self.match = MatchRecord(
            provider="sportmonks", provider_match_id="1", match_id="sportmonks-1",
            kickoff_utc=datetime(2026, 6, 28, tzinfo=timezone.utc),
            home_team="Spain", away_team="Brazil",
            tournament="World Cup", neutral=True, status="scheduled",
        )

    def _make_snapshot(self, model_version="test-v1"):
        sm = ScoreMatrix.from_independent_poisson(1.4, 1.1)
        return build_prediction_snapshot(
            match_id="sportmonks-1", home_team="Spain", away_team="Brazil",
            score_matrix=sm, final_probs=sm.match_outcome_probs(),
            model_version=model_version, data_version="test", confidence=0.7,
            lineup_data_quality="none",
            data_updated_at="2026-06-28T12:00:00Z",
            model_pipeline={"base_model": "dynamic-dc-v1"},
            prematch_snapshot_id="sportmonks-1_20260628T120000",
        )

    # -- Cache tests ------------------------------------------------------------

    def test_save_and_load_latest_prediction(self):
        from src.services.prediction_service import (
            save_prediction_snapshot,
            load_latest_prediction,
        )
        snap = self._make_snapshot()
        with tempfile.TemporaryDirectory() as tmpdir:
            import src.services.prediction_service as svc
            orig = svc.CACHE_DIR
            svc.CACHE_DIR = Path(tmpdir)
            try:
                path = save_prediction_snapshot(snap, tier="vip")
                self.assertTrue(path.exists())

                loaded = load_latest_prediction(snap.match_id)
                self.assertIsNotNone(loaded)
                self.assertEqual(loaded["match_id"], snap.match_id)
                self.assertEqual(loaded["model_version"], snap.model_version)
            finally:
                svc.CACHE_DIR = orig

    def test_load_latest_returns_newest(self):
        from src.services.prediction_service import (
            save_prediction_snapshot,
            load_latest_prediction,
        )
        snap1 = self._make_snapshot("v1")
        snap2 = self._make_snapshot("v2")
        with tempfile.TemporaryDirectory() as tmpdir:
            import src.services.prediction_service as svc
            orig = svc.CACHE_DIR
            svc.CACHE_DIR = Path(tmpdir)
            try:
                save_prediction_snapshot(snap1, tier="vip")
                save_prediction_snapshot(snap2, tier="vip")
                latest = load_latest_prediction(snap1.match_id)
                self.assertEqual(latest["model_version"], "v2")
            finally:
                svc.CACHE_DIR = orig

    def test_list_cached_predictions(self):
        from src.services.prediction_service import (
            save_prediction_snapshot,
            list_cached_predictions,
        )
        snap = self._make_snapshot()
        with tempfile.TemporaryDirectory() as tmpdir:
            import src.services.prediction_service as svc
            orig = svc.CACHE_DIR
            svc.CACHE_DIR = Path(tmpdir)
            try:
                save_prediction_snapshot(snap, tier="vip")
                cached = list_cached_predictions(snap.match_id)
                self.assertEqual(len(cached), 1)
            finally:
                svc.CACHE_DIR = orig

    # -- Basic/VIP contract tests -----------------------------------------------

    def test_basic_never_exposes_vip_fields(self):
        snap = self._make_snapshot()
        basic = snap.to_public_dict(tier="basic")

        forbidden = [
            "score_heatmap", "market_edge", "lineup_adjustment",
            "prematch_snapshot_id", "model_pipeline", "missing_key_players",
        ]
        for field in forbidden:
            self.assertNotIn(field, basic, f"basic must not contain '{field}'")

        required = [
            "match_id", "home_team", "away_team", "model_version",
            "probabilities", "top_scorelines", "confidence",
            "lineup_data_status", "data_updated_at",
        ]
        for field in required:
            self.assertIn(field, basic, f"basic must contain '{field}'")

    def test_vip_includes_full_fields(self):
        snap = self._make_snapshot()
        vip = snap.to_public_dict(tier="vip")

        required_vip = ["score_heatmap", "over25", "btts"]
        for field in required_vip:
            self.assertIn(field, vip, f"vip must contain '{field}'")

    # -- Fallback tests ---------------------------------------------------------

    def test_no_odds_payload(self):
        """Without odds, market_probs and market_edge must be absent from VIP."""
        sm = ScoreMatrix.from_independent_poisson(1.4, 1.1)
        snap = build_prediction_snapshot(
            match_id="test", home_team="A", away_team="B",
            score_matrix=sm, final_probs=sm.match_outcome_probs(),
            model_version="test", data_version="test", confidence=0.7,
            market_probs=None,
        )
        vip = snap.to_public_dict(tier="vip")
        self.assertIsNone(vip.get("market_probs"))
        self.assertIsNone(vip.get("market_edge"))

    def test_model_pipeline_complete(self):
        snap = self._make_snapshot()
        vip = snap.to_public_dict(tier="vip")
        self.assertIsNotNone(vip.get("model_pipeline"))
        mp = vip["model_pipeline"]
        self.assertIn("base_model", mp)

    def test_prediction_result_repr(self):
        from src.services.prediction_service import PredictionResult
        snap = self._make_snapshot()
        result = PredictionResult(
            snapshot=snap,
            fallback_used=["odds_failed"],
            warnings=["no lineup data"],
        )
        self.assertEqual(len(result.fallback_used), 1)
        self.assertEqual(len(result.warnings), 1)


if __name__ == "__main__":
    unittest.main()
