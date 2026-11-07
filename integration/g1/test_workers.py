"""Real PostgreSQL fault/lease tests; only the remote queue is a local stub."""
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest
import os
from uuid import uuid4

import psycopg

from engine.control_plane import ControlPlane, StaleLease
from engine.handoff import Dispatcher, QueueRateLimited
from g1.support import Fixture, connect


class DurableQueue:
    """An idempotent provider contract, including acceptance with lost response."""

    def __init__(self, faults=()):
        self.tasks = {}
        self.faults = list(faults)
        self.calls = []
        self.effects = 0
        self.lock = threading.Lock()

    def lookup(self, event_id):
        with self.lock:
            return self.tasks.get(event_id)

    def ensure_task(self, event_id, job_id):
        with self.lock:
            self.calls.append((event_id, job_id))
            fault = self.faults.pop(0) if self.faults else None
            if fault == "timeout":
                raise TimeoutError("synthetic timeout before acceptance")
            if fault == "429":
                raise QueueRateLimited("synthetic 429 before acceptance")
            if event_id not in self.tasks:
                self.tasks[event_id] = {"event_id": event_id, "job_id": job_id}
                self.effects += 1
            if fault == "lost":
                raise TimeoutError("synthetic response lost after acceptance")
            return self.tasks[event_id]


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.worker = ControlPlane(os.environ["G1_TEST_DSN"])
        self.dispatcher = Dispatcher(os.environ["G1_TEST_DSN"])
        # The dispatcher intentionally scans all tenants. Other test fixtures are
        # completed here so this test's event is the only pending queue work.
        with connect() as db:
            db.execute("UPDATE public.render_outbox SET dispatched_at=clock_timestamp(), "
                       "state='dispatched' WHERE state='pending'")

    def submit(self, key="worker", **kwargs):
        return self.fixture.submit(key=key, **kwargs)

    @staticmethod
    def manifest(job):
        generation = f"{job['organization_id']}/{job['id']}/{job['fence']}"
        return {"generation": generation, "outputs": [
            {"key": generation + "/vertical.mp4", "sha256": "a" * 64,
             "size_bytes": 123}
        ]}

    def expire_job(self, job):
        with connect() as db:
            db.execute("UPDATE public.render_jobs SET lease_expires_at="
                       "clock_timestamp()-interval '1 second' WHERE id=%s", (job["id"],))

    def ready_job(self, job):
        with connect() as db:
            db.execute("UPDATE public.render_jobs SET available_at="
                       "clock_timestamp()-interval '1 second' WHERE id=%s", (job["id"],))

    def ready_event(self, event):
        event_id = event.get("event_id", event.get("id"))
        with connect() as db:
            db.execute("UPDATE public.render_outbox SET available_at="
                       "clock_timestamp()-interval '1 second' WHERE id=%s", (event_id,))

    def test_concurrent_duplicate_delivery_has_one_live_claim(self):
        job = self.submit()
        gate = threading.Barrier(20)

        def claim(_):
            gate.wait(timeout=20)
            return self.worker.claim_job(job["id"])

        with ThreadPoolExecutor(max_workers=20) as pool:
            claims = list(pool.map(claim, range(20)))
        owners = [claim for claim in claims if claim is not None]
        self.assertEqual(1, len(owners))
        self.assertEqual(1, owners[0]["fence"])
        with connect() as db:
            attempts = db.execute("SELECT * FROM public.render_attempts WHERE job_id=%s",
                                  (job["id"],)).fetchall()
        self.assertEqual(1, len(attempts))
        self.assertEqual(owners[0]["lease_token"], attempts[0]["lease_token"])

    def test_org_concurrency_cap_is_atomic_and_other_org_can_run(self):
        with connect() as db:
            db.execute("UPDATE public.org_quotas SET max_active_jobs=1 WHERE organization_id=%s",
                       (self.fixture.org_a,))
        jobs = [self.submit(key=f"limited-{n}") for n in range(2)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(lambda job: self.worker.claim_job(job["id"]), jobs))
        self.assertEqual(1, sum(claim is not None for claim in claims))
        other = self.submit(key="other-org", org=self.fixture.org_b,
                            user=self.fixture.user_b, asset=self.fixture.asset_b)
        self.assertIsNotNone(self.worker.claim_job(other["id"]))

    def test_expired_owner_cannot_commit_even_before_takeover(self):
        claim = self.worker.claim_job(self.submit()["id"])
        self.expire_job(claim)
        with self.assertRaises(StaleLease):
            self.worker.finish_job(claim["id"], claim["lease_token"], claim["fence"],
                                   self.manifest(claim))
        with self.assertRaises(StaleLease):
            self.worker.fail_job(claim["id"], claim["lease_token"], claim["fence"],
                                 error_code="timeout")
        self.assertEqual("running", self.worker.get_job(claim["id"])["state"])

    def test_takeover_fences_old_owner_and_final_result_is_immutable(self):
        old = self.worker.claim_job(self.submit()["id"])
        self.expire_job(old)
        new = self.worker.claim_job(old["id"])
        self.assertEqual(old["fence"] + 1, new["fence"])
        self.assertNotEqual(old["lease_token"], new["lease_token"])
        with self.assertRaises(StaleLease):
            self.worker.finish_job(old["id"], old["lease_token"], old["fence"],
                                   self.manifest(old))
        result = self.worker.finish_job(new["id"], new["lease_token"], new["fence"],
                                        self.manifest(new))
        self.assertEqual("completed", result["state"])
        with self.assertRaises(StaleLease):
            self.worker.fail_job(old["id"], old["lease_token"], old["fence"],
                                 error_code="crash")
        self.assertEqual(self.manifest(new), self.worker.get_job(new["id"])["output_manifest"])
        with connect() as db:
            attempts = db.execute("SELECT result FROM public.render_attempts WHERE job_id=%s "
                                  "ORDER BY attempt_no", (new["id"],)).fetchall()
        self.assertEqual(["expired", "completed"], [a["result"] for a in attempts])

    def test_commit_response_lost_reconciles_without_second_completion(self):
        claim = self.worker.claim_job(self.submit()["id"])
        self.worker.finish_job(claim["id"], claim["lease_token"], claim["fence"], self.manifest(claim))
        # Caller loses the response after DB commit, then repeats both delivery
        # and completion. Neither changes attempt count nor creates another effect.
        reconciled = self.worker.claim_job(claim["id"])
        self.assertEqual("completed", reconciled["state"])
        repeated = self.worker.finish_job(claim["id"], claim["lease_token"], claim["fence"],
                                          self.manifest(claim))
        self.assertEqual(reconciled["output_manifest"], repeated["output_manifest"])
        self.assertEqual(reconciled["attempt_count"], repeated["attempt_count"])
        with connect() as db:
            counts = db.execute("SELECT count(*) AS n FROM public.render_attempts WHERE job_id=%s",
                                (claim["id"],)).fetchone()
        self.assertEqual(1, counts["n"])

    def test_crash_before_completion_commit_rolls_back_then_recovers(self):
        claim = self.worker.claim_job(self.submit()["id"])
        suffix = uuid4().hex
        function_name = "g1_fail_finish_" + suffix
        trigger_name = "g1_fail_finish_" + suffix
        with connect() as db:
            # All interpolated identifiers/values here are generated UUIDs. This
            # emulates a DB failure after the job and attempt were updated but
            # before the completion audit/transaction could commit.
            db.execute(f"""CREATE FUNCTION public.{function_name}() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN
                IF NEW.job_id = '{claim['id']}'::uuid THEN
                    RAISE EXCEPTION 'synthetic crash before completion commit';
                END IF;
                RETURN NEW; END $$""")
            db.execute(f"CREATE TRIGGER {trigger_name} BEFORE INSERT ON public.audit_events "
                       f"FOR EACH ROW EXECUTE FUNCTION public.{function_name}()")
        try:
            with self.assertRaises(psycopg.errors.RaiseException):
                self.worker.finish_job(claim["id"], claim["lease_token"], claim["fence"],
                                       self.manifest(claim))
            row = self.worker.get_job(claim["id"])
            self.assertEqual("running", row["state"])
            self.assertIsNone(row["output_manifest"])
            with connect() as db:
                attempt = db.execute("SELECT result,ended_at FROM public.render_attempts "
                                     "WHERE job_id=%s", (claim["id"],)).fetchone()
            self.assertIsNone(attempt["result"])
            self.assertIsNone(attempt["ended_at"])
        finally:
            with connect() as db:
                db.execute(f"DROP TRIGGER {trigger_name} ON public.audit_events")
                db.execute(f"DROP FUNCTION public.{function_name}()")
        self.expire_job(claim)
        new = self.worker.claim_job(claim["id"])
        completed = self.worker.finish_job(new["id"], new["lease_token"], new["fence"],
                                           self.manifest(new))
        self.assertEqual("completed", completed["state"])

    def test_timeout_429_retries_have_backoff_and_bounded_attempts(self):
        job = self.submit()
        for number, error in enumerate(("timeout", "429", "crash"), start=1):
            claim = self.worker.claim_job(job["id"])
            self.assertEqual(number, claim["attempt_count"])
            result = self.worker.fail_job(job["id"], claim["lease_token"], claim["fence"],
                                          error_code=error)
            self.assertEqual(error, result["last_error"])
            self.assertIsNone(self.worker.claim_job(job["id"]))
            if number < 3:
                self.assertEqual("queued", result["state"])
                self.ready_job(job)
            else:
                self.assertEqual("failed", result["state"])
        self.ready_job(job)
        self.assertIsNone(self.worker.claim_job(job["id"]))
        self.assertEqual(3, self.worker.get_job(job["id"])["attempt_count"])

    def test_repeated_crashes_exhaust_claims(self):
        job = self.submit()
        for number in range(1, 4):
            claim = self.worker.claim_job(job["id"])
            self.assertEqual(number, claim["attempt_count"])
            self.expire_job(claim)
        self.assertIsNone(self.worker.claim_job(job["id"]))
        self.assertEqual("failed", self.worker.get_job(job["id"])["state"])

    def test_manifest_validation_rejects_cross_tenant_keys_and_bad_checksum(self):
        claim = self.worker.claim_job(self.submit()["id"])
        for broken in (
            {"generation": "another-org/job/1", "outputs": []},
            {"generation": self.manifest(claim)["generation"], "outputs": [
                {"key": self.manifest(claim)["generation"] + "/a", "sha256": "BAD", "size_bytes": 1}]},
        ):
            with self.assertRaises(ValueError):
                self.worker.finish_job(claim["id"], claim["lease_token"], claim["fence"], broken)
        self.assertEqual("running", self.worker.get_job(claim["id"])["state"])

    def test_concurrent_dispatchers_claim_one_event(self):
        self.submit()
        gate = threading.Barrier(16)

        def claim(_):
            gate.wait(timeout=20)
            return self.dispatcher.claim_outbox()

        with ThreadPoolExecutor(max_workers=16) as pool:
            claimed = list(pool.map(claim, range(16)))
        self.assertEqual(1, sum(row is not None for row in claimed))

    def test_timeout_429_lost_queue_response_never_multiply_effect(self):
        job = self.submit()
        queue = DurableQueue(["timeout", "429", "lost"])
        results = []
        for _ in range(4):
            result = self.dispatcher.dispatch_one(queue)
            results.append(result)
            if result["state"] == "dispatched":
                break
            self.ready_event(result)
        self.assertEqual("dispatched", results[-1]["state"])
        self.assertEqual(1, queue.effects)
        self.assertEqual(1, len(queue.tasks))
        self.assertEqual({str(job["id"])}, {task["job_id"] for task in queue.tasks.values()})
        self.assertEqual(1, len({event_id for event_id, _ in queue.calls}))
        self.assertIsNone(self.dispatcher.dispatch_one(queue))

    def test_dispatch_crash_after_acceptance_before_ack_reconciles(self):
        self.submit()
        queue = DurableQueue()
        event = self.dispatcher.claim_outbox()
        queue.ensure_task(str(event["id"]), str(event["job_id"]))
        # Process disappears with its lease before ack. New process looks up the
        # same event after expiry and records delivery without ensure_task again.
        with connect() as db:
            db.execute("UPDATE public.render_outbox SET lease_expires_at="
                       "clock_timestamp()-interval '1 second' WHERE id=%s", (event["id"],))
        result = self.dispatcher.dispatch_one(queue)
        self.assertEqual("dispatched", result["state"])
        self.assertEqual(1, queue.effects)
        self.assertEqual(1, len(queue.calls))

    def test_dispatch_crash_before_acceptance_retries_same_id(self):
        self.submit()
        event = self.dispatcher.claim_outbox()
        with connect() as db:
            db.execute("UPDATE public.render_outbox SET lease_expires_at="
                       "clock_timestamp()-interval '1 second' WHERE id=%s", (event["id"],))
        queue = DurableQueue()
        result = self.dispatcher.dispatch_one(queue)
        self.assertEqual("dispatched", result["state"])
        self.assertEqual([(str(event["id"]), str(event["job_id"]))], queue.calls)

    def test_stale_dispatcher_cannot_ack_or_release_new_lease(self):
        self.submit()
        old = self.dispatcher.claim_outbox()
        with connect() as db:
            db.execute("UPDATE public.render_outbox SET lease_expires_at="
                       "clock_timestamp()-interval '1 second' WHERE id=%s", (old["id"],))
        new = self.dispatcher.claim_outbox()
        self.assertGreater(new["fence"], old["fence"])
        with self.assertRaises(StaleLease):
            self.dispatcher.ack(old)
        with self.assertRaises(StaleLease):
            self.dispatcher.release(old, error_code="timeout")
        self.dispatcher.ack(new)

    def test_dispatch_exhaustion_requires_reconciliation(self):
        job = self.submit()
        queue = DurableQueue(["timeout"] * 5)
        results = []
        for _ in range(5):
            result = self.dispatcher.dispatch_one(queue)
            results.append(result)
            self.ready_event(result)
        self.assertEqual("reconcile", results[-1]["state"])
        self.assertEqual(0, queue.effects)
        self.assertIsNone(self.dispatcher.dispatch_one(queue))
        with connect() as db:
            row = db.execute("SELECT * FROM public.render_outbox WHERE job_id=%s", (job["id"],)).fetchone()
        self.assertIsNone(row["dispatched_at"])
        self.assertEqual("reconcile", row["state"])
        self.assertEqual(5, row["attempt_count"])


if __name__ == "__main__":
    unittest.main()
