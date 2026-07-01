import tempfile
import unittest
from pathlib import Path

from src.billing.order_store import BillingStore
from src.billing.subscription_service import SubscriptionService


class CardCodeServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_redeem_code_activates_subscription_and_marks_used(self):
        from src.auth.card_codes import CardCodeService, CardCodeStore

        billing = SubscriptionService(BillingStore(self.base_dir / "billing"))
        service = CardCodeService(
            store=CardCodeStore(self.base_dir / "cards"),
            subscription_service=billing,
        )
        generated = service.generate_codes(tier="vip", duration_days=7, count=1)

        result = service.redeem_code(username="alice", code=generated[0]["code"])

        self.assertEqual(result["tier"], "vip")
        self.assertEqual(result["username"], "alice")
        self.assertFalse(result["reused"])
        sub = billing.get_subscription("alice")
        self.assertIsNotNone(sub)
        self.assertEqual(sub.tier, "vip")

        stored = service.store.get_by_code(generated[0]["code"])
        self.assertEqual(stored.used_by, "alice")
        self.assertIsNotNone(stored.used_at)

    def test_redeem_code_cannot_be_reused(self):
        from src.auth.card_codes import CardCodeService, CardCodeStore

        billing = SubscriptionService(BillingStore(self.base_dir / "billing"))
        service = CardCodeService(
            store=CardCodeStore(self.base_dir / "cards"),
            subscription_service=billing,
        )
        code = service.generate_codes(tier="basic", duration_days=3, count=1)[0]["code"]
        service.redeem_code(username="alice", code=code)

        with self.assertRaises(ValueError):
            service.redeem_code(username="bob", code=code)

    def test_same_user_can_login_again_with_same_card_without_extending(self):
        from src.auth.card_codes import CardCodeService, CardCodeStore

        billing = SubscriptionService(BillingStore(self.base_dir / "billing"))
        service = CardCodeService(
            store=CardCodeStore(self.base_dir / "cards"),
            subscription_service=billing,
        )
        code = service.generate_codes(tier="basic", duration_days=3, count=1)[0]["code"]
        first = service.redeem_code(username="alice", code=code)

        second = service.redeem_code(username="alice", code=code)

        self.assertTrue(second["reused"])
        self.assertEqual(second["tier"], "basic")
        self.assertEqual(second["expires_at"], first["expires_at"])

    def test_list_codes_filters_by_batch_and_keeps_plaintext_for_export(self):
        from src.auth.card_codes import CardCodeService, CardCodeStore

        billing = SubscriptionService(BillingStore(self.base_dir / "billing"))
        service = CardCodeService(
            store=CardCodeStore(self.base_dir / "cards"),
            subscription_service=billing,
        )
        service.generate_codes(tier="basic", duration_days=3, count=1, batch_id="tb-1", platform="taobao")
        service.generate_codes(tier="vip", duration_days=7, count=1, batch_id="xy-1", platform="xianyu")

        listed = service.list_codes(batch_id="tb-1")

        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["batch_id"], "tb-1")
        self.assertEqual(listed[0]["platform"], "taobao")
        self.assertTrue(listed[0]["code"])

    def test_disable_code_prevents_future_redemption(self):
        from src.auth.card_codes import CardCodeService, CardCodeStore

        billing = SubscriptionService(BillingStore(self.base_dir / "billing"))
        service = CardCodeService(
            store=CardCodeStore(self.base_dir / "cards"),
            subscription_service=billing,
        )
        code = service.generate_codes(tier="basic", duration_days=3, count=1)[0]["code"]

        service.disable_code(code)

        with self.assertRaises(ValueError):
            service.redeem_code(username="alice", code=code)

    def test_store_is_backward_compatible_with_old_json_shape(self):
        from src.auth.card_codes import CardCodeStore

        store = CardCodeStore(self.base_dir / "cards")
        store.codes_path.write_text(
            """
            {
              "codes": {
                "abc": {
                  "code_hash": "abc",
                  "tier": "basic",
                  "duration_days": 7,
                  "created_at": "2026-07-01T00:00:00+00:00"
                }
              }
            }
            """.strip(),
            encoding="utf-8",
        )

        record = store.all_records()[0]

        self.assertIsNone(record.code_value)
        self.assertIsNone(record.batch_id)
        self.assertFalse(record.disabled)
