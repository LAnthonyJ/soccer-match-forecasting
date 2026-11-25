import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from uuid import uuid4

from epl_agent.worker import single_worker


REPO = Path(__file__).resolve().parents[1]
RUNTIME = REPO / "scripts" / "collector_runtime.py"

# Exercise the genuine scheduling loop, substituting only the jobs that call providers.
IDLE_WORKER = """
import os
from pathlib import Path
from unittest.mock import patch
from epl_agent import worker
root = Path(os.environ['EPL_AGENT_HOME'])
def capture(_):
    (root / 'ready').touch()
    return {'captured': 0}
with patch('epl_agent.freezing.capture', side_effect=capture), patch.object(worker, 'synchronize'), patch.object(worker.data, 'load_matches', return_value=[]):
    worker.run()
(root / 'returned').touch()
"""

LEGACY_HOLDER = """
import os, time
from pathlib import Path
root = Path(os.environ['EPL_AGENT_HOME'])
with (root / 'worker.lock').open('w+b') as lock:
    lock.write(b'0')
    lock.flush()
    lock.seek(0)
    if os.name == 'nt':
        import msvcrt
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    (root / 'ready').touch()
    time.sleep(90)
"""


class WorkerStopTests(unittest.TestCase):
    def setUp(self):
        self.sandbox = Path(self.enterContext(TemporaryDirectory()))

    def start_holder(self, root, script):
        root.mkdir()
        process = subprocess.Popen([sys.executable, "-B", "-c", script], cwd=REPO,
                                   env={**os.environ, "EPL_AGENT_HOME":str(root)})

        def cleanup():
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)

        self.addCleanup(cleanup)
        deadline = time.monotonic() + 20
        while not (root/"ready").exists():
            self.assertIsNone(process.poll(), "isolated worker exited before acquiring its lock")
            self.assertLess(time.monotonic(), deadline, "isolated worker did not start")
            time.sleep(.05)
        return process

    def stop(self, root, *, powershell=False):
        if powershell:
            cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "RemoteSigned", "-File",
                   str(REPO / "scripts" / "windows-collector.ps1"), "Stop",
                   "-Python", sys.executable, "-DataRoot", str(root),
                   "-StateDir", str(self.sandbox / "test-settings"), "-TaskName", "EPL-test-"+uuid4().hex]
        else:
            cmd = [sys.executable, "-B", str(RUNTIME), "stop", "--root", str(root)]
        # Both the launcher and the worker must import the checkout under test.
        return subprocess.run(cmd, cwd=REPO, env={**os.environ, "PYTHONPATH":str(REPO)},
                              capture_output=True, text=True, errors="replace", timeout=30)

    def test_stop_releases_only_the_requested_workers_lock(self):
        first, other = self.sandbox/"first", self.sandbox/"other"
        target = self.start_holder(first, IDLE_WORKER)
        sentinel = self.start_holder(other, IDLE_WORKER)
        with self.assertRaises(ValueError), single_worker(first):
            self.fail("acquired lock while target was running")
        result = self.stop(first, powershell=os.name=="nt")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(target.wait(timeout=10), 0)
        self.assertTrue((first/"returned").is_file())
        with single_worker(first):
            pass
        self.assertEqual(self.stop(first).returncode, 0, "stopping a stopped collector must be idempotent")
        self.assertIsNone(sentinel.poll())
        with self.assertRaises(ValueError), single_worker(other):
            self.fail("stop released an unrelated collector's lock")
        self.assertEqual(self.stop(other).returncode, 0)
        self.assertEqual(sentinel.wait(timeout=10), 0)

    def test_legacy_holder_is_reported_and_retains_its_lock(self):
        root = self.sandbox/"legacy"
        holder = self.start_holder(root, LEGACY_HOLDER)
        result = self.stop(root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("without the stop protocol", result.stderr)
        self.assertIsNone(holder.poll())
        with self.assertRaises(ValueError), single_worker(root):
            self.fail("stop released an unmanaged legacy lock")
