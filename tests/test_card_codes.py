import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


class CardCliIssueBatchTests(unittest.TestCase):
    def test_issue_rows_generates_prefixed_usernames(self):
        from src.auth.card_cli import _issue_rows

        rows = [
            {"code": "code-a", "tier": "basic"},
            {"code": "code-b", "tier": "basic"},
            {"code": "code-c", "tier": "basic"},
        ]

        issued = _issue_rows(rows, username_prefix="wc", start_index=7, expires_days=7)

        self.assertEqual(
            issued,
            [
                {"username": "wc007", "card_code": "code-a", "tier": "basic", "expires_days": 7},
                {"username": "wc008", "card_code": "code-b", "tier": "basic", "expires_days": 7},
                {"username": "wc009", "card_code": "code-c", "tier": "basic", "expires_days": 7},
            ],
        )

    def test_issue_rows_expands_padding_for_large_start_index(self):
        from src.auth.card_cli import _issue_rows

        rows = [{"code": "code-a", "tier": "vip"}]
        issued = _issue_rows(rows, username_prefix="vip", start_index=1001, expires_days=30)

        self.assertEqual(
            issued,
            [{"username": "vip1001", "card_code": "code-a", "tier": "vip", "expires_days": 30}],
        )

    def test_issue_batch_command_writes_shipping_csv(self):
        import csv
        from src.auth import card_cli

        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "issued.csv"
            fake_rows = [
                {"code": "card-1", "tier": "basic"},
                {"code": "card-2", "tier": "basic"},
            ]

            class FakeService:
                def generate_codes(self, tier, duration_days, count, batch_id=None, platform=None):
                    self.called = (tier, duration_days, count, batch_id, platform)
                    return fake_rows

            fake_service = FakeService()
            argv = [
                "card_cli.py",
                "issue-batch",
                "--tier", "basic",
                "--days", "7",
                "--count", "2",
                "--username-prefix", "wc",
                "--batch", "batch-1",
                "--platform", "railway",
                "--output", str(output),
            ]
            with patch("src.auth.card_cli.build_service", return_value=fake_service), patch("sys.argv", argv):
                card_cli.main()

            self.assertEqual(fake_service.called, ("basic", 7, 2, "batch-1", "railway"))
            with output.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))

            self.assertEqual(
                rows,
                [
                    {"username": "wc001", "card_code": "card-1", "tier": "basic", "expires_days": "7"},
                    {"username": "wc002", "card_code": "card-2", "tier": "basic", "expires_days": "7"},
                ],
            )

    def test_issue_batch_requires_positive_start_index(self):
        from src.auth import card_cli

        argv = [
            "card_cli.py",
            "issue-batch",
            "--tier", "basic",
            "--days", "7",
            "--count", "1",
            "--username-prefix", "wc",
            "--start-index", "0",
            "--output", "ignored.csv",
        ]
        with patch("src.auth.card_cli.build_service"), patch("sys.argv", argv):
            with self.assertRaises(SystemExit) as ctx:
                card_cli.main()
        self.assertIn("--start-index must be >= 1", str(ctx.exception))
