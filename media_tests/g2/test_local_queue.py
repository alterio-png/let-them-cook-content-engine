"""Real persistent local named-task transport, independent of PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
import gc
import os
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from engine.local_queue import LocalQueue


class LocalQueueTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "queue.sqlite3"
        self.queue = LocalQueue(self.path)

    def test_connection_descriptors_close_without_garbage_collection(self):
        event_id, job_id = str(uuid4()), str(uuid4())
        self.queue.ensure_task(event_id, job_id)
        # SQLite Connection.__exit__ commits/rolls back but does not close it.
        # Explicit closure must hold even if cyclic garbage collection is off.
        gc.collect()
        baseline = len(list(Path("/proc/self/fd").iterdir()))
        enabled = gc.isenabled()
        gc.disable()
        try:
            for _ in range(200):
                self.assertEqual(job_id, self.queue.lookup(event_id)["job_id"])
            self.assertLessEqual(len(list(Path("/proc/self/fd").iterdir())), baseline + 1)
        finally:
            if enabled:
                gc.enable()

    def test_concurrent_idempotent_tasks_persist_and_acknowledge(self):
        event_id, job_id = str(uuid4()), str(uuid4())
        with ThreadPoolExecutor(max_workers=8) as pool:
            rows = list(pool.map(lambda _: self.queue.ensure_task(event_id, job_id), range(24)))
        self.assertEqual({event_id}, {row["event_id"] for row in rows})
        reopened = LocalQueue(self.path)
        self.assertEqual(1, len(reopened.pending()))
        self.assertEqual(job_id, reopened.lookup(event_id)["job_id"])
        self.assertTrue(reopened.ack(event_id))
        self.assertEqual([], self.queue.pending())
        with self.assertRaises(ValueError):
            reopened.ensure_task(event_id, str(uuid4()))
        self.assertEqual(0o600, self.path.stat().st_mode & 0o777)

    def test_unsafe_queue_paths_and_identifiers_are_rejected(self):
        external = self.root / "external"
        external.mkdir(mode=0o700)
        symlink = self.root / "alias"
        symlink.symlink_to(external, target_is_directory=True)
        with self.assertRaises(OSError):
            LocalQueue(symlink / "queue.sqlite3")
        with self.assertRaises(ValueError):
            self.queue.ensure_task("../escape", str(uuid4()))
        self.assertEqual([], self.queue.pending())


if __name__ == "__main__":
    unittest.main()
