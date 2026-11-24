import io
import sys
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from src import predict_production as cli


class OfflineForecastTests(unittest.TestCase):
    def test_bundled_forecast_skips_pickle_and_returns_probabilities(self):
        with patch.object(cli, "load_score_models", side_effect=AssertionError):
            result = cli.predict_one(home_team="France", away_team="Argentina",
                                     bundled_model=True, odds=None, tier="basic")
        self.assertAlmostEqual(sum(result["probabilities"].values()), 1.0)
        self.assertEqual(result["data_version"], "dc-1945ec2e2d6e")
        self.assertEqual(len(result["top_scorelines"]), 3)

    def test_unknown_team_reports_missing_parameters(self):
        with self.assertRaisesRegex(ValueError, "Atlantis"):
            cli.predict_one(home_team="Atlantis", away_team="Argentina",
                            bundled_model=True, odds=None, tier="basic")

    def test_cli_rejects_incomplete_and_invalid_odds_before_prediction(self):
        args = ["forecast", "--home", "France", "--away", "Argentina"]
        cases = [["--odds-home", "2"]]
        for invalid in ("0", "1", "nan", "inf"):
            cases.append(["--odds-home", invalid, "--odds-draw", "3", "--odds-away", "4"])
        for odds in cases:
            with self.subTest(odds=odds), patch.object(sys, "argv", args + odds):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                    cli.main()
                self.assertEqual(caught.exception.code, 2)
