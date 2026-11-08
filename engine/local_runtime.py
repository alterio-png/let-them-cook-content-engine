"""Bounded local-only ingest/outbox/render daemon and laboratory sampler.

PostgreSQL owns every durable signal and job. This module creates no hosted
resource and never runs FFmpeg in a request handler. The render role is the
historical trusted engine_worker; ingestion has a separate narrow role.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import signal
import stat
import threading
import time
from uuid import UUID

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from .control_plane import ControlPlane, StaleLease
from .handoff import Dispatcher
from .media_pipeline import MediaWorker
from .media_ingest import inventory_inputs
from .object_store import LocalObjectStore


def local_dsn(dsn):
    """Reject remote targets and libpq overrides before opening a connection."""
    info = conninfo_to_dict(dsn)
    if (info.get("host") not in {"127.0.0.1", "localhost", "::1"}
            or not info.get("dbname", "").startswith("g3a_auth_")
            or info.get("service") or info.get("hostaddr")
            or os.environ.get("PGSERVICE") or os.environ.get("PGHOSTADDR")):
        raise ValueError("local runtime requires its disposable loopback Auth database")
    return dsn


def private_json(path):
    """Read private configuration without following a final symlink."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077 or info.st_size > 65536):
            raise ValueError("runtime configuration must be a private owned bounded file")
        value = json.loads(handle.read())
    if not isinstance(value, dict):
        raise ValueError("runtime configuration must be an object")
    return value


def event(path, stage, outcome, **fields):
    """Best-effort diagnostics must never block/fail already committed work."""
    try:
        written = _write_event(path, stage, outcome, **fields)
    except (OSError, ValueError, TypeError):
        written = False
    if not written:
        event.dropped += 1
    return written


event.dropped = 0


