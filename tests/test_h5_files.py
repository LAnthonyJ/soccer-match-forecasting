import tests.warnings_filter  # noqa: F401 - suppress pickle warnings

import unittest
from pathlib import Path


ROOT = Path(__file__).parent.parent / "frontend" / "h5"


class H5FilesTests(unittest.TestCase):
    def test_required_files_exist(self):
        required = [
            "index.html",
            "styles.css",
            "app.js",
            "api.js",
            "runtime-config.js",
            "state.js",
            "components/time.js",
            "views/login.js",
            "views/account.js",
            "views/matches.js",
            "views/prediction.js",
            "views/searchPrediction.js",
        ]
        for rel in required:
            self.assertTrue((ROOT / rel).exists(), rel)

    def test_api_uses_envelope_and_auth_header(self):
        api_js = (ROOT / "api.js").read_text(encoding="utf-8")
        self.assertIn("if (!payload.ok)", api_js)
        self.assertIn("Authorization", api_js)
        self.assertIn("/api/auth/redeem", api_js)
        self.assertIn("/api/teams/search", api_js)
        self.assertIn("__WC_PREDICT_CONFIG__", api_js)
        self.assertIn("buildUrl(path)", api_js)
        self.assertIn("if (!API_BASE_URL) return path;", api_js)

    def test_state_tracks_session_unlock_and_clear_auth(self):
        state_js = (ROOT / "state.js").read_text(encoding="utf-8")
        self.assertIn("sessionUnlocked", state_js)
        self.assertIn("sessionStorage", state_js)
        self.assertIn("clearAuth", state_js)

    def test_app_has_separate_login_route(self):
        app_js = (ROOT / "app.js").read_text(encoding="utf-8")
        login_js = (ROOT / "views/login.js").read_text(encoding="utf-8")
        account_js = (ROOT / "views/account.js").read_text(encoding="utf-8")
        self.assertIn('hash !== "#/login"', app_js)
        self.assertIn('window.location.hash = "#/login"', app_js)
        self.assertIn("renderLogin", app_js)
        self.assertIn(">Sign in<", login_js)
        self.assertIn("My account", account_js)

    def test_search_page_uses_prediction_label_and_no_dates(self):
        search_js = (ROOT / "views/searchPrediction.js").read_text(encoding="utf-8")
        self.assertIn("Find match forecast", search_js)
        self.assertIn("Enter a national team", search_js)
        self.assertNotIn("fromDate", search_js)
        self.assertNotIn("toDate", search_js)

    def test_search_and_matches_use_beijing_time(self):
        search_js = (ROOT / "views/searchPrediction.js").read_text(encoding="utf-8")
        matches_js = (ROOT / "views/matches.js").read_text(encoding="utf-8")
        prediction_js = (ROOT / "views/prediction.js").read_text(encoding="utf-8")
        time_js = (ROOT / "components/time.js").read_text(encoding="utf-8")
        self.assertIn("formatBeijingTime", search_js)
        self.assertIn("formatBeijingTime", matches_js)
        self.assertIn("formatBeijingTime", prediction_js)
        self.assertIn("Asia/Shanghai", time_js)
        self.assertIn("Beijing time", time_js)

    def test_account_requires_session_unlock_for_profile(self):
        account_js = (ROOT / "views/account.js").read_text(encoding="utf-8")
        self.assertIn("!state.sessionUnlocked", account_js)
        self.assertIn("Sign in first", account_js)
        self.assertIn("Sign out", account_js)

    def test_prediction_view_keeps_ai_and_vip_sections(self):
        prediction_js = (ROOT / "views/prediction.js").read_text(encoding="utf-8")
        self.assertIn("api.prediction(fixtureId, state.userId, state.sessionToken)", prediction_js)
        self.assertIn("AI analysis", prediction_js)
        self.assertIn("VIP data", prediction_js)
