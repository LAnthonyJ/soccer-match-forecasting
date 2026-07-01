import tests.warnings_filter  # noqa: F401 — suppress pickle warnings

import unittest

from src.api.app import app


class H5StaticTests(unittest.TestCase):
    def test_h5_index_served(self):
        with app.test_client() as c:
            r = c.get("/h5")
            try:
                self.assertEqual(r.status_code, 200)
                self.assertIn("text/html", r.content_type)
            finally:
                r.close()

    def test_h5_asset_css_served(self):
        with app.test_client() as c:
            r = c.get("/h5/styles.css")
            try:
                self.assertEqual(r.status_code, 200)
                self.assertIn("text/css", r.content_type)
            finally:
                r.close()

    def test_h5_top_level_js_served(self):
        with app.test_client() as c:
            r = c.get("/h5/app.js")
            try:
                self.assertEqual(r.status_code, 200)
                self.assertIn("text/javascript", r.content_type)
            finally:
                r.close()

    def test_h5_nested_js_served(self):
        with app.test_client() as c:
            r = c.get("/h5/views/matches.js")
            try:
                self.assertEqual(r.status_code, 200)
                self.assertIn("text/javascript", r.content_type)
            finally:
                r.close()

    def test_h5_can_be_disabled_by_runtime_config(self):
        from src.api import app as api_app
        old = api_app._runtime_config
        api_app._runtime_config = type("Cfg", (), {"enable_h5": False})()
        try:
            with app.test_client() as c:
                r = c.get("/h5")
                try:
                    self.assertEqual(r.status_code, 404)
                    payload = r.get_json()
                    self.assertFalse(payload["ok"])
                    self.assertEqual(payload["error"]["code"], "not_found")
                finally:
                    r.close()
        finally:
            api_app._runtime_config = old


if __name__ == "__main__":
    unittest.main()