def _write_event(path, stage, outcome, **fields):
    value = {"at": datetime.now(timezone.utc).isoformat(), "pid": os.getpid(),
             "stage": stage, "outcome": outcome, **fields}
    payload = json.dumps(value, sort_keys=True, default=str, allow_nan=False) + "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as out:
        fcntl.flock(out.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        info = os.fstat(out.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("telemetry requires a private owned regular file")
        # Observability cannot exhaust the operator's disk during a long-lived
        # manual session. A fresh laboratory is required after this cap.
        if info.st_size + len(payload.encode("utf-8")) > 32 * 1024 * 1024:
            return False
        out.write(payload)
    return True


class PostgresNamedQueue:
    """Durable local named wake-up adapter, never an upload/receipt authority."""

    def __init__(self, dsn):
        self.control = ControlPlane(local_dsn(dsn))

    @staticmethod
    def _id(value):
        text = str(value)
        if str(UUID(text)) != text:
            raise ValueError("queue requires canonical identifiers")
        return text

    def lookup(self, event_id):
        with self.control.transaction() as db:
            return db.execute("SELECT event_id,job_id,acknowledged FROM g3br0_private.local_wakeups WHERE event_id=%s",
                              (self._id(event_id),)).fetchone()

    def ensure_task(self, event_id, job_id):
        event_id, job_id = self._id(event_id), self._id(job_id)
        with self.control.transaction() as db:
            identity = db.execute("SELECT id FROM public.render_outbox WHERE id=%s AND job_id=%s FOR SHARE",
                                  (event_id, job_id)).fetchone()
            if not identity:
                raise ValueError("wake-up must match a durable outbox event")
            db.execute("INSERT INTO g3br0_private.local_wakeups(event_id,job_id) VALUES(%s,%s) ON CONFLICT DO NOTHING",
                       (event_id, job_id))
            row = db.execute("SELECT event_id,job_id,acknowledged FROM g3br0_private.local_wakeups WHERE event_id=%s",
                             (event_id,)).fetchone()
            if str(row["job_id"]) != job_id:
                raise ValueError("wake-up payload conflicts with its immutable event")
            return row

    def pending(self, limit=16):
        if type(limit) is not int or not 1 <= limit <= 64:
            raise ValueError("bounded local pending limit required")
        with self.control.transaction() as db:
            return db.execute("SELECT event_id,job_id,acknowledged FROM g3br0_private.local_wakeups "
                              "WHERE NOT acknowledged ORDER BY created_at,event_id LIMIT %s", (limit,)).fetchall()

    def ack(self, event_id):
        with self.control.transaction() as db:
            return db.execute("UPDATE g3br0_private.local_wakeups SET acknowledged=true WHERE event_id=%s",
                              (self._id(event_id),)).rowcount == 1


def run_worker(config, *, stop=None, once=False):
    """One supervised process, serial ingest and one physical media worker."""
    from .local_ingest import LocalIngestWorker

    stop = stop or threading.Event()
    dsn, ingest_dsn = local_dsn(config["worker_dsn"]), local_dsn(config["ingest_dsn"])
    root = Path(config["store_root"])
    events = Path(config["events_path"])
    store = LocalObjectStore(root)
    ingester = LocalIngestWorker(ingest_dsn, store, scratch_root=Path(config["ingest_scratch"]))
    queue, dispatcher = PostgresNamedQueue(dsn), Dispatcher(dsn)
    worker = MediaWorker(dsn, store, Path(config["scratch_root"]), lease_seconds=5)
    poll = config.get("poll_seconds", 0.25)
    if not isinstance(poll, (int, float)) or not 0.05 <= poll <= 2:
        raise ValueError("bounded runtime polling interval required")
    event(events, "runtime", "started", render_concurrency=1)
    while not stop.is_set():
        active = False
        started = time.monotonic()
        try:
            ingest = ingester.process_one()
            if ingest is not None:
                active = True
                event(events, "ingest", str(ingest.get("state", "observed")),
                      ingestion_id=ingest.get("id"), error_code=ingest.get("error_code"),
                      elapsed_ms=round((time.monotonic() - started) * 1000))
            dispatched = dispatcher.dispatch_one(queue)
            if dispatched:
                active = True
                event(events, "outbox", dispatched["state"], event_id=dispatched["event_id"])
            pending = queue.pending(1)
            task = pending[0] if pending else None
            if task:
                durable = worker.control.get_job(str(task["job_id"]))
                if durable and durable["state"] in {"completed", "failed"}:
                    # Covers a crash after the fenced commit but before task
                    # acknowledgement; this old signal cannot block the queue.
                    queue.ack(task["event_id"])
                    task = None
            render_started = time.monotonic()
            # A durable sweep is mandatory after crash/lease expiry even when
            # a queue signal has already been acknowledged. Both paths use G2.
            rendered = worker.run(str(task["job_id"])) if task else worker.run()
            if rendered is None and task:
                # An unavailable retry/live lease at the queue head must not
                # starve later durable work. PostgreSQL still claims/fences it.
                rendered = worker.run()
            if rendered is not None:
                active = True
                event(events, "worker", rendered["state"], job_id=rendered["id"],
                      fence=rendered["fence"], elapsed_ms=round((time.monotonic() - render_started) * 1000))
                if (task and str(rendered["id"]) == str(task["job_id"])
                        and rendered["state"] in {"completed", "failed"}):
                    queue.ack(task["event_id"])
        except (StaleLease, psycopg.Error) as error:
            event(events, "runtime", "retry", error_code=type(error).__name__)
        except Exception as error:
            # Neither exception text nor DSNs/native stderr enter telemetry.
            event(events, "runtime", "blocked", error_code=type(error).__name__)
        if once:
            return active
        stop.wait(poll if not active else 0.05)
    event(events, "runtime", "stopped")


def _descendants(parent):
    """Read Linux process metadata; no command lines or credentials are logged."""
    rows = {}
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        try:
            fields = (path / "stat").read_text().rsplit(") ", 1)[1].split()
            rows[int(path.name)] = {"parent": int(fields[1]), "user_ticks": int(fields[11]),
                                    "system_ticks": int(fields[12]), "rss_pages": int(fields[21])}
        except (OSError, ValueError, IndexError):
            continue
    found, frontier = set(), {parent}
    while frontier:
        found.update(frontier)
        frontier = {pid for pid, data in rows.items() if data["parent"] in frontier} - found
    return {pid: data for pid, data in rows.items() if pid in found}


def sample_resources(config, *, stop=None, once=False):
    stop = stop or threading.Event()
    dsn = local_dsn(config["monitor_dsn"])
    path, parent = Path(config["events_path"]), int(config["parent_pid"])
    interval = config.get("interval_seconds", 0.25)
    if not isinstance(interval, (int, float)) or not 0.1 <= interval <= 2:
        raise ValueError("bounded monitoring interval required")
    page = os.sysconf("SC_PAGE_SIZE")
    ticks = os.sysconf("SC_CLK_TCK")
    while not stop.is_set():
        started = time.monotonic()
        try:
            with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=3) as db:
                db.execute("SET LOCAL statement_timeout='2s'")
                db.execute("SET LOCAL lock_timeout='1s'")
                counts = db.execute("SELECT usename,count(*) AS connections,"
                                    "count(*) FILTER(WHERE state='active') AS active,"
                                    "count(*) FILTER(WHERE wait_event_type='Lock') AS lock_waiters "
                                    "FROM pg_stat_activity WHERE backend_type='client backend' "
                                    "GROUP BY usename ORDER BY usename").fetchall()
                limits = db.execute("SELECT setting::integer AS max_connections FROM pg_settings "
                                    "WHERE name='max_connections'").fetchone()
            processes = _descendants(parent)
            event(path, "resources", "sample", postgres_clients=counts,
                  postgres_client_total=sum(r["connections"] for r in counts),
                  postgres_max_connections=limits["max_connections"],
                  descendant_process_count=len(processes),
                  descendant_rss_bytes=sum(r["rss_pages"] for r in processes.values()) * page,
                  descendant_cpu_seconds=sum(r["user_ticks"] + r["system_ticks"] for r in processes.values()) / ticks,
                  sample_elapsed_ms=round((time.monotonic() - started) * 1000),
                  measurement_scope="all dedicated-cluster client backends; parent/descendant Linux RSS and live CPU; PG/Auth container CPU/RSS excluded")
        except Exception as error:
            event(path, "resources", "unavailable", error_code=type(error).__name__)
        if once:
            return
        stop.wait(interval)


def reconcile(config):
    """Report obsolete generations and unreferenced inputs; never delete/refund.

    An input being published by live ingestion may be temporarily unreferenced.
    The report is inventory for the operator, not permission for garbage
    collection. Private quarantine objects remain governed by their SQL ledger.
    """
    worker = MediaWorker(local_dsn(config["worker_dsn"]), LocalObjectStore(config["store_root"]),
                         Path(config["scratch_root"]), lease_seconds=5)
    with worker.control.transaction() as db:
        jobs = db.execute("SELECT id,organization_id,state FROM public.render_jobs ORDER BY id LIMIT 1001").fetchall()
        organizations = db.execute("SELECT DISTINCT organization_id FROM public.assets ORDER BY organization_id LIMIT 1001").fetchall()
    if len(jobs) > 1000 or len(organizations) > 1000:
        raise ValueError("local reconciliation inventory exceeds its explicit bounded scan")
    outputs = [{"job_id": str(row["id"]), "organization_id": str(row["organization_id"]),
                **worker.reconcile_orphans(row["organization_id"], row["id"], delete=False)} for row in jobs]
    inputs = []
    with worker.control.transaction() as db:
        for row in organizations:
            org = row["organization_id"]
            inputs.append({"organization_id": str(org), "unreferenced_inputs": inventory_inputs(db, worker.store, org)})
    return {"deleted": False, "refunded": False, "outputs": outputs, "inputs": inputs,
            "limitation": "unreferenced may include live unpublished ingestion; never automatic garbage collection"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("worker", "monitor", "reconcile"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    config = private_json(args.config)
    if args.mode == "reconcile":
        print(json.dumps(reconcile(config), sort_keys=True, default=str))
        return
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    (run_worker if args.mode == "worker" else sample_resources)(config, stop=stop, once=args.once)


if __name__ == "__main__":
    main()
