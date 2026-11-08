"""Bounded, secret-free observations for the isolated R0 demonstration."""
from collections import Counter, deque
from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import threading
import time
import stat

import psycopg


class Metrics:
    max_log_bytes = 32 * 1024 * 1024
    stages = frozenset({'authentication', 'pool', 'upload', 'ledger', 'ingest',
                        'outbox', 'worker', 'download', 'api'})
    numeric = frozenset({'status', 'elapsed_ms', 'bytes', 'active', 'peak', 'limit',
                         'logical', 'physical', 'replay', 'retry', 'attempts',
                         'connections', 'rss_bytes', 'cpu_seconds', 'pid'})
    codes = frozenset({'error_code', 'role', 'component'})

    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.lock = threading.Lock()
        self.counts = Counter()
        self.recent = deque(maxlen=512)
        if self.path:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    def __call__(self, stage, outcome='ok', **fields):
        if stage not in self.stages:
            stage = 'api'
        clean = lambda x: isinstance(x, str) and 0 < len(x) <= 64 and all(c.isalnum() or c in '_-' for c in x)
        outcome = outcome if clean(outcome) else 'observation'
        row = {'stage': stage, 'outcome': outcome, 'at': time.time(), 'pid': os.getpid()}
        for key, value in fields.items():
            if key in self.numeric and isinstance(value, (int, float)) and math.isfinite(value):
                row[key] = value
            elif key in self.codes and clean(value):
                row[key] = value
        with self.lock:
            self.counts[f'{stage}/{outcome}'] += 1
            self.recent.append(row)
            if self.path:
                fd = None
                try:
                    fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    info = os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                        raise ValueError('metrics requires a private owned regular file')
                    payload = (json.dumps(row, separators=(',', ':'), allow_nan=False) + '\n').encode()
                    if info.st_size + len(payload) <= self.max_log_bytes:
                        os.write(fd, payload)
                    else:
                        self.counts['telemetry/dropped'] += 1
                except (OSError, ValueError):
                    # A diagnostic lock/disk failure must never turn an already
                    # committed idempotent request into a misleading HTTP 500.
                    self.counts['telemetry/dropped'] += 1
                finally:
                    if fd is not None:
                        os.close(fd)

    emit = event = record = __call__

    def snapshot(self):
        with self.lock:
            return {'scope': 'LOCAL_ONLY', 'counts': dict(self.counts),
                    'recent': list(self.recent), 'retained_events_max': self.recent.maxlen,
                    'log_bytes_max': self.max_log_bytes}


class ConnectionBudget:
    """One process budget shared by route, upload and session consumers."""
    def __init__(self, limit, metrics, component='api'):
        self.limit, self.metrics, self.component = limit, metrics, component
        self.slots = threading.BoundedSemaphore(limit)
        self.lock = threading.Lock()
        self.active = self.peak = 0

    @contextmanager
    def hold(self, role):
        started = time.monotonic()
        if not self.slots.acquire(timeout=0.1):
            self.metrics('pool', 'backpressure', error_code='combined_budget', role=role,
                         component=self.component, limit=self.limit)
            raise psycopg.OperationalError('local database budget exhausted')
        try:
            with self.lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
                active, peak = self.active, self.peak
            self.metrics('pool', 'admitted', role=role, component=self.component,
                         active=active, peak=peak, limit=self.limit,
                         elapsed_ms=round((time.monotonic() - started) * 1000, 3))
            yield
        finally:
            with self.lock:
                self.active -= 1
            self.slots.release()
