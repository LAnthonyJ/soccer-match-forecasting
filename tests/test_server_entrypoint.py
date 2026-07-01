import tests.warnings_filter  # noqa: F401 — suppress pickle warnings

import unittest


class ServerEntrypointTests(unittest.TestCase):
    def test_server_exposes_flask_app(self):
        from src.server import app

        self.assertEqual(app.name, "src.api.app")

    def test_server_has_runtime_config(self):
        from src.server import runtime_config

        self.assertIn(runtime_config.app_env, {"dev", "test", "prod"})
        self.assertGreater(runtime_config.port, 0)
