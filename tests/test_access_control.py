import tests.warnings_filter  # noqa: F401 — suppress pickle warnings

import unittest
from datetime import datetime, timedelta, timezone

from src.auth.access_control import (
    UserSubscription,
    VIP_ONLY_FIELDS,
    can_access_tier,
    clear_subscriptions,
    filter_prediction_payload,
    resolve_user_tier,
    set_subscription,
)


class AccessControlTests(unittest.TestCase):
    def setUp(self):
        clear_subscriptions()

    def _vip_payload(self):
        return {
            "match_id": "test", "home_team": "A", "away_team": "B",
            "probabilities": {"home": 0.4, "draw": 0.3, "away": 0.3},
            "top_scorelines": [[1, 0, 0.1], [0, 1, 0.09], [1, 1, 0.08]],
            "score_heatmap": [[0.01] * 10] * 10,
            "market_probs": {"home": 0.38, "draw": 0.30, "away": 0.32},
            "market_edge": {"home": 0.02, "draw": 0.0, "away": -0.02},
            "lineup_adjustment": {"home_lineup_delta": 0.02},
            "missing_key_players": ["home missing: Star"],
            "prematch_snapshot_id": "test_snap",
            "model_pipeline": {"base_model": "dc"},
            "over25": 0.5, "btts": 0.5,
            "confidence": 0.7,
            "lineup_data_status": "none",
        }

    def test_unknown_user_is_free(self):
        self.assertEqual(resolve_user_tier("nobody"), "free")

    def test_vip_user_gets_vip_fields(self):
        set_subscription(UserSubscription("u1", "vip"))
        payload = filter_prediction_payload(self._vip_payload(), "vip")
        for field in VIP_ONLY_FIELDS:
            self.assertIn(field, payload, f"vip must have '{field}'")

    def test_basic_user_strips_vip_fields(self):
        set_subscription(UserSubscription("u2", "basic"))
        payload = filter_prediction_payload(self._vip_payload(), "basic")
        for field in VIP_ONLY_FIELDS:
            self.assertNotIn(field, payload, f"basic must not have '{field}'")

    def test_free_user_strips_vip_fields(self):
        payload = filter_prediction_payload(self._vip_payload(), "free")
        for field in VIP_ONLY_FIELDS:
            self.assertNotIn(field, payload, f"free must not have '{field}'")

    def test_admin_sees_all_plus_debug(self):
        set_subscription(UserSubscription("u3", "admin"))
        payload = filter_prediction_payload(self._vip_payload(), "admin")
        for field in VIP_ONLY_FIELDS:
            self.assertIn(field, payload, f"admin must have '{field}'")
        self.assertIn("_debug_tier", payload)

    def test_expired_vip_downgrades_to_free(self):
        sub = UserSubscription("u4", "vip",
                               expires_at=datetime.now(timezone.utc) - timedelta(days=1))
        set_subscription(sub)
        self.assertEqual(resolve_user_tier("u4"), "free")

    def test_can_access_tier(self):
        set_subscription(UserSubscription("u5", "basic"))
        self.assertTrue(can_access_tier("u5", "basic"))
        self.assertFalse(can_access_tier("u5", "vip"))
        self.assertTrue(can_access_tier("u5", "free"))

    def test_filter_preserves_basic_required_fields(self):
        payload = filter_prediction_payload(self._vip_payload(), "basic")
        required = ["match_id", "probabilities", "top_scorelines", "confidence"]
        for field in required:
            self.assertIn(field, payload, f"basic must retain '{field}'")

    def test_vip_not_downgraded_when_active(self):
        sub = UserSubscription("u6", "vip",
                               expires_at=datetime.now(timezone.utc) + timedelta(days=30))
        set_subscription(sub)
        self.assertEqual(resolve_user_tier("u6"), "vip")


    def test_durable_subscription_resolves_tier(self):
        set_subscription(UserSubscription("durable_u1", "vip"))
        self.assertEqual(resolve_user_tier("durable_u1"), "vip")

    def test_invalid_tier_downgrades_to_free(self):
        set_subscription(UserSubscription("bad_tier", "supervip"))
        self.assertEqual(resolve_user_tier("bad_tier"), "free")


    def test_can_access_tier_from_service_uses_durable_tier(self):
        from unittest.mock import MagicMock
        mock_svc = MagicMock()
        mock_svc.resolve_tier.return_value = "vip"
        from src.auth.access_control import can_access_tier_from_service
        self.assertTrue(can_access_tier_from_service("u1", "vip", mock_svc))
        self.assertFalse(can_access_tier_from_service("u1", "admin", mock_svc))
        mock_svc.resolve_tier.assert_called_with("u1")


if __name__ == "__main__":
    unittest.main()
