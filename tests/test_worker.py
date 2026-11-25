import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from epl_agent import operations, worker


class WorkerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.enterContext(patch.dict(os.environ, {"EPL_AGENT_HOME":directory.name,
                                                 "EPL_EVIDENCE_REPO":"example/epl"}))

    def test_single_instance_and_readonly_idle_status(self):
        with worker.single_worker(self.root), self.assertRaisesRegex(ValueError,"already holds"):
            with worker.single_worker(self.root):
                self.fail("second worker acquired live lock")
        with worker.single_worker(self.root):
            self.assertEqual(operations.local_status()["worker_status"], "unknown")
        self.assertFalse((self.root/"operations.sqlite3").exists())
        self.assertFalse((self.root/"market.sqlite3").exists())

    def test_once_runs_independent_jobs_and_reconciles_after_capture(self):
        events = []
        def receive(provider, root):
            events.append(provider)
            if provider == "openfootball":
                raise OSError("fixture service unavailable")
        with patch("epl_agent.freezing.capture",side_effect=lambda _:events.append("capture") or {"captured":1}), \
             patch.object(worker,"synchronize",side_effect=receive), \
             patch.object(worker,"witness_pending",side_effect=lambda _:events.append("witness")), \
             patch.object(worker,"publish_snapshot",side_effect=lambda _:events.append("upload")), \
             patch.object(operations,"check_deployment",side_effect=lambda _:events.append("pages")):
            result = worker.run(publish_snapshot_enabled=True,once=True)
        self.assertEqual(result,{"ok":False,"errors":{"openfootball":"OSError"}})
        self.assertTrue(events.index("capture")<events.index("witness")<events.index("upload")<events.index("pages"))
        self.assertEqual(operations.public_state()["sync_status"],"partial")

    def test_failed_upload_retries_same_batch_and_records_actual_online_marker(self):
        sha = "a"*40
        batch = {"fixtures":[{"id":"f","kickoff_utc":"2026-11-25","schedule_revision":2}],
                 "ledger":{"records":[{"id":"f","kickoff":"2026-11-25","revision":2,"hash":"c"*64}]}}
        def export(target, root):
            (target/"snapshot.json").write_text(json.dumps({"data":batch}))
        with patch.object(worker.snapshot,"export_bundle",side_effect=export) as exports, \
             patch.object(worker.snapshot,"publish_bundle",side_effect=[OSError("down"),sha]):
            with self.assertRaises(OSError):
                worker.publish_snapshot(self.root)
            worker.publish_snapshot(self.root)
            exports.assert_called_once()
        self.assertEqual(operations.public_deployments(),[])
        operations.check_deployment(self.root,http=Mock(return_value=Mock(text="b"*40)))
        self.assertEqual(operations.public_state()["publication_status"],"pending_deployment")
        self.assertEqual(operations.public_deployments(),[])
        get = Mock(return_value=Mock(text=sha))
        operations.check_deployment(self.root,http=get)
        first = operations.public_deployments()
        operations.check_deployment(self.root,http=get)
        self.assertEqual(operations.public_deployments(),first)
        self.assertEqual(first[0]["sha"],sha)
        self.assertEqual(operations.public_state()["publication_status"],"deployed")
