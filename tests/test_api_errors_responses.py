import tests.warnings_filter  # noqa: F401 — suppress pickle warnings

import unittest

from src.api.errors import ApiError
from src.api.responses import build_error_payload, build_success_payload


class ApiResponsesTests(unittest.TestCase):
    def test_success_payload_shape(self):
        payload = build_success_payload({"value": 1}, request_id="req_test")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["value"], 1)
        self.assertEqual(payload["meta"]["request_id"], "req_test")
        self.assertIn("generated_at", payload["meta"])

    def test_error_payload_shape(self):
        err = ApiError("vip_required", "VIP access required", status_code=403)
        payload = build_error_payload(err, request_id="req_test")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "vip_required")
        self.assertEqual(payload["error"]["message"], "VIP access required")
        self.assertEqual(payload["meta"]["request_id"], "req_test")

    def test_api_error_defaults(self):
        err = ApiError("bad_request", "Bad request")
        self.assertEqual(err.status_code, 400)
        self.assertEqual(err.details, {})


if __name__ == "__main__":
    unittest.main()
