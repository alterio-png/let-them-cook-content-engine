"""Persistent local named-task adapter for the G1 outbox contract.

This private SQLite file is an internal local transport, not an end-user API or
a model of a cloud queue's retention/consistency guarantees. PostgreSQL remains
the recovery source of truth; task acknowledgements never replace job leases.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import stat
from uuid import UUID


class LocalQueue:
    def __init__(self, path):
        self.path = Path(path).absolute()
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for component in self.path.parent.parts[1:]:
                try:
                    os.mkdir(component, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=fd)
                os.close(fd)
                fd = child
            parent = os.fstat(fd)
        finally:
            os.close(fd)
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid()
                or stat.S_IMODE(parent.st_mode) & 0o077):
            raise ValueError("queue directory must be owned and private")
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            except FileExistsError:
                info = self.path.lstat()
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                        or stat.S_IMODE(info.st_mode) & 0o077):
                    raise ValueError("queue file must be owned, regular and private")
            else:
                os.close(fd)
        else:
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) & 0o077):
                raise ValueError("queue file must be owned, regular and private")
        with self._connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS named_tasks (
                event_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                acknowledged INTEGER NOT NULL DEFAULT 0 CHECK(acknowledged IN (0,1))
            )""")

    @contextmanager
    def _connection(self):
        # The parent directory is owner-only and is a trusted runtime setting.
        # SQLite sidecars inherit the database's 0600 mode.
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=15000")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _identifier(value):
        text = str(value)
        if str(UUID(text)) != text:
            raise ValueError("named task identifiers must be canonical UUIDs")
        return text

    def lookup(self, event_id):
        event = self._identifier(event_id)
        with self._connection() as db:
            row = db.execute("SELECT * FROM named_tasks WHERE event_id=?", (event,)).fetchone()
            return dict(row) if row else None

    def ensure_task(self, event_id, job_id):
        event, job = self._identifier(event_id), self._identifier(job_id)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM named_tasks WHERE event_id=?", (event,)).fetchone()
            if row is not None:
                if row["job_id"] != job:
                    raise ValueError("named task payload conflicts with existing task")
                return dict(row)
            db.execute("INSERT INTO named_tasks(event_id,job_id) VALUES (?,?)", (event, job))
            return {"event_id": event, "job_id": job, "acknowledged": 0}

    def pending(self, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("pending limit must be in [1,1000]")
        with self._connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM named_tasks WHERE acknowledged=0 ORDER BY event_id LIMIT ?", (limit,))]

    def ack(self, event_id):
        event = self._identifier(event_id)
        with self._connection() as db:
            return db.execute("UPDATE named_tasks SET acknowledged=1 WHERE event_id=?", (event,)).rowcount == 1
