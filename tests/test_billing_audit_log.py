import tests.warnings_filter  # noqa: F401 — suppress pickle warnings

import tempfile
import unittest
from pathlib import Path

from src.billing.audit_log import AuditLog


class AuditLogTests(unittest.TestCase):
    def test_appends_record(self):
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "audit.json"
            audit = AuditLog(log_path)
            audit.append("checkout.created", {"order_id": "ord_1"})
            audit.append("payment.succeeded", {"order_id": "ord_1"})
            records = audit.list_all()
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]["event_type"], "checkout.created")

    def test_empty_log_returns_list(self):
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "nonexistent.json"
            audit = AuditLog(log_path)
            records = audit.list_all()
            self.assertEqual(records, [])

    def test_preserves_event_order(self):
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "audit.json"
            audit = AuditLog(log_path)
            for i in range(5):
                audit.append("event", {"n": i})
            records = audit.list_all()
            self.assertEqual([r["payload"]["n"] for r in records], [0, 1, 2, 3, 4])
