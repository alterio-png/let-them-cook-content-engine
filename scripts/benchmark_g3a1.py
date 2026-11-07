#!/usr/bin/env python3
"""Real, bounded, local PostgreSQL + two-process HTTP upload evidence.

This laboratory workload creates no cloud resources. Its synthetic JWT issuer
is a LOCAL_CRYPTO_CONTRACT fixture; official Auth validation is a separate gate.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / "integration", ROOT / "media_tests", ROOT / "upload_tests"):
    sys.path.insert(0, str(directory))

from g1.support import connect
from scripts.benchmark_g3a import (Events, cpu_snapshot, distribution,
                                   proc_tree_snapshot, require_isolated_dsn)


def summarize_http(observations, operations):
    """Keep physical HTTP attempts distinct from logical idempotent operations."""
    return {
        "logical_operations": len(operations),
        "http_attempts": len(observations),
        "http_status_counts": dict(sorted(Counter(str(row["status"]) for row in observations).items())),
        "initial_status_counts": dict(sorted(Counter(str(row["initial_status"]) for row in operations).items())),
        "final_status_counts": dict(sorted(Counter(str(row["status"]) for row in operations).items())),
        "backpressure_retries": sum(row["attempts"] - 1 for row in operations),
        "http_attempt_latency_seconds": distribution([row["elapsed_seconds"] for row in observations]),
        "logical_operation_latency_seconds": distribution([row["elapsed_seconds"] for row in operations]),
    }


def quota_snapshot(org):
    """Read actual rows with the lab admin connection; never the API identity."""
    with connect() as db:
        row = db.execute("""SELECT l.used_count,l.used_bytes,l.max_count,l.max_bytes,
            (SELECT count(*) FROM g3a1_private.upload_reservations r
             WHERE r.organization_id=l.organization_id AND r.state<>'expired') AS charged_rows,
            (SELECT coalesce(sum(r.size_bytes),0) FROM g3a1_private.upload_reservations r
             WHERE r.organization_id=l.organization_id AND r.state<>'expired') AS charged_bytes,
            (SELECT count(*) FROM g3a1_private.upload_receipts p
             WHERE p.organization_id=l.organization_id) AS receipts
            FROM g3a1_private.upload_limits l WHERE l.organization_id=%s""", (org,)).fetchone()
    if row is None:
        raise AssertionError("benchmark quota row missing")
    return {key: int(value) for key, value in row.items()}


def verify_quota(snapshot, *, expected_count, expected_bytes, expected_receipts=0):
    if not (snapshot["used_count"] == snapshot["charged_rows"] == expected_count
            and snapshot["used_bytes"] == snapshot["charged_bytes"] == expected_bytes
            and snapshot["receipts"] == expected_receipts
            and 0 <= snapshot["used_count"] <= snapshot["max_count"]
            and 0 <= snapshot["used_bytes"] <= snapshot["max_bytes"]):
        raise AssertionError("actual PostgreSQL quota/reservation/receipt invariant failed")


def verify_cleanup(fixture):
    """Do not infer successful cleanup merely from a fixture method returning."""
    orgs = [fixture.f.org_a, fixture.f.org_b]
    with connect() as db:
        remaining = db.execute("""SELECT
            (SELECT count(*) FROM g3a1_private.upload_limits WHERE organization_id=ANY(%s)) +
            (SELECT count(*) FROM g3a1_private.upload_reservations WHERE organization_id=ANY(%s)) +
            (SELECT count(*) FROM g3a1_private.upload_receipts WHERE organization_id=ANY(%s)) +
            (SELECT count(*) FROM g3a1_private.upload_events WHERE organization_id=ANY(%s)) +
            (SELECT count(*) FROM public.organizations WHERE id=ANY(%s)) AS rows,
            EXISTS(SELECT 1 FROM pg_roles WHERE rolname=%s) AS login_exists,
            (SELECT count(*) FROM auth.users WHERE id=ANY(%s)) AS users""",
            (orgs, orgs, orgs, orgs, orgs, fixture.login,
             [fixture.f.user_a, fixture.f.user_b, fixture.user_c])).fetchone()
    if remaining["rows"] or remaining["users"] or remaining["login_exists"] or Path(fixture.root).exists():
        raise AssertionError("benchmark-owned database rows, role or private files remain")


class ResourceMonitor:
    """CPU includes servers only once reaped; RSS samples live descendants."""
    def __init__(self, events, interval=.05):
        self.events, self.interval = events, interval
        self.stop = threading.Event()
        self.samples = self.peak_rss = self.peak_processes = 0
        self.error = None

    def __enter__(self):
        self.before, self.started = cpu_snapshot(), time.monotonic()
        self.thread = threading.Thread(target=self._sample, daemon=True)
        self.thread.start()
        return self

    def _sample(self):
        try:
            while not self.stop.is_set():
                snapshot = proc_tree_snapshot(os.getpid())
                self.samples += 1
                self.peak_rss = max(self.peak_rss, snapshot["rss_bytes"])
                self.peak_processes = max(self.peak_processes, snapshot["processes"])
                self.events.emit("resource_sample", **snapshot)
                self.stop.wait(self.interval)
        except Exception as exc:
            self.error = type(exc).__name__

    def __exit__(self, *ignored):
        self.stop.set()
        self.thread.join(timeout=5)
        if self.thread.is_alive() or self.error:
            raise RuntimeError("resource sampler failed: " + str(self.error))
        after = cpu_snapshot()
        self.result = {"elapsed_seconds": time.monotonic() - self.started,
                       **{key: after[key] - value for key, value in self.before.items()},
                       "aggregate_process_rss_peak_bytes": self.peak_rss,
                       "process_count_peak": self.peak_processes,
                       "samples": self.samples,
                       "sampling_interval_seconds": self.interval}


class HTTPWorkload:
    def __init__(self, events, case, *, max_attempts=16):
        self.events, self.case, self.max_attempts = events, case, max_attempts
        self.observations = []
        self.operations = []
        self.lock = threading.Lock()

    def call(self, replica, method, path, body=None, *, user=None, operation=0):
        started = time.monotonic()
        first = None
        for attempt in range(1, self.max_attempts + 1):
            attempt_started = time.monotonic()
            status, payload = replica.call(method, path, body, user=user)
            first = status if first is None else first
            observation = {"status": status, "elapsed_seconds": time.monotonic() - attempt_started}
            with self.lock:
                self.observations.append(observation)
            # No token, URL, body, DSN, identity or provider exception is logged.
            self.events.emit("http_attempt", case=self.case, operation=operation,
                             attempt=attempt, replica_pid=replica.pid, **observation)
            if status != 503:
                result = {"status": status, "payload": payload, "attempts": attempt,
                          "initial_status": first, "elapsed_seconds": time.monotonic() - started}
                with self.lock:
                    self.operations.append(result)
                return result
            if attempt < self.max_attempts:
                # Fixed bounded staggering makes backpressure visible and avoids
                # turning the benchmark into an unbounded immediate retry loop.
                time.sleep(min(.025 * 2 ** (attempt - 1), .25) + (operation % 7) * .003)
        raise AssertionError("bounded HTTP backpressure retries exhausted")


def concurrent_reservations(fixture, replicas, *, count, org, user, keys, payload, events, case):
    digest = hashlib.sha256(payload).hexdigest()
    barrier = threading.Barrier(count)
    workload = HTTPWorkload(events, case)

    def submit(number):
        barrier.wait(timeout=30)
        return workload.call(replicas[number % len(replicas)], "POST", f"/v1/orgs/{org}/uploads",
                             {"request_key": keys[number], "size_bytes": len(payload), "sha256": digest},
                             user=user, operation=number)

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=count) as executor:
        operations = list(executor.map(submit, range(count)))
    return operations, {"case": case, "elapsed_seconds": time.monotonic() - started,
                        "simultaneous_barrier_participants": count,
                        **summarize_http(workload.observations, operations)}


def _grant_identity(payload):
    # The deterministic capability itself is never persisted in evidence.
    return payload["id"], payload["expires_at"], payload["upload_path"]


def run_benchmark(output, *, duplicate_requests=100, distinct_requests=20, quota_slots=10):
    require_isolated_dsn(os.environ.get("G1_TEST_DSN"))
    if (type(duplicate_requests) is not int or not 2 <= duplicate_requests <= 100
            or type(distinct_requests) is not int or not 2 <= distinct_requests <= 20
            or type(quota_slots) is not int or not 1 <= quota_slots < distinct_requests):
        raise ValueError("bounded lab limits: duplicates2..100, distinct2..20, 1<=slots<distinct")
    # Imported after validating the target: constructing fixtures mutates only a
    # disposable local database and private laboratory object directories.
    from g3a1.support import SharedHTTPFixture

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    events = Events(output / "events.jsonl")
    report = {"schema_version": 1, "status": "RUNNING", "cases": [],
              "scope": "REAL_LOCAL_POSTGRESQL_TWO_HTTP_PROCESSES_AND_LOCAL_OBJECT_STORE",
              "authentication": "LOCAL_CRYPTO_CONTRACT; official Auth integration is a separate gate",
              "started_at": datetime.now(timezone.utc).isoformat()}
    fixture = None
    try:
        with connect() as db:
            postgres = db.execute("SELECT version() AS version").fetchone()["version"]
        report["runtime"] = {"python": platform.python_version(), "platform": platform.platform(),
                             "logical_cpus": os.cpu_count(), "postgres": postgres,
                             "source_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                             "source_worktree_clean": not bool(subprocess.check_output(
                                 ["git", "status", "--porcelain", "--untracked-files=all"], cwd=ROOT, text=True).strip())}
        with ResourceMonitor(events) as monitor:
            try:
                fixture = SharedHTTPFixture()
                fixture.provision_quota(org=fixture.f.org_a, max_uploads=quota_slots, max_bytes=quota_slots * 128, ttl_seconds=300)
                fixture.provision_quota(org=fixture.f.org_b, max_uploads=quota_slots, max_bytes=quota_slots * 128, ttl_seconds=300)
                a, b = fixture.new_replica(), fixture.new_replica()
                if a.pid == b.pid or a.pid == os.getpid() or b.pid == os.getpid():
                    raise AssertionError("two independent HTTP operating-system processes required")
                report["replicas"] = {"initial_pids": [a.pid, b.pid], "api_login": "restricted_nonowner",
                                      "postgres_authority_backend": "PostgresUploadAuthority",
                                      "database_connections_max_per_replica": 4}
                payload = b"G3A.1 synthetic upload evidence; no media or customer data."
                payload = (payload * 3)[:128]
                duplicate_key = "benchmark-shared-duplicate"
                duplicates, duplicate_report = concurrent_reservations(
                    fixture, (a, b), count=duplicate_requests, org=fixture.f.org_a, user=fixture.f.user_a,
                    keys=[duplicate_key] * duplicate_requests, payload=payload, events=events, case="same_key")
                if any(row["status"] != 201 for row in duplicates):
                    raise AssertionError("every duplicate must recover the same accepted reservation")
                identities = {_grant_identity(row["payload"]) for row in duplicates}
                if len(identities) != 1:
                    raise AssertionError("duplicate HTTP operations created distinct grants, expiry or capability")
                snapshot = quota_snapshot(fixture.f.org_a)
                verify_quota(snapshot, expected_count=1, expected_bytes=len(payload))
                duplicate_report.update(one_grant_key_expiry_and_capability=True, actual_postgres_counters=snapshot)
                report["cases"].append(duplicate_report)

                distinct, distinct_report = concurrent_reservations(
                    fixture, (a, b), count=distinct_requests, org=fixture.f.org_b, user=fixture.f.user_b,
                    keys=[f"benchmark-distinct-{n}" for n in range(distinct_requests)], payload=payload,
                    events=events, case="distinct_keys_quota_race")
                counts = Counter(row["status"] for row in distinct)
                if counts != {201: quota_slots, 429: distinct_requests - quota_slots}:
                    raise AssertionError("actual HTTP quota race admitted an unexpected number of grants")
                snapshot = quota_snapshot(fixture.f.org_b)
                verify_quota(snapshot, expected_count=quota_slots, expected_bytes=quota_slots * len(payload))
                distinct_report.update(accepted=quota_slots, quota_denied=distinct_requests - quota_slots,
                                       actual_postgres_counters=snapshot,
                                       final_committed_quota_matches_live_reservations=True)
                report["cases"].append(distinct_report)

                grant = duplicates[0]["payload"]
                io = HTTPWorkload(events, "publish_cross_replica_and_restart")
                published = io.call(a, "PUT", grant["upload_path"], payload, user=fixture.f.user_a)
                if published["status"] not in {200, 201}:
                    raise AssertionError("real HTTP upload did not persist a receipt")
                receipt_path = f"/v1/orgs/{fixture.f.org_a}/uploads/{grant['id']}"
                through_b = io.call(b, "GET", receipt_path, user=fixture.f.user_a)
                if through_b["status"] != 200:
                    raise AssertionError("other live replica cannot read uploaded receipt")
                expected_hash = hashlib.sha256(payload).hexdigest()
                if (through_b["payload"]["sha256"], through_b["payload"]["size_bytes"]) != (expected_hash, len(payload)):
                    raise AssertionError("receipt does not match actual uploaded bytes")
                with connect() as db:
                    persisted = db.execute("""SELECT object_key,sha256,size_bytes FROM g3a1_private.upload_receipts
                        WHERE id=%s AND organization_id=%s""", (grant["id"], fixture.f.org_a)).fetchone()
                from engine.object_store import LocalObjectStore
                actual = LocalObjectStore(fixture.store_root).stat(str(fixture.f.org_a), persisted["object_key"])
                if (actual["sha256"], actual["size_bytes"]) != (expected_hash, len(payload)):
                    raise AssertionError("persistent private object readback failed")
                # Hard termination occurs AFTER the valid receipt, deliberately.
                # Mid-publication SIGKILL/fencing belongs to the fault suite.
                old_pid = a.pid
                a.stop(kill=True)
                c = fixture.new_replica()
                if c.pid in {old_pid, b.pid, os.getpid()}:
                    raise AssertionError("replica restart did not create a fresh process")
                through_c = io.call(c, "GET", receipt_path, user=fixture.f.user_a)
                replay = io.call(c, "POST", f"/v1/orgs/{fixture.f.org_a}/uploads",
                                 {"request_key": duplicate_key, "size_bytes": len(payload), "sha256": expected_hash},
                                 user=fixture.f.user_a)
                if (through_c["status"] != 200 or through_c["payload"] != through_b["payload"]
                        or replay["status"] != 201 or _grant_identity(replay["payload"]) != _grant_identity(grant)):
                    raise AssertionError("fresh process lost or changed shared grant/receipt")
                denied = io.call(c, "GET", receipt_path, user=fixture.f.user_b)
                if denied["status"] not in {403, 404, 409}:
                    raise AssertionError("cross-organization receipt lookup was not denied")
                snapshot = quota_snapshot(fixture.f.org_a)
                verify_quota(snapshot, expected_count=1, expected_bytes=len(payload), expected_receipts=1)
                workdirs = [str(replica.work_dir) for replica in (a, b, c)]
                if len(set(workdirs)) != 3:
                    raise AssertionError("each replica must have an independent private working directory")
                ledger_files = [path for path in Path(fixture.root).rglob("*")
                                if path.is_file() and (path.suffix in {".sqlite", ".sqlite3", ".db"}
                                                      or "sqlite" in path.name.lower())]
                if ledger_files:
                    raise AssertionError("shared authority created an unexpected local ledger file")
                report["replicas"].update(restarted_pid=c.pid, private_workdirs_distinct=True,
                                          sqlite_ledger_files_created=0)
                report["cases"].append({"case": "publish_cross_replica_and_restart",
                    "uploaded_on_a_receipt_on_b": True, "hard_termination_after_valid_receipt": True,
                    "fresh_process_recovers_identical_grant_and_receipt": True,
                    "actual_private_object_hash_and_size_verified": True,
                    "sha256": expected_hash, "size_bytes": len(payload),
                    "cross_organization_receipt_denied": True,
                    "actual_postgres_counters": snapshot,
                    **summarize_http(io.observations, io.operations)})
            finally:
                if fixture is not None:
                    fixture.cleanup()  # Stop/join children BEFORE measuring child CPU.
                    verify_cleanup(fixture)
        report["resources"] = monitor.result
        report.update(status="PASS_LOCAL_ONLY", own_rows_roles_and_private_files_cleaned=True,
                      cloud_services="NOT_VALIDATED", monetary_cost="UNKNOWN",
                      measurement_bounds="CPU client plus reaped descendants; sampled summed RSS includes independent HTTP servers and Python helpers, excludes separate PostgreSQL container and kernel/cgroup overhead; shared pages may count repeatedly and short peaks may be missed",
                      failure_scope="Replica hard termination after committed receipt; mid-publication crash, stale fence and reconciliation require the separate real PostgreSQL fault suite")
    except BaseException as exc:
        report.update(status="FAILED", error_type=type(exc).__name__)
        raise
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        events.emit("benchmark_finished", status=report["status"])
        events.close()
        (output / "summary.json").write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = run_benchmark(args.output)
    except Exception as error:
        print(json.dumps({"status": "FAILED", "error_type": type(error).__name__,
                          "summary": str(args.output / "summary.json")}, sort_keys=True))
        return 1
    print(json.dumps({"status": report["status"], "summary": str(args.output / "summary.json"),
                      "events": str(args.output / "events.jsonl")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
