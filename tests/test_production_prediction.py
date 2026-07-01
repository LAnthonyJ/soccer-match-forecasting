import tests.warnings_filter  # noqa: F401 — suppress pickle warnings

import unittest

from src.models.production_prediction import ProductionPredictionEngine, PredictionInputQuality


class FakeDixonColes:
    def __init__(self):
        self.rho = -0.05

    def predict_lambdas(self, home_team, away_team, neutral=False):
        return 1.20, 0.90


class ProductionPredictionEngineTests(unittest.TestCase):
    def test_engine_returns_market_fused_versioned_snapshot(self):
        engine = ProductionPredictionEngine(
            base_dc_model=FakeDixonColes(),
            model_version="dynamic-dc-v1",
            data_version="manual-test-data",
            goal_multiplier=1.10,
        )

        snapshot = engine.predict(
            match_id="wc2026-002",
            home_team="Spain",
            away_team="Brazil",
            odds=(2.20, 3.30, 3.40),
            quality=PredictionInputQuality(
                team_sample_home=90,
                team_sample_away=85,
                has_lineups=True,
                has_injuries=True,
                odds_snapshots=4,
                odds_volatility=0.02,
            ),
        )

        vip = snapshot.to_public_dict(tier="vip")
        self.assertEqual(vip["model_version"], "dynamic-dc-v1")
        self.assertEqual(vip["data_version"], "manual-test-data")
        self.assertIn("market_edge", vip)
        self.assertGreater(vip["confidence"], 0.5)
        self.assertAlmostEqual(sum(vip["probabilities"].values()), 1.0, places=9)


if __name__ == "__main__":
    unittest.main()
