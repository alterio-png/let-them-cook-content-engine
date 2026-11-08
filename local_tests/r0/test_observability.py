"""Real filesystem checks for bounded, nonblocking and secret-free telemetry."""
import fcntl
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from engine.local_observability import Metrics


class ObservabilityTests(unittest.TestCase):
    def test_sensitive_fields_are_dropped_and_memory_and_disk_are_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            metrics = Metrics(path)
            metrics.max_log_bytes = 1024
            for _ in range(600):
                metrics('upload', 'response', status=201, password='never-record',
                        token='never-record', key='/private/never-record', error_code='safe_code')
            self.assertLessEqual(path.stat().st_size, 1024)
            self.assertNotIn('never-record', path.read_text())
            self.assertEqual(0o600, path.stat().st_mode & 0o777)
            result = metrics.snapshot()
            self.assertEqual(512, len(result['recent']))
            self.assertGreater(result['counts']['telemetry/dropped'], 0)
            self.assertTrue(all(json.loads(line)['status'] == 201 for line in path.read_text().splitlines()))

    def test_held_replica_log_lock_and_fifo_never_block_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                metrics = Metrics(path)
                started = time.monotonic()
                metrics('api', 'response', status=202)
                self.assertLess(time.monotonic() - started, .5)
                self.assertEqual(1, metrics.snapshot()['counts']['telemetry/dropped'])
            finally:
                os.close(fd)
            fifo = Path(directory) / 'fifo'
            os.mkfifo(fifo, 0o600)
            started = time.monotonic()
            Metrics(fifo)('api', 'response', status=202)
            self.assertLess(time.monotonic() - started, .5)
