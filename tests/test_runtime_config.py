import tests.warnings_filter  # noqa: F401 — suppress pickle warnings

import os
import unittest
from unittest.mock import patch

from src.config.runtime import RuntimeConfig, load_runtime_config


class RuntimeConfigTests(unittest.TestCase):
    def test_defaults_are_dev_safe(self):
        with patch.dict(os.environ, {}, clear=True):
            cfg = load_runtime_config()
        self.assertEqual(cfg.app_env, "dev")
        self.assertEqual(cfg.port, 5000)
        self.assertTrue(cfg.enable_h5)
        self.assertFalse(cfg.flask_debug)
        self.assertEqual(str(cfg.data_dir).replace("\\", "/"), "data")

    def test_env_overrides_are_parsed(self):
        env = {
            "APP_ENV": "prod",
            "APP_BASE_URL": "https://example.com",
            "DATA_DIR": "runtime-data",
            "ENABLE_H5": "false",
            "PORT": "8080",
            "FLASK_DEBUG": "0",
            "BETA_AUTH_SECRET": "beta-secret",
            "PAYMENT_WEBHOOK_SECRET": "webhook-secret",
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = load_runtime_config()
        self.assertEqual(cfg.app_env, "prod")
        self.assertEqual(cfg.app_base_url, "https://example.com")
        self.assertEqual(str(cfg.data_dir), "runtime-data")
        self.assertFalse(cfg.enable_h5)
        self.assertEqual(cfg.port, 8080)

    def test_prod_rejects_debug(self):
        with patch.dict(os.environ, {"APP_ENV": "prod", "FLASK_DEBUG": "1"}, clear=True):
            with self.assertRaises(ValueError) as ctx:
                load_runtime_config()
        self.assertIn("FLASK_DEBUG", str(ctx.exception))

    def test_beta_auth_env_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            cfg = load_runtime_config()
        self.assertEqual(cfg.payment_provider, "mock")
        self.assertFalse(cfg.enable_dev_payment_webhook)
        self.assertEqual(cfg.beta_session_ttl_hours, 720)

    def test_prod_requires_beta_secrets(self):
        with patch.dict(os.environ, {"APP_ENV": "prod"}, clear=True):
            with self.assertRaises(ValueError) as ctx:
                load_runtime_config()
        self.assertIn("BETA_AUTH_SECRET", str(ctx.exception))

    def test_prod_rejects_dev_webhook(self):
        env = {
            "APP_ENV": "prod",
            "BETA_AUTH_SECRET": "secret",
            "PAYMENT_WEBHOOK_SECRET": "webhook",
            "ENABLE_DEV_PAYMENT_WEBHOOK": "true",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ValueError) as ctx:
                load_runtime_config()
        self.assertIn("ENABLE_DEV_PAYMENT_WEBHOOK", str(ctx.exception))

    def test_invalid_boolean_rejected(self):
        with patch.dict(os.environ, {"ENABLE_H5": "maybe"}, clear=True):
            with self.assertRaises(ValueError) as ctx:
                load_runtime_config()
        self.assertIn("ENABLE_H5", str(ctx.exception))

    def test_deepseek_env_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            cfg = load_runtime_config()
        self.assertFalse(cfg.enable_ai_analysis)
        self.assertEqual(cfg.deepseek_base_url, "https://api.deepseek.com")
        self.assertEqual(cfg.deepseek_model, "deepseek-chat")
