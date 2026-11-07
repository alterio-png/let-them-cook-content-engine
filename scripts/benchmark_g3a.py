#!/usr/bin/env python3
"""Isolated local G3A capacity/recovery evidence; never a cloud load claim.

Uses the historical G1/G2 lab fixtures intentionally: all identities, source
video, captions, database rows and object directories belong to this run.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import platform
import resource
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / "integration", ROOT / "media_tests"):
    sys.path.insert(0, str(directory))

import psycopg
from psycopg.conninfo import conninfo_to_dict

from engine.cli import demo
from engine.control_plane import StaleLease
from engine.handoff import Dispatcher
from engine.local_queue import LocalQueue
from engine.media_pipeline import MediaWorker
from g1.support import connect
from g2.support import PipelineFixture


def percentile(values, quantile):
    """Linear interpolation on sorted actual observations, including endpoints."""
    if not values:
        return None
    if not 0 <= quantile <= 1:
        raise ValueError("quantile outside [0,1]")
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower, upper = math.floor(position), math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def distribution(values):
    return {"observations": len(values), "p50": percentile(values, .50),
            "p95": percentile(values, .95), "p99": percentile(values, .99)}


def require_isolated_dsn(dsn):
    if not dsn:
        raise ValueError("G1_TEST_DSN is required; PostgreSQL benchmarks never skip")
    fields = conninfo_to_dict(dsn)
    if fields.get("host") not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("benchmark requires an explicit loopback PostgreSQL host")
    if not fields.get("dbname", "").startswith("g1_"):
        raise ValueError("benchmark requires a disposable g1_* database")
    if (fields.get("service") or fields.get("hostaddr")
            or os.environ.get("PGSERVICE") or os.environ.get("PGHOSTADDR")):
        raise ValueError("service/hostaddr overrides are forbidden for this lab")
    return fields


def proc_tree_snapshot(root_pid):
    """Sample aggregate RSS of this process and descendants, including FFmpeg.

    RSS is summed, so shared pages can be counted more than once. A process
    shorter than the sampling interval can be missed. This excludes PostgreSQL
    in its separate Docker container and is not a cgroup memory measurement.
    """
    rows = {}
    for directory in Path("/proc").iterdir():
        if not directory.name.isdecimal():
            continue
        try:
            raw = (directory / "stat").read_text()
            end = raw.rindex(")")
            fields = raw[end + 2:].split()
            rows[int(directory.name)] = {
                "ppid": int(fields[1]), "name": raw[raw.index("(") + 1:end],
                "rss_bytes": int(fields[21]) * os.sysconf("SC_PAGE_SIZE"),
            }
        except (OSError, ValueError, IndexError):
            continue  # A process can exit between listing and reading /proc.
    included = {root_pid} if root_pid in rows else set()
    while True:
        discovered = {pid for pid, row in rows.items() if row["ppid"] in included}
        expanded = included | discovered
        if expanded == included:
            break
        included = expanded
    return {"rss_bytes": sum(rows[pid]["rss_bytes"] for pid in included),
            "processes": len(included),
            "ffmpeg_processes": sum(rows[pid]["name"] == "ffmpeg" for pid in included),
            "native_media_processes": sum(rows[pid]["name"] in {"ffmpeg", "ffprobe"}
                                          for pid in included)}


def cpu_snapshot():
    own = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return {"self_user_seconds": own.ru_utime, "self_system_seconds": own.ru_stime,
            "reaped_children_user_seconds": children.ru_utime,
            "reaped_children_system_seconds": children.ru_stime}


class Events:
    def __init__(self, path):
        self.handle = path.open("w", encoding="utf-8")
        self.lock = threading.Lock()

    def emit(self, event, **fields):
        with self.lock:
            self.handle.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(),
                                          "event": event, **fields}, default=str,
                                         sort_keys=True, allow_nan=False) + "\n")
            self.handle.flush()

    def close(self):
        self.handle.close()


class ResourceMonitor:
    def __init__(self, orgs, events, case, interval=.05):
        self.orgs, self.events, self.case, self.interval = list(orgs), events, case, interval
        self.stop = threading.Event()
        self.samples = 0
        self.peak_rss = self.peak_processes = self.peak_media = self.peak_ffmpeg = self.peak_active = 0
        self.tenant_peak = {str(org): 0 for org in orgs}
        self.error = None

    def __enter__(self):
        self.before, self.started = cpu_snapshot(), time.monotonic()
        self.thread = threading.Thread(target=self._sample, daemon=True)
        self.thread.start()
        return self

    def _sample(self):
        try:
            with psycopg.connect(os.environ["G1_TEST_DSN"], autocommit=True,
                                  connect_timeout=10) as db:
                db.execute("SET statement_timeout='10s'")
                while not self.stop.is_set():
                    snapshot = proc_tree_snapshot(os.getpid())
                    counts = db.execute("""SELECT organization_id,count(*) FROM public.render_jobs
                        WHERE organization_id=ANY(%s) AND state='running'
                        AND lease_expires_at>clock_timestamp() GROUP BY organization_id""",
                                        (self.orgs,)).fetchall()
                    active = {str(org): count for org, count in counts}
                    self.samples += 1
                    self.peak_rss = max(self.peak_rss, snapshot["rss_bytes"])
                    self.peak_processes = max(self.peak_processes, snapshot["processes"])
                    self.peak_media = max(self.peak_media, snapshot["native_media_processes"])
                    self.peak_ffmpeg = max(self.peak_ffmpeg, snapshot["ffmpeg_processes"])
                    self.peak_active = max(self.peak_active, sum(active.values()))
                    for org, count in active.items():
                        self.tenant_peak[org] = max(self.tenant_peak[org], count)
                    self.events.emit("resource_sample", case=self.case, **snapshot,
                                     active_jobs_by_org=active)
                    self.stop.wait(self.interval)
        except Exception as exc:
            self.error = type(exc).__name__

    def __exit__(self, *ignored):
        self.stop.set()
        self.thread.join(timeout=12)
        if self.thread.is_alive() or self.error:
            raise RuntimeError("resource sampler failed: " + str(self.error))
        after = cpu_snapshot()
        self.result = {"elapsed_seconds": time.monotonic() - self.started,
                       **{key: after[key] - value for key, value in self.before.items()},
                       "aggregate_process_rss_peak_bytes": self.peak_rss,
                       "process_count_peak": self.peak_processes,
                       "native_media_process_count_peak": self.peak_media,
                       "ffmpeg_process_count_peak": self.peak_ffmpeg,
                       "postgres_active_jobs_peak": self.peak_active,
                       "postgres_active_jobs_peak_by_org": self.tenant_peak,
                       "samples": self.samples, "sampling_interval_seconds": self.interval}


def verify_results(fixture, jobs, *, expected_attempts=None):
    """Verify actual immutable bytes and sole authoritative DB publication."""
    output_bytes = 0
    result_rows = []
    for job in jobs:
        row = fixture.row(job)
        if row["state"] != "completed":
            raise AssertionError(f"job {row['id']} is {row['state']}")
        manifest = row["output_manifest"]
        generation = f"{row['organization_id']}/{row['id']}/{row['fence']}"
        if manifest["generation"] != generation or len(manifest["outputs"]) != 3:
            raise AssertionError("invalid authoritative generation")
        for output in manifest["outputs"]:
            actual = fixture.store.stat(row["organization_id"], output["key"])
            if (actual["sha256"], actual["size_bytes"]) != (output["sha256"], output["size_bytes"]):
                raise AssertionError("published object hash/size mismatch")
            if not output["key"].startswith(generation + "/") or not output["has_audio"]:
                raise AssertionError("tenant/generation/audio contract failed")
            output_bytes += actual["size_bytes"]
        # G2 completion already read back and ffprobed every output. A completed
        # replay independently repeats this actual-media verification, without
        # claiming a new lease, attempt, object generation or reservation.
        repeated = fixture.worker().run(row["id"])
        if repeated["output_manifest"] != manifest or repeated["attempt_count"] != row["attempt_count"]:
            raise AssertionError("completed replay changed authoritative result")
        result_rows.append(row)
    orgs = [fixture.org_a, fixture.org_b]
    with connect() as db:
        counts = db.execute("""SELECT (SELECT count(*) FROM public.render_jobs WHERE organization_id=ANY(%s)) jobs,
            (SELECT count(*) FROM public.render_outbox WHERE organization_id=ANY(%s)) outbox,
            (SELECT count(*) FROM public.media_job_specs WHERE organization_id=ANY(%s)) specs,
            (SELECT count(*) FROM public.audit_events WHERE organization_id=ANY(%s)
              AND event_type='job_completed') completions,
            (SELECT sum(reserved_cents) FROM public.org_quotas WHERE organization_id=ANY(%s)) reserved,
            (SELECT sum(cost_cents) FROM public.render_jobs WHERE organization_id=ANY(%s)) requested""",
                            (orgs,) * 6).fetchone()
        io = db.execute("""SELECT coalesce(sum(read_bytes),0) AS read_bytes,
            coalesce(sum(written_bytes),0) AS written_bytes FROM public.media_attempt_metrics
            WHERE organization_id=ANY(%s)""", (orgs,)).fetchone()
        attempts = db.execute("""SELECT j.id,j.attempt_count,
            extract(epoch FROM min(a.started_at)-j.created_at) AS queue_seconds,
            extract(epoch FROM j.updated_at-j.created_at) AS end_to_end_seconds
            FROM public.render_jobs j JOIN public.render_attempts a ON a.job_id=j.id
            WHERE j.organization_id=ANY(%s) GROUP BY j.id""", (orgs,)).fetchall()
    if any(counts[key] != len(jobs) for key in ("jobs", "outbox", "specs", "completions")):
        raise AssertionError("job/outbox/spec/authoritative completion multiplication")
    if counts["reserved"] != counts["requested"]:
        raise AssertionError("reservation leak or duplication")
    if expected_attempts is not None and sum(row["attempt_count"] for row in attempts) != expected_attempts:
        raise AssertionError("unexpected attempt count")
    return {"completed": len(result_rows), "errors": 0,
            "retry_attempts": sum(row["attempt_count"] - 1 for row in attempts),
            "output_video_bytes": output_bytes, "attempt_io_bytes_read": int(io["read_bytes"]),
            "attempt_io_bytes_written": int(io["written_bytes"]),
            "application_reservation_units": int(counts["reserved"]),
            "queue_latency_seconds": distribution([float(row["queue_seconds"]) for row in attempts]),
            "end_to_end_latency_seconds": distribution([float(row["end_to_end_seconds"]) for row in attempts]),
            "one_job_outbox_spec_completion_each": True, "actual_object_hashes_verified": True,
            "completed_replay_ffprobe_verified": True, "reservation_equals_sum_immutable_requests": True,
            "artifacts": [{"job_id": str(row["id"]), "organization_id": str(row["organization_id"]),
                           "fence": row["fence"], "outputs": row["output_manifest"]["outputs"]}
                          for row in result_rows]}


def dispatch_all(fixture, queue):
    dispatcher = Dispatcher(fixture.dsn)
    count = 0
    while True:
        dispatched = dispatcher.dispatch_one(queue)
        if dispatched is None:
            return count
        if dispatched["state"] != "dispatched":
            raise AssertionError("unexpected handoff retry in normal benchmark")
        count += 1


def execute_named(fixture, queue, jobs, workers, events, case):
    lock = threading.Lock()
    active = peak = 0

    def execute(job):
        nonlocal active, peak
        claimed = False

        def hook(stage, claim):
            nonlocal active, peak, claimed
            if stage == "before_download":
                with lock:
                    active += 1
                    peak = max(peak, active)
                    claimed = True
                events.emit("job_claimed", case=case, job_id=claim["id"],
                            organization_id=claim["organization_id"], fence=claim["fence"])
        deadline = time.monotonic() + 900
        try:
            worker = fixture.worker(fault_hook=hook)
            while time.monotonic() < deadline:
                result = worker.run(job["id"])
                if result is None:
                    threading.Event().wait(.02)  # quota contention; no forced claim
                    continue
                if result["state"] != "completed":
                    raise AssertionError("normal cohort did not complete: " + result["state"])
                events.emit("job_completed", case=case, job_id=result["id"], fence=result["fence"])
                return result
            raise TimeoutError("local benchmark job deadline exceeded")
        finally:
            if claimed:
                with lock:
                    active -= 1

    tasks = queue.pending(limit=1000)
    if {task["job_id"] for task in tasks} != {str(job["id"]) for job in jobs}:
        raise AssertionError("durable queue differs from submitted jobs")
    # Jobs are submitted in alternating organizations. All are committed and
    # handed off before the bounded pool begins; this is a real queued cohort.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(execute, jobs))
    for task in tasks:
        queue.ack(task["event_id"])
    if queue.pending():
        raise AssertionError("completed local queue tasks were not acknowledged")
    return results, peak


def cohort(paths, number, workers, duration_ms, per_org, events):
    fixture = PipelineFixture(paths["video"], paths["caption"])
    case = f"queued_{number}_jobs"
    try:
        with connect() as db:
            db.execute("UPDATE public.org_quotas SET max_active_jobs=%s WHERE organization_id=ANY(%s)",
                       (per_org, [fixture.org_a, fixture.org_b]))
        assets = (fixture.ingest(), fixture.ingest(other=True))
        jobs = [fixture.submit_media(assets[index % 2], f"cohort-{number}-{index}",
                                     other=bool(index % 2), duration_ms=duration_ms)
                for index in range(number)]
        queue = LocalQueue(fixture.root / "queue.sqlite3")
        if dispatch_all(fixture, queue) != number:
            raise AssertionError("cohort handoff count mismatch")
        events.emit("cohort_queued", case=case, jobs=number, organizations=2,
                    global_workers=workers, per_org_max_active_jobs=per_org)
        with ResourceMonitor((fixture.org_a, fixture.org_b), events, case) as monitor:
            _, peak = execute_named(fixture, queue, jobs, workers, events, case)
        measured = verify_results(fixture, jobs, expected_attempts=number)
        resources = monitor.result
        if (peak > workers or resources["postgres_active_jobs_peak"] > workers
                or any(count > per_org for count in resources["postgres_active_jobs_peak_by_org"].values())):
            raise AssertionError("worker/organization concurrency cap exceeded")
        if resources["ffmpeg_process_count_peak"] == 0:
            raise AssertionError("resource sampler observed no actual FFmpeg subprocess")
        return {"case": case, "submitted_distinct": number, "organizations": 2,
                "global_worker_limit": workers, "claimed_worker_highwater": peak,
                "per_org_active_limit": per_org, "concurrency_caps_verified": True,
                "resources": resources, "throughput_jobs_per_second": number / resources["elapsed_seconds"],
                **measured, "local_unit_measurements": {
                    "cpu_seconds_per_completed_job": sum(resources[key] for key in (
                        "self_user_seconds", "self_system_seconds", "reaped_children_user_seconds",
                        "reaped_children_system_seconds")) / number,
                    "video_bytes_per_completed_job": measured["output_video_bytes"] / number,
                    "currency": None, "monetary_cost_per_job": None,
                    "price_status": "UNKNOWN_NO_PRICE_OR_CLOUD_BILLING_MEASURED"}}
    finally:
        fixture.cleanup()


def duplicates(paths, requests, duration_ms, events):
    fixture = PipelineFixture(paths["video"], paths["caption"])
    try:
        asset = fixture.ingest()
        barrier = threading.Barrier(requests)

        def submit(_):
            with connect("authenticated", fixture.user_a) as db:
                barrier.wait(timeout=45)
                return db.execute("SELECT * FROM public.submit_media_render(%s,'benchmark-duplicate',%s,%s,'smoke')",
                                  (fixture.org_a, asset["id"], duration_ms)).fetchone()
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=requests) as pool:
            submitted = list(pool.map(submit, range(requests)))
        elapsed = time.monotonic() - started
        if len({job["id"] for job in submitted}) != 1:
            raise AssertionError("duplicate requests multiplied logical jobs")
        queue = LocalQueue(fixture.root / "queue.sqlite3")
        if dispatch_all(fixture, queue) != 1:
            raise AssertionError("duplicate requests multiplied durable events")
        execute_named(fixture, queue, submitted[:1], 1, events, "duplicates")
        return {"case": "concurrent_duplicate_sql_submissions", "requests": requests,
                "submission_elapsed_seconds": elapsed, "boundary": "authenticated PostgreSQL function; not HTTP",
                **verify_results(fixture, submitted[:1], expected_attempts=1)}
    finally:
        fixture.cleanup()


def recoveries(paths, duration_ms, events):
    fixture = PipelineFixture(paths["video"], paths["caption"])
    try:
        asset = fixture.ingest()
        queue_job = fixture.submit_media(asset, "queue-accepted-response-lost", duration_ms=duration_ms)
        queue = LocalQueue(fixture.root / "queue.sqlite3")

        class AcceptedResponseLost:
            def lookup(self, event_id):
                return queue.lookup(event_id)

            def ensure_task(self, event_id, job_id):
                queue.ensure_task(event_id, job_id)
                raise TimeoutError("injected after durable local queue acceptance")

        dispatcher = Dispatcher(fixture.dsn)
        first = dispatcher.dispatch_one(AcceptedResponseLost())
        if first["state"] != "retry" or len(queue.pending()) != 1:
            raise AssertionError("accepted-lost queue failure did not preserve one task")
        with connect() as db:
            row = db.execute("SELECT available_at>clock_timestamp() AS backoff FROM public.render_outbox WHERE job_id=%s",
                             (queue_job["id"],)).fetchone()
            if not row["backoff"]:
                raise AssertionError("outbox retry omitted backoff")
            db.execute("UPDATE public.render_outbox SET available_at=clock_timestamp()-interval '1 second' WHERE job_id=%s",
                       (queue_job["id"],))
        if dispatcher.dispatch_one(queue)["state"] != "dispatched" or len(queue.pending()) != 1:
            raise AssertionError("named task reconciliation multiplied or lost task")
        fixture.worker().run(queue_job["id"])

        storage_job = fixture.submit_media(asset, "storage-transient", duration_ms=duration_ms)

        class FailOutputOnce:
            def __init__(self):
                self.failed = False

            def __getattr__(self, name):
                return getattr(fixture.store, name)

            def put_file(self, org, key, source):
                if not self.failed:
                    self.failed = True
                    raise OSError("injected local object write failure")
                return fixture.store.put_file(org, key, source)

        faulty = MediaWorker(fixture.dsn, FailOutputOnce(), fixture.scratch)
        failed = faulty.run(storage_job["id"])
        if failed["state"] != "queued":
            raise AssertionError("storage error was not retryable")
        with connect() as db:
            if not db.execute("SELECT available_at>clock_timestamp() AS backoff FROM public.render_jobs WHERE id=%s",
                              (storage_job["id"],)).fetchone()["backoff"]:
                raise AssertionError("storage retry omitted backoff")
        fixture.ready(storage_job)
        fixture.worker().run(storage_job["id"])

        worker_job = fixture.submit_media(asset, "worker-after-object", duration_ms=duration_ms)
        old_claim = {}

        def fault(stage, claim):
            if stage == "after_object" and not old_claim:
                old_claim.update(claim)
                raise OSError("injected worker exception after durable object; not SIGKILL")

        worker = fixture.worker(fault_hook=fault)
        if worker.run(worker_job["id"])["state"] != "queued" or not old_claim:
            raise AssertionError("worker checkpoint failure was not recovered")
        fixture.ready(worker_job)
        authoritative = fixture.worker().run(worker_job["id"])
        try:
            worker.control.finish_job(old_claim["id"], old_claim["lease_token"], old_claim["fence"],
                                      authoritative["output_manifest"])
        except StaleLease:
            pass
        else:
            raise AssertionError("obsolete fenced worker replaced authoritative result")
        if fixture.row(worker_job)["output_manifest"] != authoritative["output_manifest"]:
            raise AssertionError("stale completion changed manifest")
        orphans = fixture.worker().reconcile_orphans(fixture.org_a, worker_job["id"], delete=True)
        if not orphans["objects"]:
            raise AssertionError("no obsolete durable object was reconciled")
        if fixture.worker().reconcile_orphans(fixture.org_a, worker_job["id"], delete=True)["objects"]:
            raise AssertionError("orphan reconciliation was not idempotent")
        measured = verify_results(fixture, [queue_job, storage_job, worker_job], expected_attempts=5)
        events.emit("recovery_cases_verified", queue="accepted-response-lost", storage="write-error-once",
                    worker="exception-after-object", obsolete_fence_rejected=True,
                    orphan_objects_deleted=len(orphans["objects"]))
        return {"case": "controlled_local_recovery", **measured,
                "queue_acceptance_response_lost_reconciled": True,
                "queue_named_tasks_after_retry": 1, "queue_backoff_verified": True,
                "storage_write_error_once_recovered": True, "storage_backoff_verified": True,
                "worker_exception_after_durable_object_recovered": True,
                "obsolete_fence_completion_rejected": True,
                "orphan_objects_deleted": len(orphans["objects"]),
                "orphan_reconciliation_idempotent": True,
                "retry_clock": "available_at moved into past after verifying real backoff; accelerated laboratory scheduling",
                "hard_kill_status": "NOT_RUN_HERE; separate unchanged G2 SIGKILL integration tests required"}
    finally:
        fixture.cleanup()


def run_benchmark(output, *, jobs=(20, 50), workers=2, duration_ms=300,
                  per_org=1, duplicate_requests=100, include_recovery=True):
    require_isolated_dsn(os.environ.get("G1_TEST_DSN"))
    if (not jobs or any(type(number) is not int or not 1 <= number <= 100 for number in jobs)
            or not 1 <= workers <= 2 or not 1 <= per_org <= 2
            or not 300 <= duration_ms <= 500 or not 2 <= duplicate_requests <= 100):
        raise ValueError("bounded lab limits: jobs 1..100, workers/per-org 1..2, duration 300..500ms, duplicates 2..100")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    events = Events(output / "events.jsonl")
    started = datetime.now(timezone.utc).isoformat()
    report = {"schema_version": 1, "scope": "REAL_LOCAL_POSTGRESQL_AND_FFMPEG; NOT_CLOUD_LOAD",
              "started_at": started, "status": "RUNNING", "cases": []}
    try:
        with connect() as db:
            if db.execute("SELECT count(*) AS n FROM public.render_jobs").fetchone()["n"]:
                raise RuntimeError("benchmark requires an idle disposable database without existing jobs")
            postgres = db.execute("SELECT version() AS version").fetchone()["version"]
        report["runtime"] = {"python": platform.python_version(), "platform": platform.platform(),
                             "logical_cpus": os.cpu_count(), "postgres": postgres,
                             "source_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                             "ffmpeg": subprocess.check_output(["ffmpeg", "-version"], text=True).splitlines()[0],
                             "ffprobe": subprocess.check_output(["ffprobe", "-version"], text=True).splitlines()[0]}
        with tempfile.TemporaryDirectory(prefix="g3a-benchmark-") as source:
            paths = demo(Path(source), "synthetic")
            report["cases"].append(duplicates(paths, duplicate_requests, duration_ms, events))
            for number in jobs:
                report["cases"].append(cohort(paths, number, workers, duration_ms, per_org, events))
            if include_recovery:
                report["cases"].append(recoveries(paths, duration_ms, events))
        with connect() as db:
            if db.execute("SELECT count(*) AS n FROM public.render_jobs").fetchone()["n"]:
                raise AssertionError("benchmark-owned rows were not cleaned")
        report.update(status="PASS_LOCAL_ONLY", own_rows_and_temporary_files_cleaned=True,
                      cloud_services="NOT_VALIDATED", monetary_cost="UNKNOWN",
                      measurement_bounds="CPU self plus reaped children; sampled summed RSS includes native media descendants, excludes separate PostgreSQL container and kernel/cgroup overhead; short-lived process peaks may be missed",
                      reservation_note="G1 cost_cents is an existing synthetic admission reservation, not measured monetary cost")
    except BaseException as exc:
        report.update(status="FAILED", error_type=type(exc).__name__)
        raise
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        events.emit("benchmark_finished", status=report["status"])
        events.close()
        (output / "summary.json").write_text(json.dumps(report, indent=2, sort_keys=True,
                                                       allow_nan=False) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", default="20,50", help="queued distinct-job cohorts, default 20,50")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--duration-ms", type=int, default=300)
    parser.add_argument("--per-org", type=int, default=1)
    args = parser.parse_args()
    report = run_benchmark(args.output, jobs=tuple(int(n) for n in args.jobs.split(",")),
                           workers=args.workers, duration_ms=args.duration_ms, per_org=args.per_org)
    print(json.dumps({"status": report["status"], "summary": str(args.output / "summary.json"),
                      "events": str(args.output / "events.jsonl")}, sort_keys=True))


if __name__ == "__main__":
    main()
