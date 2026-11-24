import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class DeepSeekAnalysisServiceTests(unittest.TestCase):
    def test_service_caches_by_snapshot_key(self):
        from src.services.deepseek_analysis_service import DeepSeekAnalysisService

        payload = {
            "match_id": "sportmonks-123",
            "home_team": "Spain",
            "away_team": "Brazil",
            "generated_at": "2026-06-30T12:00:00+00:00",
            "probabilities": {"home": 0.4, "draw": 0.3, "away": 0.3},
            "top_scorelines": [[1, 0, 0.12], [1, 1, 0.1], [0, 1, 0.09]],
            "confidence": 0.72,
            "prematch_snapshot_id": "snap_123",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            svc = DeepSeekAnalysisService(
                cache_dir=Path(tmpdir),
                api_key="test-key",
                base_url="https://api.deepseek.com",
                model="deepseek-chat",
                enabled=True,
            )
            with patch.object(svc, "_call_api", return_value="Analysis") as call_mock:
                first = svc.generate(payload)
                second = svc.generate(payload)
                self.assertIn("Analysis", first["analysis"])
                self.assertIn("This analysis is for informational purposes only and does not constitute betting advice.", first["analysis"])
                self.assertEqual(first["analysis"], second["analysis"])
                self.assertEqual(call_mock.call_count, 1)
