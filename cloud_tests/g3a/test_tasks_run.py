"""LOCAL contracts: provider stub + real PostgreSQL/FFmpeg, never Google PASS."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen
import base64
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
import psycopg

from engine.cli import demo
from engine.cloud_auth import AuthError, TrustedKey
from engine.cloud_run import (CloudLaunchControl, CloudRunJobsAdapter, LaunchBoundMediaControl,
                              LaunchCapacity, LaunchController, LaunchUncertain)
from engine.cloud_tasks import CloudDispatcher, CloudTasksAdapter
from engine.cloud_transport import CloudResponse
from engine.cloud_worker import GoogleServiceIdentityVerifier, TaskSignalHandler, run_bound_worker
from engine.control_plane import StaleLease
from engine.core import sha256_file
from engine.media_runtime import Limits, inspect_media
from g1.support import connect
from g2.support import PipelineFixture


class ProviderStub:
    """Deterministic in-process provider model, NOT Google or an emulator."""
    def __init__(self):
        self.tasks, self.operations = {}, {}
        self.task_calls = self.signal_creations = self.run_calls = 0
        self.lose_task_response = self.lose_run_response = False
        self.rate_limit = False
        self.lock = threading.Lock()

    def request(self, method, url, *, headers, body, timeout, max_response_bytes):
        with self.lock:
            if "cloudtasks.googleapis.com" in url:
                self.task_calls += 1
                if self.rate_limit:
                    return CloudResponse(429, {}, b'{}')
                task = json.loads(body)["task"]
                name = task["name"]
                if name in self.tasks:
                    return CloudResponse(409, {}, b'{"error":{"status":"ALREADY_EXISTS"}}')
                self.tasks[name] = task
                self.signal_creations += 1
                if self.lose_task_response:
                    self.lose_task_response = False
                    raise TimeoutError("accepted response lost")
                return CloudResponse(200, {}, json.dumps({"name": name}).encode())
            if method == "POST" and url.endswith(":run"):
                self.run_calls += 1
                operation = "projects/synthetic-project/locations/test-region/operations/op_" + str(self.run_calls)
                self.operations[operation] = {"name": operation, "done": False}
                self.last_run_body = json.loads(body)
                if self.lose_run_response:
                    self.lose_run_response = False
                    raise TimeoutError("accepted execution response lost")
                return CloudResponse(200, {}, json.dumps(self.operations[operation]).encode())
            name = url.split("/v2/", 1)[1]
            if name not in self.operations:
                return CloudResponse(404, {}, b'{}')
            return CloudResponse(200, {}, json.dumps(self.operations[name]).encode())

    def expire_provider_history(self):
        self.tasks.clear()


def task_adapter(stub):
    return CloudTasksAdapter("synthetic-project", "test-region", "render-signals",
                             callback_url="https://orchestrator.example/tasks/render",
                             audience="https://orchestrator.example/tasks/render",
                             service_account_email="tasks-caller@synthetic-project.iam.gserviceaccount.com",
                             transport=stub)


def run_adapter(stub):
    return CloudRunJobsAdapter("synthetic-project", "test-region", "synthetic-worker", transport=stub)


class GoogleServiceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.verifier = GoogleServiceIdentityVerifier(
            audience="https://orchestrator.example/tasks/render",
            service_account_email="tasks-caller@synthetic-project.iam.gserviceaccount.com",
            service_account_subject="123456789012345678901",
            keys={"local-test": TrustedKey("RS256", cls.private.public_key())})

    def token(self, **changes):
        now = int(time.time())
        claims = {"iss": "https://accounts.google.com", "aud": self.verifier.audience,
                  "sub": self.verifier.subject, "email": self.verifier.email,
                  "email_verified": True, "iat": now, "exp": now + 300}
        claims.update(changes)
        return jwt.encode(claims, self.private, algorithm="RS256", headers={"kid": "local-test"})

    def test_local_rsa_signature_service_identity_and_rejections(self):
        self.assertEqual(self.verifier.email, self.verifier.verify(self.token()))
        for changes in ({"iss": "http://local-supabase/auth/v1", "role": "authenticated"},
                        {"aud": "authenticated"}, {"email": "other@synthetic-project.iam.gserviceaccount.com"},
                        {"sub": "987654321098765432109"}, {"email_verified": False},
                        {"exp": int(time.time()) - 1}, {"exp": int(time.time()) + 4000}):
            with self.subTest(changes=changes), self.assertRaises(AuthError):
                self.verifier.verify(self.token(**changes))
        bad_signature = self.token()[:-5] + "abcde"
        with self.assertRaises(AuthError):
            self.verifier.verify(bad_signature)

    def test_callback_does_not_trust_queue_headers_or_enduser_identity(self):
        class Controller:
            calls = 0
            def launch(self, *args):
                self.calls += 1
        control = Controller()
        handler = TaskSignalHandler(control, verifier=self.verifier)
        body = json.dumps({"version": 1, "event_id": str(uuid4()), "job_id": str(uuid4()),
                           "organization_id": str(uuid4())}).encode()
        self.assertEqual(401, handler.handle("X-CloudTasks-TaskName anything", body)[0])
        self.assertEqual(401, handler.handle("Bearer " + self.token(iss="local-supabase"), body)[0])
        self.assertEqual(202, handler.handle("Bearer " + self.token(), body)[0])
        self.assertEqual(1, control.calls)
        self.assertEqual(400, handler.handle("Bearer " + self.token(), body + b"x")[0])
        with self.assertRaises(ValueError):
            TaskSignalHandler(control, verifier=lambda token: True)

    def test_tasks_request_oidc_name_and_cloud_run_fixed_overrides(self):
        stub = ProviderStub()
        event = {"id": uuid4(), "job_id": uuid4(), "organization_id": uuid4()}
        adapter = task_adapter(stub)
        first = adapter.ensure_signal(event)
        self.assertFalse(first["duplicate"])
        self.assertTrue(adapter.ensure_signal(event)["duplicate"])
        task = stub.tasks[first["name"]]
        signal = json.loads(base64.b64decode(task["httpRequest"]["body"]))
        self.assertEqual(str(event["id"]), signal["event_id"])
        self.assertEqual(adapter.audience, task["httpRequest"]["oidcToken"]["audience"])
        self.assertEqual("30s", task["dispatchDeadline"])
        launch = {"id": uuid4(), "event_id": event["id"], "job_id": event["job_id"],
                  "organization_id": event["organization_id"], "expected_fence": 1}
        operation = run_adapter(stub).run_launch(launch)
        self.assertIn("/operations/", operation)
        override = stub.last_run_body["overrides"]
        self.assertEqual(1, override["taskCount"])
        self.assertEqual("300s", override["timeout"])
        self.assertEqual(CloudRunJobsAdapter.execution_args(launch), override["containerOverrides"][0]["args"])
        self.assertNotIn("env", override["containerOverrides"][0])

    def test_run_acceptance_lost_is_uncertain_and_not_internally_retried(self):
        stub = ProviderStub()
        stub.lose_run_response = True
        launch = {"id": uuid4(), "event_id": uuid4(), "job_id": uuid4(),
                  "organization_id": uuid4(), "expected_fence": 1}
        with self.assertRaises(LaunchUncertain):
            run_adapter(stub).run_launch(launch)
        self.assertEqual(1, stub.run_calls)


class AsyncPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get("G1_TEST_DSN"):
            raise RuntimeError("G1_TEST_DSN required: async PostgreSQL tests never silently skip")
        cls.synthetic = tempfile.TemporaryDirectory()
        cls.paths = demo(Path(cls.synthetic.name), "synthetic")

    @classmethod
    def tearDownClass(cls):
        cls.synthetic.cleanup()

    def setUp(self):
        self.f = PipelineFixture(self.paths["video"], self.paths["caption"])
        self.addCleanup(self.cleanup)
        self.stub = ProviderStub()
        self.controller = LaunchController(self.f.dsn, run_adapter(self.stub), max_inflight=2)
        self.job = self.f.submit_media(self.f.ingest())
        self.event = self.event_for(self.job)

    def cleanup(self):
        with connect() as db:
            db.execute("DELETE FROM public.cloud_job_launches WHERE organization_id=ANY(%s)",
                       ([self.f.org_a, self.f.org_b],))
        self.f.cleanup()

    @staticmethod
    def event_for(job):
        with connect() as db:
            return db.execute("SELECT * FROM public.render_outbox WHERE job_id=%s", (job["id"],)).fetchone()

    def launch(self, job=None, event=None):
        job, event = job or self.job, event or self.event
        return self.controller.launch(job["id"], job["organization_id"], event["id"])

    def assert_real_outputs(self, row):
        self.assertEqual("completed", row["state"])
        self.assertEqual(1, row["attempt_count"])
        expected = {"vertical": (270, 480), "square": (320, 320), "feed": (320, 400)}
        for output in row["output_manifest"]["outputs"]:
            destination = self.f.root / (uuid4().hex + ".mp4")
            self.f.store.get_file(row["organization_id"], output["key"], destination, max_bytes=10485760)
            metadata = inspect_media(destination, Limits())
            self.assertEqual(expected[output["preset"]], (metadata["input_width"], metadata["input_height"]))
            self.assertTrue(metadata["has_audio"])
            self.assertAlmostEqual(.8, metadata["duration_seconds"], delta=.15)
            self.assertEqual(output["sha256"], sha256_file(destination))

    def finish_provider_execution(self, intent, suffix="stopped"):
        execution = run_adapter(self.stub).job_name + "/executions/" + suffix
        self.stub.operations[intent["operation_name"]] = {
            "name": intent["operation_name"], "done": True, "response": {"name": execution}}
        self.stub.operations[execution] = {"name": execution, "taskCount": 1, "failedCount": 1,
                                           "completionTime": "2026-11-07T12:00:00Z"}

    def test_cloud_safe_sweep_recovers_acked_lost_signal_after_provider_history_deleted(self):
        self.assertEqual("dispatched", CloudDispatcher(self.f.dsn).dispatch_one(task_adapter(self.stub))["state"])
        self.stub.expire_provider_history()  # No callback ever delivered; outbox already acknowledged.
        report = self.controller.sweep_once(limit=1)
        self.assertEqual([], report["errors"])
        self.assertEqual(1, len(report["launched"]))
        intent = self.controller.control.get_launch(report["launched"][0]["launch_id"])
        self.assertEqual(self.event["id"], intent["event_id"])
        self.assertEqual(1, self.stub.run_calls)
        self.assert_real_outputs(run_bound_worker(self.f.dsn, self.f.store, self.f.scratch, intent))
        self.assertEqual(1, self.f.counts()["jobs"])
        self.assertEqual(1, self.f.counts()["outbox"])

    def test_cloud_safe_sweep_never_retries_missing_operation_or_pre_network_crash(self):
        intent = self.controller.control.prepare_launch(self.job["id"], self.f.org_a, self.event["id"])
        self.assertEqual(0, self.stub.run_calls)
        for _ in range(3):
            report = self.controller.sweep_once(limit=1)
            self.assertEqual([], report["launched"])
            self.assertIn(str(intent["id"]), report["blocked"])
        self.assertEqual(0, self.stub.run_calls)
        other = self.f.submit_media(self.f.ingest(other=True), "lost-acceptance", other=True)
        self.stub.lose_run_response = True
        uncertain = self.launch(other, self.event_for(other))
        for _ in range(3):
            self.controller.sweep_once(limit=2)
        self.assertEqual("uncertain", self.controller.control.get_launch(uncertain["id"])["state"])
        self.assertEqual(1, self.stub.run_calls)

    def test_cloud_safe_sweep_and_task_delivery_concurrently_create_one_intent(self):
        def deliver(index):
            return self.controller.sweep_once(limit=1) if index % 2 else self.launch()
        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(deliver, range(20)))
        self.assertEqual(1, sum(len(row["launched"]) if "launched" in row
                                else int(row.get("new_intent", False)) for row in results))
        self.assertEqual(1, self.stub.run_calls)
        with connect() as db:
            rows = db.execute("SELECT * FROM public.cloud_job_launches WHERE job_id=%s", (self.job["id"],)).fetchall()
        self.assertEqual(1, len(rows))
        claimed = LaunchBoundMediaControl(self.f.dsn, rows[0]).claim_job(self.job["id"])
        self.assertEqual(1, claimed["fence"])
        self.assertEqual(1, claimed["attempt_count"])

    def test_cloud_safe_sweep_recovers_claimed_crash_only_after_provider_terminal(self):
        intent = self.launch()
        LaunchBoundMediaControl(self.f.dsn, intent).claim_job(self.job["id"])
        self.f.expire(self.job)
        self.controller.sweep_once(limit=1)
        self.assertEqual(1, self.stub.run_calls)
        self.assertEqual(intent["id"], self.launch()["id"])  # Even with a free global slot.
        self.finish_provider_execution(intent)
        report = self.controller.sweep_once(limit=1)
        self.assertEqual([], report["errors"])
        self.assertEqual(1, len(report["launched"]))
        retry = self.controller.control.get_launch(report["launched"][0]["launch_id"])
        self.assertEqual(2, retry["expected_fence"])
        self.assertEqual(2, self.stub.run_calls)

    def test_cloud_safe_sweep_exhaustion_is_terminal_only_after_physical_slot_closed(self):
        with connect() as db:
            db.execute("UPDATE public.render_jobs SET max_attempts=1 WHERE id=%s", (self.job["id"],))
        intent = self.launch()
        claim = LaunchBoundMediaControl(self.f.dsn, intent).claim_job(self.job["id"])
        self.f.expire(self.job)
        self.controller.sweep_once(limit=1)
        self.assertEqual("running", self.f.row(self.job)["state"])
        self.assertEqual(1, self.stub.run_calls)
        self.finish_provider_execution(intent)
        report = self.controller.sweep_once(limit=1)
        self.assertEqual([str(self.job["id"])], report["failed"])
        row = self.f.row(self.job)
        self.assertEqual("failed", row["state"])
        self.assertEqual("attempts_exhausted", row["last_error"])
        self.assertIsNone(row["lease_token"])
        self.assertEqual(claim["fence"], row["fence"])
        self.assertEqual(1, row["attempt_count"])
        self.assertEqual(1, self.stub.run_calls)
        with connect() as db:
            audits = db.execute("SELECT count(*) n FROM public.audit_events WHERE job_id=%s AND event_type='job_failed'",
                                (self.job["id"],)).fetchone()["n"]
            attempt = db.execute("SELECT result FROM public.render_attempts WHERE job_id=%s", (self.job["id"],)).fetchone()
        self.assertEqual(1, audits)
        self.assertEqual("expired", attempt["result"])
        self.controller.sweep_once(limit=1)
        self.assertEqual(1, self.stub.run_calls)

    def test_cloud_safe_sweep_terminal_proof_cannot_exhaust_a_still_live_lease(self):
        with connect() as db:
            db.execute("UPDATE public.render_jobs SET max_attempts=1 WHERE id=%s", (self.job["id"],))
        intent = self.launch()
        LaunchBoundMediaControl(self.f.dsn, intent).claim_job(self.job["id"])
        self.finish_provider_execution(intent)
        report = self.controller.sweep_once(limit=1)
        self.assertEqual([], report["failed"])
        self.assertEqual("closed", self.controller.control.get_launch(intent["id"])["state"])
        self.assertEqual("running", self.f.row(self.job)["state"])
        self.f.expire(self.job)
        self.assertEqual([str(self.job["id"])], self.controller.sweep_once(limit=1)["failed"])
        self.assertEqual(1, self.stub.run_calls)

    def test_concurrent_cloud_safe_sweeps_preserve_global_admission_cap(self):
        asset_a = {"id": self.job["source_asset_id"]}
        asset_b = self.f.ingest(other=True)
        for index in range(4):
            self.f.submit_media(asset_a, "sweep-a-" + str(index))
            self.f.submit_media(asset_b, "sweep-b-" + str(index), other=True)
        with ThreadPoolExecutor(max_workers=8) as pool:
            reports = list(pool.map(lambda _: self.controller.sweep_once(limit=10), range(8)))
        self.assertTrue(all(not report["errors"] for report in reports))
        self.assertEqual(2, sum(len(report["launched"]) for report in reports))
        self.assertEqual(2, self.stub.run_calls)
        with connect() as db:
            self.assertEqual(2, db.execute("SELECT count(*) n FROM public.cloud_job_launches WHERE state<>'closed'").fetchone()["n"])

    def test_cloud_safe_sweep_rotates_unknown_observations_with_limit_one(self):
        self.stub.lose_run_response = True
        unknown = self.launch()
        other = self.f.submit_media(self.f.ingest(other=True), "observed", other=True)
        terminal = self.launch(other, self.event_for(other))
        self.finish_provider_execution(terminal)
        third = self.f.submit_media(self.f.ingest(), "ready-after-release")
        first = self.controller.sweep_once(limit=1)
        self.assertIn(str(unknown["id"]), first["blocked"])
        self.assertEqual([], first["launched"])
        second = self.controller.sweep_once(limit=1)
        self.assertEqual("closed", self.controller.control.get_launch(terminal["id"])["state"])
        self.assertEqual([str(third["id"])], [row["job_id"] for row in second["launched"]])
        self.assertEqual(3, self.stub.run_calls)

    def test_cloud_safe_sweep_skips_saturated_organization_before_candidate_limit(self):
        with connect() as db:
            db.execute("UPDATE public.org_quotas SET max_active_jobs=1 WHERE organization_id=%s", (self.f.org_a,))
        self.launch()  # Reserves org A's only slot; older due A jobs cannot starve B.
        self.f.submit_media(self.f.ingest(), "blocked-old-a")
        other = self.f.submit_media(self.f.ingest(other=True), "ready-b", other=True)
        report = self.controller.sweep_once(limit=1)
        self.assertEqual([str(other["id"])], [row["job_id"] for row in report["launched"]])
        self.assertEqual(2, self.stub.run_calls)
        for value in (0, 101, True, "1"):
            with self.subTest(limit=value), self.assertRaises(ValueError):
                self.controller.sweep_once(limit=value)

    def test_lost_task_response_retry_history_expiry_duplicates_no_extra_job_or_claim(self):
        self.stub.lose_task_response = True
        dispatcher = CloudDispatcher(self.f.dsn)
        self.assertEqual("retry", dispatcher.dispatch_one(task_adapter(self.stub))["state"])
        with connect() as db:
            db.execute("UPDATE public.render_outbox SET available_at=clock_timestamp() WHERE id=%s", (self.event["id"],))
        self.assertEqual("dispatched", dispatcher.dispatch_one(task_adapter(self.stub))["state"])
        self.assertEqual(1, self.stub.signal_creations)
        first = self.launch()
        self.stub.expire_provider_history()  # Finite provider retention; no durable lookup guarantee.
        task_adapter(self.stub).ensure_signal(self.event)
        repeat = self.launch()
        self.assertEqual(first["id"], repeat["id"])
        self.assertEqual(2, self.stub.signal_creations)
        self.assertEqual(1, self.stub.run_calls)
        row = run_bound_worker(self.f.dsn, self.f.store, self.f.scratch, first)
        self.assert_real_outputs(row)
        with self.assertRaises(StaleLease):
            run_bound_worker(self.f.dsn, self.f.store, self.f.scratch, first)
        counts = self.f.counts()
        self.assertEqual(1, counts["jobs"])
        self.assertEqual(1, counts["outbox"])
        with connect() as db:
            self.assertEqual(1, db.execute("SELECT count(*) n FROM public.render_attempts WHERE job_id=%s",
                                          (self.job["id"],)).fetchone()["n"])

    def test_twenty_concurrent_duplicate_signals_and_worker_claims(self):
        with ThreadPoolExecutor(max_workers=20) as pool:
            launches = list(pool.map(lambda _: self.launch(), range(20)))
        self.assertEqual(1, len({row["id"] for row in launches}))
        self.assertEqual(1, self.stub.run_calls)
        def claim(_):
            try:
                return LaunchBoundMediaControl(self.f.dsn, launches[0]).claim_job(self.job["id"])
            except StaleLease:
                return None
        with ThreadPoolExecutor(max_workers=20) as pool:
            claims = list(pool.map(claim, range(20)))
        self.assertEqual(1, sum(row is not None for row in claims))
        self.assertEqual(1, self.f.row(self.job)["attempt_count"])

    def test_uncertain_run_reserves_intent_and_worker_recovers_without_repeat_call(self):
        self.stub.lose_run_response = True
        intent = self.launch()
        self.assertEqual("uncertain", intent["state"])
        for _ in range(3):
            self.assertEqual(intent["id"], self.launch()["id"])
            self.assertEqual("uncertain", self.controller.reconcile(intent["id"])["state"])
        self.assertEqual(1, self.stub.run_calls)
        self.assert_real_outputs(run_bound_worker(self.f.dsn, self.f.store, self.f.scratch, intent))
        self.assertEqual("claimed", self.controller.reconcile(intent["id"])["state"])

    def test_controller_crash_before_network_is_not_blindly_retried_and_pg_sweep_recovers(self):
        intent = self.controller.control.prepare_launch(self.job["id"], self.job["organization_id"], self.event["id"])
        self.assertEqual("submitting", self.launch()["state"])
        self.assertEqual(0, self.stub.run_calls)
        self.assert_real_outputs(self.f.worker().run(self.job["id"]))
        self.assertEqual("submitting", self.controller.reconcile(intent["id"])["state"])

    def test_stale_bound_worker_cannot_replace_valid_g2_takeover(self):
        intent = self.launch()
        old = LaunchBoundMediaControl(self.f.dsn, intent).claim_job(self.job["id"])
        self.f.expire(self.job)
        current = self.f.worker().run(self.job["id"])
        self.assertEqual(2, current["fence"])
        with self.assertRaises(StaleLease):
            run_bound_worker(self.f.dsn, self.f.store, self.f.scratch, intent)
        self.assertEqual(current["output_manifest"], self.f.row(self.job)["output_manifest"])
        with self.assertRaises(StaleLease):
            LaunchBoundMediaControl(self.f.dsn, intent).finish_job(old["id"], old["lease_token"], old["fence"],
                                                                  current["output_manifest"])

    def test_cross_org_signal_fk_and_ledger_access_are_rejected(self):
        with self.assertRaises(ValueError):
            self.controller.launch(self.job["id"], self.f.org_b, self.event["id"])
        other = self.f.submit_media(self.f.ingest(other=True), "other", other=True)
        with self.assertRaises(ValueError):
            self.controller.launch(self.job["id"], self.f.org_a, self.event_for(other)["id"])
        self.assertEqual(0, self.stub.run_calls)
        intent = self.launch()
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with connect("authenticated", self.f.user_a) as db:
                db.execute("SELECT * FROM public.cloud_job_launches")
        with self.assertRaises(psycopg.errors.CheckViolation):
            with connect("engine_worker") as db:
                db.execute("UPDATE public.cloud_job_launches SET expected_fence=2 WHERE id=%s", (intent["id"],))

    def test_global_slots_cover_two_orgs_and_expired_leases_do_not_prove_stopped_compute(self):
        first = self.launch()
        LaunchBoundMediaControl(self.f.dsn, first).claim_job(self.job["id"])
        self.f.expire(self.job)
        other = self.f.submit_media(self.f.ingest(other=True), "other", other=True)
        self.launch(other, self.event_for(other))
        third = self.f.submit_media(self.f.ingest(), "third")
        with self.assertRaises(LaunchCapacity):
            self.launch(third, self.event_for(third))
        self.assertEqual("claimed", self.controller.reconcile(first["id"])["state"])
        self.assertEqual(2, self.stub.run_calls)

    def test_stopped_provider_execution_releases_slot_and_bounded_retry_uses_new_fence(self):
        intent = self.launch()
        execution = run_adapter(self.stub).job_name + "/executions/stopped_1"
        self.stub.operations[intent["operation_name"]] = {
            "name": intent["operation_name"], "done": True, "response": {"name": execution}}
        self.stub.operations[execution] = {"name": execution, "taskCount": 1, "failedCount": 1,
                                           "completionTime": "2026-11-07T12:00:00Z"}
        row = self.controller.reconcile(intent["id"])
        self.assertEqual("closed", row["state"])
        self.assertEqual(1, self.stub.run_calls)
        failed_attempt = self.f.row(self.job)
        self.assertEqual("queued", failed_attempt["state"])
        self.assertEqual(1, failed_attempt["fence"])
        self.assertEqual(1, failed_attempt["attempt_count"])
        self.f.ready(self.job)
        retry = self.launch()
        self.assertNotEqual(intent["id"], retry["id"])
        self.assertEqual(2, retry["expected_fence"])
        self.assertEqual(2, self.stub.run_calls)

    def test_operation_done_only_means_execution_created_not_worker_stopped(self):
        intent = self.launch()
        execution = run_adapter(self.stub).job_name + "/executions/running_1"
        self.stub.operations[intent["operation_name"]] = {
            "name": intent["operation_name"], "done": True, "response": {"name": execution}}
        self.stub.operations[execution] = {"name": execution, "taskCount": 1, "runningCount": 1}
        self.assertEqual("accepted", self.controller.reconcile(intent["id"])["state"])
        self.assertEqual(0, self.f.row(self.job)["attempt_count"])

    def test_pg_completion_does_not_free_physical_slot_until_provider_execution_terminal(self):
        first = self.launch()
        self.assert_real_outputs(run_bound_worker(self.f.dsn, self.f.store, self.f.scratch, first))
        self.assertEqual("claimed", self.controller.reconcile(first["id"])["state"])
        other = self.f.submit_media(self.f.ingest(other=True), "other", other=True)
        self.launch(other, self.event_for(other))
        third = self.f.submit_media(self.f.ingest(), "third")
        with self.assertRaises(LaunchCapacity):
            self.launch(third, self.event_for(third))
        execution = run_adapter(self.stub).job_name + "/executions/completed_1"
        self.stub.operations[first["operation_name"]] = {
            "name": first["operation_name"], "done": True, "response": {"name": execution}}
        self.stub.operations[execution] = {"name": execution, "taskCount": 1, "succeededCount": 1,
                                           "completionTime": "2026-11-07T12:00:00Z"}
        self.assertEqual("closed", self.controller.reconcile(first["id"])["state"])
        self.assertEqual("accepted", self.launch(third, self.event_for(third))["state"])

    def test_claimed_worker_crash_requires_execution_terminal_before_new_fence_launch(self):
        first = self.launch()
        old = LaunchBoundMediaControl(self.f.dsn, first).claim_job(self.job["id"])
        self.f.expire(self.job)  # SIGKILL-equivalent durable state; no fail_job callback.
        execution = run_adapter(self.stub).job_name + "/executions/crashed_1"
        self.stub.operations[first["operation_name"]] = {
            "name": first["operation_name"], "done": True, "response": {"name": execution}}
        self.stub.operations[execution] = {"name": execution, "taskCount": 1, "failedCount": 1,
                                           "completionTime": "2026-11-07T12:00:00Z"}
        self.assertEqual("closed", self.controller.reconcile(first["id"])["state"])
        retry = self.launch()
        self.assertEqual(2, retry["expected_fence"])
        current = LaunchBoundMediaControl(self.f.dsn, retry).claim_job(self.job["id"])
        self.assertEqual(2, current["fence"])
        self.assertEqual(2, current["attempt_count"])
        self.assertEqual(2, self.stub.run_calls)
        with self.assertRaises(StaleLease):
            LaunchBoundMediaControl(self.f.dsn, first).renew_lease(old["id"], old["lease_token"], old["fence"])

    def slow_http_store(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                threading.Event().wait(1.5)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"local blocking I/O contract")
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(5)))
        fixture = self.f
        class SlowStore:
            delayed = False
            def __getattr__(self, key):
                return getattr(fixture.store, key)
            def get_file(self, *args, **kwargs):
                if not self.delayed:
                    self.delayed = True
                    with urlopen("http://127.0.0.1:" + str(server.server_port), timeout=4) as response:
                        response.read(1024)
                return fixture.store.get_file(*args, **kwargs)
        return SlowStore()

    def test_slow_real_loopback_io_longer_than_lease_keeps_same_fence_alive(self):
        intent = self.launch()
        result = run_bound_worker(self.f.dsn, self.slow_http_store(), self.f.scratch,
                                  intent, lease_seconds=1)
        self.assert_real_outputs(result)
        self.assertEqual(1, result["fence"])
        self.assertFalse(any(t.name == "g3a-lease-keeper" and t.is_alive() for t in threading.enumerate()))

    def test_independent_keeper_stops_at_job_deadline_and_never_commits_afterward(self):
        intent = self.launch()
        with self.assertRaises(StaleLease):
            run_bound_worker(self.f.dsn, self.slow_http_store(), self.f.scratch, intent,
                             lease_seconds=1, limits=replace(Limits(), timeout_seconds=1))
        row = self.f.row(self.job)
        self.assertEqual("running", row["state"])
        self.assertIsNone(row["output_manifest"])
        self.assertFalse(any(t.name == "g3a-lease-keeper" and t.is_alive() for t in threading.enumerate()))
        self.f.expire(self.job)
        recovered = self.f.worker().run(self.job["id"])
        self.assertEqual("completed", recovered["state"])
        self.assertEqual(2, recovered["fence"])

    def test_completed_fence_stops_keeper_before_slow_after_commit_metrics(self):
        intent = self.launch()
        def slow_after_commit(stage, claim):
            if stage == "after_commit":
                threading.Event().wait(.8)
        row = run_bound_worker(self.f.dsn, self.f.store, self.f.scratch, intent,
                               lease_seconds=1, fault_hook=slow_after_commit)
        self.assert_real_outputs(row)
        self.assertEqual("completed", self.f.row(self.job)["state"])
        self.assertFalse(any(t.name == "g3a-lease-keeper" and t.is_alive() for t in threading.enumerate()))

    def test_operation_error_alone_does_not_prove_no_physical_execution(self):
        intent = self.launch()
        self.stub.operations[intent["operation_name"]] = {
            "name": intent["operation_name"], "done": True, "error": {"code": 13}}
        self.assertEqual("accepted", self.controller.reconcile(intent["id"])["state"])
        self.assertEqual(1, self.stub.run_calls)
        self.assertEqual(0, self.f.row(self.job)["attempt_count"])

    def test_429_outbox_backoff_and_operation_history_loss_do_not_duplicate_launch(self):
        self.stub.rate_limit = True
        result = CloudDispatcher(self.f.dsn).dispatch_one(task_adapter(self.stub))
        self.assertEqual("retry", result["state"])
        with connect() as db:
            row = db.execute("SELECT * FROM public.render_outbox WHERE id=%s", (self.event["id"],)).fetchone()
        self.assertEqual("429", row["last_error"])
        self.assertIsNone(row["dispatched_at"])
        intent = self.launch()
        self.stub.operations.clear()
        with self.assertRaises(LaunchUncertain):
            self.controller.reconcile(intent["id"])
        self.assertEqual(intent["id"], self.launch()["id"])
        self.assertEqual(1, self.stub.run_calls)
