import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class CloudDeploymentFileTests(unittest.TestCase):
    def test_dockerfile_uses_platform_port_and_production_entrypoint(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("python:3.11-slim", dockerfile)
        self.assertIn("gunicorn", dockerfile)
        self.assertIn("src.server:app", dockerfile)
        self.assertIn("0.0.0.0:${PORT:-5000}", dockerfile)
        self.assertIn("--timeout 120", dockerfile)
        self.assertIn("ENV DATA_DIR=/app/data", dockerfile)
        self.assertIn("mkdir -p /app/data", dockerfile)

    def test_dockerignore_excludes_secrets_and_local_data(self):
        ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

        self.assertIn(".env", ignored)
        self.assertIn("data/raw", ignored)
        self.assertIn("data/processed", ignored)
        self.assertIn("data/backtests", ignored)
        self.assertIn("outputs/models/*", ignored)
        self.assertIn("!outputs/models/score_models.pkl", ignored)
        self.assertIn(".venv", ignored)

    def test_gitignore_keeps_required_production_score_model(self):
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

        self.assertIn("outputs/models/*.pkl", ignored)
        self.assertIn("!outputs/models/score_models.pkl", ignored)

    def test_railway_config_uses_docker_and_live_healthcheck(self):
        config = json.loads((ROOT / "railway.json").read_text(encoding="utf-8"))

        self.assertEqual(config["build"]["builder"], "DOCKERFILE")
        self.assertIn("src.server:app", config["deploy"]["startCommand"])
        self.assertIn("/bin/sh -c", config["deploy"]["startCommand"])
        self.assertIn("${PORT}", config["deploy"]["startCommand"])
        self.assertNotIn("${PORT:-5000}", config["deploy"]["startCommand"])
        self.assertEqual(config["deploy"]["healthcheckPath"], "/health/live")

    def test_render_config_mounts_persistent_disk(self):
        config = (ROOT / "render.yaml").read_text(encoding="utf-8")

        self.assertIn("env: docker", config)
        self.assertIn("healthCheckPath: /health/live", config)
        self.assertIn("mountPath: /app/data", config)
        self.assertIn("key: DATA_DIR", config)
        self.assertIn("value: /app/data", config)
        self.assertIn("key: APP_BASE_URL", config)
        self.assertIn("key: PAYMENT_PROVIDER", config)
        self.assertIn("value: mock", config)
        self.assertIn("key: PAYMENT_RETURN_URL", config)
        self.assertIn("key: SPORTMONKS_API_KEY", config)
        self.assertIn("sync: false", config)

    def test_deployment_guide_documents_cloud_platform_requirements(self):
        guide = (ROOT / "docs" / "deployment_guide.md").read_text(encoding="utf-8")

        self.assertIn("Railway / Render / Zeabur", guide)
        self.assertIn("Dockerfile", guide)
        self.assertIn("/app/data", guide)
        self.assertIn("SPORTMONKS_API_KEY", guide)
        self.assertIn("BETA_AUTH_SECRET", guide)
        self.assertIn("docs/cloud_platform_launch.md", guide)
        self.assertIn("python src/smoke_test_launch.py --base-url https://<your-platform-domain>", guide)

    def test_cloud_platform_launch_doc_has_operational_steps(self):
        guide = (ROOT / "docs" / "cloud_platform_launch.md").read_text(encoding="utf-8")

        self.assertIn("railway.json", guide)
        self.assertIn("/app/data", guide)
        self.assertIn("app.<你的域名>", guide)
        self.assertIn("Cloudflare Registrar", guide)
        self.assertIn("DNS only", guide)
        self.assertIn("python src/smoke_test_launch.py --base-url", guide)
        self.assertIn("src/auth/card_cli.py generate --tier basic", guide)
        self.assertIn("src/auth/card_cli.py generate --tier vip", guide)
        self.assertIn("src/auth/card_cli.py issue-batch", guide)

    def test_seller_checklist_documents_cloud_card_issuance(self):
        checklist = (ROOT / "docs" / "seller_launch_checklist.md").read_text(encoding="utf-8")

        self.assertIn("/app/data/auth/card_codes.json", checklist)
        self.assertIn("src/auth/card_cli.py generate --tier basic", checklist)
        self.assertIn("src/auth/card_cli.py generate --tier vip", checklist)
        self.assertIn("src/auth/card_cli.py issue-batch", checklist)
        self.assertIn("app.<你的域名>/h5#/login", checklist)
        self.assertIn("GET /health/live", checklist)


class RuntimeDataDirCloudTests(unittest.TestCase):
    def test_sportmonks_cache_uses_runtime_data_dir(self):
        sys.modules.pop("src.data.sportmonks_client", None)
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"DATA_DIR": tmpdir}, clear=True):
                module = importlib.import_module("src.data.sportmonks_client")
            expected = Path(tmpdir) / "processed" / "sportmonks_cache"
            self.assertEqual(module.CACHE_DIR, expected)
        sys.modules.pop("src.data.sportmonks_client", None)

    def test_prematch_snapshot_dir_uses_runtime_data_dir(self):
        sys.modules.pop("src.data.prematch_snapshot", None)
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"DATA_DIR": tmpdir}, clear=True):
                module = importlib.import_module("src.data.prematch_snapshot")
            expected = Path(tmpdir) / "prematch_snapshots"
            self.assertEqual(module.SNAPSHOT_DIR, expected)
        sys.modules.pop("src.data.prematch_snapshot", None)


if __name__ == "__main__":
    unittest.main()
