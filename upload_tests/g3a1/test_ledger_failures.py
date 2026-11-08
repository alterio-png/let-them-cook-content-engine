"""Real PostgreSQL, immutable local files and actual OS-process crash tests.

Small synthetic byte strings deliberately remain unvalidated uploads. These
tests prove ledger/storage contracts, not media validity or cloud integration.
Fault hooks inject failures at an actual PostgreSQL/local-disk boundary; no
database or object-store implementation is replaced with a mock.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import multiprocessing
import os
from pathlib import Path
import signal
import threading
import time
import unittest
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from engine.object_store import LocalObjectStore, ObjectConflict, ObjectMissing
from engine.shared_upload_authority import PostgresUploadAuthority
from engine.upload_authority import CapabilityError, UploadAlreadyUsed
from g1.support import connect
from g3a1.support import SharedHTTPFixture


def _paused_process_publish(config, channel):
    """A fresh interpreter opens its own DB connections and local-store handle."""
    def pause(checkpoint, context):
        if checkpoint == config["checkpoint"]:
            channel.send({"checkpoint": checkpoint, "pid": os.getpid()})
            signal.pause()

    authority = PostgresUploadAuthority(
        config["dsn"], config["signing_key"], lease_seconds=1,
        ttl_seconds=config["ttl_seconds"], fault_hook=pause,
    )
    try:
        authority.publish(config["token"], config["user_id"], Path(config["source"]),
                          LocalObjectStore(Path(config["store_root"])))
    except BaseException as error:
        channel.send({"error": type(error).__name__, "detail": str(error)})
        raise


def _process_reconcile(config, start, channel):
    authority = PostgresUploadAuthority(config["dsn"], config["signing_key"],
                                       lease_seconds=1, ttl_seconds=config["ttl_seconds"])
    start.wait(10)
    try:
        result = authority.reconcile(config["org_id"], config["user_id"], config["grant_id"],
                                     LocalObjectStore(Path(config["store_root"])))
        channel.send({"result": result, "pid": os.getpid()})
    except Exception as error:
        channel.send({"error": type(error).__name__, "detail": str(error), "pid": os.getpid()})


class LedgerFailureTests(unittest.TestCase):
    def setUp(self):
        self.fx = SharedHTTPFixture(ttl_seconds=30, lease_seconds=1)
        self.addCleanup(self.fx.cleanup)
        self.f = self.fx.f
        self.org, self.user = self.f.org_a, self.f.user_a
        self.data = b"G3A.1 synthetic unvalidated upload\x00\x01\x02"
        self.source = self.fx.root / "fault-source.bin"
        self.source.write_bytes(self.data)
        self.source.chmod(0o600)
        self.digest = hashlib.sha256(self.data).hexdigest()
        self.store = LocalObjectStore(self.fx.store_root)
        self.fx.provision_quota(max_uploads=128, max_bytes=len(self.data) * 128, ttl_seconds=30)
        self.authority = self.make_authority()

    def make_authority(self, **kwargs):
        options = {"lease_seconds": 1, "ttl_seconds": 30}
        options.update(kwargs)
        return PostgresUploadAuthority(self.fx.dsn, self.fx.signing_key, **options)

    def reserve(self, key=None, *, authority=None, data=None):
        body = self.data if data is None else data
        return (authority or self.authority).reserve(
            self.org, self.user, request_key=key or uuid4().hex,
            size_bytes=len(body), sha256=hashlib.sha256(body).hexdigest())

    def row(self, grant_id):
        with connect() as db:
            return db.execute("SELECT * FROM g3a1_private.upload_reservations WHERE id=%s", (grant_id,)).fetchone()

    def snapshot(self):
        with connect() as db:
            limits = db.execute("SELECT used_bytes,used_count FROM g3a1_private.upload_limits WHERE organization_id=%s", (self.org,)).fetchone()
            receipts = db.execute("SELECT * FROM g3a1_private.upload_receipts WHERE organization_id=%s ORDER BY id", (self.org,)).fetchall()
            jobs = db.execute("SELECT count(*) AS n FROM public.render_jobs WHERE organization_id=%s", (self.org,)).fetchone()["n"]
            assets = db.execute("SELECT count(*) AS n FROM public.assets WHERE organization_id=%s", (self.org,)).fetchone()["n"]
        return {"bytes": limits["used_bytes"], "count": limits["used_count"],
                "receipts": receipts, "jobs": jobs, "assets": assets}

    def key(self, grant):
        return self.row(grant["id"])["object_key"]

    def assert_charged_once(self):
        state = self.snapshot()
        self.assertEqual((len(self.data), 1), (state["bytes"], state["count"]))
        self.assertEqual(0, state["jobs"], "upload must not create a render job")
        self.assertEqual(1, state["assets"], "upload must not promote an unvalidated asset")
        return state

    def wait_for_lease(self, grant_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with connect() as db:
                expired = db.execute("SELECT lease_expires_at IS NULL OR lease_expires_at<=clock_timestamp() AS done FROM g3a1_private.upload_reservations WHERE id=%s", (grant_id,)).fetchone()["done"]
            if expired:
                return
            time.sleep(0.03)
        self.fail("actual PostgreSQL lease did not expire within the bounded wait")

    def wait_for_ttl(self, grant_id):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            with connect() as db:
                expired = db.execute("SELECT expires_at<=clock_timestamp() AS done FROM g3a1_private.upload_reservations WHERE id=%s", (grant_id,)).fetchone()["done"]
            if expired:
                return
            time.sleep(0.03)
        self.fail("actual PostgreSQL reservation TTL did not expire")

    @contextmanager
    def actor(self):
        with psycopg.connect(self.fx.dsn, row_factory=dict_row) as db:
            db.execute("SET LOCAL ROLE authenticated")
            db.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (str(self.user),))
            yield db

    def crash_at(self, grant, checkpoint):
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        config = {"dsn": self.fx.dsn, "signing_key": self.fx.signing_key,
                  "ttl_seconds": 3, "token": grant["authorization"], "user_id": str(self.user),
                  "source": str(self.source), "store_root": str(self.fx.store_root),
                  "checkpoint": checkpoint}
        process = context.Process(target=_paused_process_publish, args=(config, sender))
        process.start()
        sender.close()
        try:
            self.assertTrue(receiver.poll(8), "publisher did not reach durable crash boundary")
            message = receiver.recv()
            self.assertEqual(checkpoint, message.get("checkpoint"), message)
            self.assertNotEqual(os.getpid(), message["pid"])
            os.kill(process.pid, signal.SIGKILL)
            process.join(5)
            self.assertEqual(-signal.SIGKILL, process.exitcode)
        finally:
            if process.is_alive():
                process.kill()
                process.join(5)
            receiver.close()

    def test_same_request_has_stable_hmac_and_immutable_fingerprint(self):
        grant = self.reserve("immutable-fingerprint")
        repeated = self.reserve("immutable-fingerprint", authority=self.make_authority())
        self.assertEqual(grant, repeated)
        self.assertEqual(grant["authorization"], repeated["authorization"])
        for data in (self.data + b"x", b"x" * len(self.data)):
            with self.assertRaises(CapabilityError):
                self.reserve("immutable-fingerprint", data=data)
        self.assertEqual([], self.assert_charged_once()["receipts"])

    def test_fault_before_claim_does_not_change_reservation(self):
        grant = self.reserve()
        def fail(checkpoint, context):
            if checkpoint == "before_claim":
                raise RuntimeError("injected-before-claim")
        with self.assertRaisesRegex(RuntimeError, "injected-before-claim"):
            self.make_authority(fault_hook=fail).publish(grant["authorization"], self.user, self.source, self.store)
        row = self.row(grant["id"])
        self.assertEqual(("reserved", 0), (row["state"], row["fence"]))
        self.assertEqual([], self.store.list_keys(self.org))
        self.assert_charged_once()

    def test_fault_after_claim_or_before_store_holds_quota_without_receipt(self):
        for checkpoint in ("after_claim", "before_store"):
            with self.subTest(checkpoint=checkpoint):
                grant = self.reserve()
                def fail(stage, context):
                    if stage == checkpoint:
                        raise RuntimeError("injected-" + checkpoint)
                with self.assertRaises(RuntimeError):
                    self.make_authority(fault_hook=fail).publish(grant["authorization"], self.user, self.source, self.store)
                self.assertIn(self.row(grant["id"])["state"], ("publishing", "uncertain"))
                self.assertGreater(self.row(grant["id"])["fence"], 0)
                self.assertEqual([], self.snapshot()["receipts"])
                self.assertEqual([], self.store.list_keys(self.org))
        self.assertEqual((len(self.data) * 2, 2), (self.snapshot()["bytes"], self.snapshot()["count"]))

    def test_storage_complete_db_receipt_missing_is_reconciled_after_restart(self):
        grant = self.reserve()
        def fail(stage, context):
            if stage == "after_store":
                raise RuntimeError("injected-after-durable-store")
        with self.assertRaisesRegex(RuntimeError, "after-durable-store"):
            self.make_authority(fault_hook=fail).publish(grant["authorization"], self.user, self.source, self.store)
        self.assertEqual(self.digest, self.store.stat(self.org, self.key(grant))["sha256"])
        self.assertEqual([], self.snapshot()["receipts"])
        self.wait_for_lease(grant["id"])
        result = self.make_authority().reconcile(self.org, self.user, grant["id"], self.store)
        self.assertEqual("uploaded_pending_validation", result["state"])
        self.assertEqual(1, len(self.assert_charged_once()["receipts"]))

    def test_actual_sigkill_after_durable_object_recovers_in_fresh_process_authority(self):
        grant = self.reserve()
        self.crash_at(grant, "after_store")
        self.assertEqual([], self.snapshot()["receipts"])
        self.assertEqual(self.digest, self.store.stat(self.org, self.key(grant))["sha256"])
        self.wait_for_lease(grant["id"])
        result = self.make_authority().reconcile(self.org, self.user, grant["id"], LocalObjectStore(self.fx.store_root))
        self.assertEqual("uploaded_pending_validation", result["state"])
        self.assertEqual(1, len(self.assert_charged_once()["receipts"]))

    def test_actual_sigkill_before_storage_expiry_never_refunds_claimed_quota(self):
        grant = self.reserve(authority=self.make_authority(ttl_seconds=3))
        self.crash_at(grant, "before_store")
        self.wait_for_lease(grant["id"])
        self.wait_for_ttl(grant["id"])
        self.make_authority().reconcile(self.org, self.user, grant["id"], self.store)
        self.assertEqual("uncertain", self.row(grant["id"])["state"])
        self.assertEqual([], self.store.list_keys(self.org))
        self.assertEqual([], self.assert_charged_once()["receipts"])
        with self.assertRaises(CapabilityError):
            self.make_authority().publish(grant["authorization"], self.user, self.source, self.store)
        wrong = self.fx.root / "wrong-resume.bin"
        wrong.write_bytes(b"X" * len(self.data))
        wrong.chmod(0o600)
        with self.assertRaises(CapabilityError):
            self.make_authority().resume(self.org, self.user, grant["id"], wrong, self.store)
        result = self.make_authority().resume(self.org, self.user, grant["id"], self.source, self.store)
        self.assertEqual("uploaded_pending_validation", result["state"])
        self.assertEqual(1, len(self.assert_charged_once()["receipts"]))

    def test_response_lost_after_commit_replay_returns_exact_immutable_receipt(self):
        grant = self.reserve()
        def lost(stage, context):
            if stage == "after_commit":
                raise ConnectionResetError("simulated lost transport response after real commit")
        with self.assertRaises(ConnectionResetError):
            self.make_authority(fault_hook=lost).publish(grant["authorization"], self.user, self.source, self.store)
        before = self.assert_charged_once()["receipts"]
        self.assertEqual(1, len(before))
        result = self.make_authority().publish(grant["authorization"], self.user, self.source, self.store)
        self.assertEqual("uploaded_pending_validation", result["state"])
        self.assertEqual(before, self.assert_charged_once()["receipts"])
        self.assertEqual([self.key(grant)], self.store.list_keys(self.org))

    def test_twenty_concurrent_publishes_have_one_object_receipt_and_charge(self):
        grant = self.reserve()
        start = threading.Barrier(20)
        def publish(_):
            authority = self.make_authority()
            start.wait(5)
            try:
                return authority.publish(grant["authorization"], self.user, self.source, self.store)
            except UploadAlreadyUsed:
                return None  # A currently leased attempt is explicitly retriable.
        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(publish, range(20)))
        self.assertTrue(any(result is not None for result in results))
        self.assertEqual(1, len(self.assert_charged_once()["receipts"]))
        self.assertEqual([self.key(grant)], self.store.list_keys(self.org))
        self.assertEqual(self.digest, self.store.stat(self.org, self.key(grant))["sha256"])

    def test_stale_fence_cannot_replace_new_valid_receipt(self):
        grant = self.reserve()
        with self.actor() as db:
            old = db.execute("SELECT g3a1_private.claim_upload(%s,%s,1) AS value", (self.org, grant["id"])).fetchone()["value"]
        self.wait_for_lease(grant["id"])
        valid = self.authority.publish(grant["authorization"], self.user, self.source, self.store)
        before = self.assert_charged_once()["receipts"]
        with self.assertRaises(psycopg.Error) as caught:
            with self.actor() as db:
                db.execute("SELECT * FROM g3a1_private.complete_upload(%s,%s,%s,%s,%s,%s,%s,NULL)",
                           (self.org, grant["id"], old["lease_token"], old["fence"], self.key(grant), len(self.data), self.digest))
        self.assertEqual("40001", caught.exception.sqlstate)
        self.assertEqual(before, self.assert_charged_once()["receipts"])
        self.assertEqual("uploaded_pending_validation", valid["state"])

    def test_reconcile_during_live_publish_never_deletes_or_refunds(self):
        grant = self.reserve()
        entered, release = threading.Event(), threading.Event()
        def pause(stage, context):
            if stage == "after_store":
                entered.set()
                if not release.wait(8):
                    raise RuntimeError("test did not release publisher")
        with ThreadPoolExecutor(max_workers=2) as pool:
            future = pool.submit(self.make_authority(fault_hook=pause).publish,
                                 grant["authorization"], self.user, self.source, self.store)
            try:
                self.assertTrue(entered.wait(5))
                try:
                    self.make_authority().reconcile(self.org, self.user, grant["id"], self.store)
                except UploadAlreadyUsed:
                    pass
                self.assert_charged_once()
                self.assertEqual(self.digest, self.store.stat(self.org, self.key(grant))["sha256"])
            finally:
                release.set()
            self.assertEqual("uploaded_pending_validation", future.result(timeout=8)["state"])
        self.assertEqual(1, len(self.assert_charged_once()["receipts"]))

    def test_unused_expired_grant_refunded_once_across_independent_processes(self):
        grant = self.reserve("never-claimed-expiry", authority=self.make_authority(ttl_seconds=3))
        self.wait_for_ttl(grant["id"])
        context = multiprocessing.get_context("spawn")
        start = context.Event()
        receivers, processes = [], []
        config = {"dsn": self.fx.dsn, "signing_key": self.fx.signing_key, "ttl_seconds": 3,
                  "org_id": str(self.org), "user_id": str(self.user), "grant_id": grant["id"],
                  "store_root": str(self.fx.store_root)}
        for _ in range(2):
            receiver, sender = context.Pipe(duplex=False)
            process = context.Process(target=_process_reconcile, args=(config, start, sender))
            process.start()
            sender.close()
            processes.append(process)
            receivers.append(receiver)
        try:
            start.set()
            messages = []
            for receiver in receivers:
                self.assertTrue(receiver.poll(10), "independent reconciler did not finish")
                messages.append(receiver.recv())
            for process in processes:
                process.join(5)
                self.assertEqual(0, process.exitcode)
            self.assertEqual(2, len({message["pid"] for message in messages}))
            self.assertFalse(any("error" in message for message in messages), messages)
        finally:
            for process in processes:
                if process.is_alive():
                    process.kill()
                    process.join(5)
            for receiver in receivers:
                receiver.close()
        self.assertEqual("expired", self.row(grant["id"])["state"])
        self.assertEqual((0, 0), (self.snapshot()["bytes"], self.snapshot()["count"]))
        with self.assertRaises(CapabilityError):
            self.reserve("never-claimed-expiry")
        with self.assertRaises(CapabilityError):
            self.make_authority().resume(self.org, self.user, grant["id"], self.source, self.store)
        replacement = self.reserve("new-request-after-expiry")
        self.assertNotEqual(grant["id"], replacement["id"])
        self.assert_charged_once()

    def test_published_receipt_with_missing_or_corrupt_object_is_blocked_not_refunded(self):
        grant = self.reserve()
        self.authority.publish(grant["authorization"], self.user, self.source, self.store)
        original = self.assert_charged_once()["receipts"]
        path = self.fx.store_root / self.key(grant)
        path.write_bytes(b"X" * len(self.data))
        with self.assertRaises(CapabilityError):
            self.make_authority().reconcile(self.org, self.user, grant["id"], self.store)
        self.assertEqual(original, self.assert_charged_once()["receipts"])

        path.unlink()
        with self.assertRaises((CapabilityError, ObjectMissing)):
            self.make_authority().reconcile(self.org, self.user, grant["id"], self.store)
        self.assertEqual(original, self.assert_charged_once()["receipts"])

    def test_wrong_existing_bytes_cannot_become_valid_receipt(self):
        grant = self.reserve()
        wrong = self.fx.root / "wrong.bin"
        wrong.write_bytes(b"X" * len(self.data))
        wrong.chmod(0o600)
        self.store.put_file(self.org, self.key(grant), wrong)
        with self.assertRaises(ObjectConflict):
            self.authority.publish(grant["authorization"], self.user, self.source, self.store)
        self.wait_for_lease(grant["id"])
        with self.assertRaises(CapabilityError):
            self.make_authority().reconcile(self.org, self.user, grant["id"], self.store)
        self.assertEqual([], self.assert_charged_once()["receipts"])
        self.assertEqual("uncertain", self.row(grant["id"])["state"])

    def test_injected_storage_metadata_mismatch_cannot_commit_receipt(self):
        # Real local files remain authoritative. Only the metadata returned at
        # the provider boundary is faulted; this is not a remote-store proof.
        delegate = self.store
        class FaultMetadata:
            def __init__(self, field, value):
                self.field, self.value = field, value
            def put_file(self, *args):
                result = delegate.put_file(*args)
                result[self.field] = self.value
                return result
            def get_file(self, *args, **kwargs):
                return delegate.get_file(*args, **kwargs)
        for field, value in (("size_bytes", len(self.data) + 1),
                             ("sha256", "f" * 64), ("generation", "wrong-provider-generation")):
            with self.subTest(field=field):
                grant = self.reserve()
                with self.assertRaises(CapabilityError):
                    self.authority.publish(grant["authorization"], self.user, self.source, FaultMetadata(field, value))
                self.assertEqual("uncertain", self.row(grant["id"])["state"])
                with connect() as db:
                    count = db.execute("SELECT count(*) AS n FROM g3a1_private.upload_receipts WHERE id=%s", (grant["id"],)).fetchone()["n"]
                self.assertEqual(0, count)
                self.assertEqual(self.digest, self.store.stat(self.org, self.key(grant))["sha256"])
        self.assertEqual((len(self.data) * 3, 3), (self.snapshot()["bytes"], self.snapshot()["count"]))

    def test_actual_database_receipt_and_audit_trigger_failures_rollback_completion(self):
        # The claim commits before storage I/O. These triggers are installed only
        # after durable publication, so failure is specifically in completion.
        for table, operation in (("upload_receipts", "INSERT"),
                                 ("upload_events", "INSERT")):
            with self.subTest(table=table):
                grant = self.reserve()
                name = "fault_" + uuid4().hex
                def install(stage, context):
                    if stage != "before_commit":
                        return
                    with connect() as db:
                        db.execute(sql.SQL("CREATE FUNCTION g3a1_private.{}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.organization_id = {}::uuid THEN RAISE EXCEPTION 'injected real DB completion rollback'; END IF; RETURN NEW; END $$").format(sql.Identifier(name), sql.Literal(str(self.org))))
                        db.execute(sql.SQL("CREATE TRIGGER {} AFTER {} ON g3a1_private.{} FOR EACH ROW EXECUTE FUNCTION g3a1_private.{}()").format(sql.Identifier(name), sql.SQL(operation), sql.Identifier(table), sql.Identifier(name)))
                try:
                    with self.assertRaises(psycopg.errors.RaiseException) as caught:
                        self.make_authority(fault_hook=install).publish(grant["authorization"], self.user, self.source, self.store)
                    self.assertIn("injected real DB completion rollback", str(caught.exception))
                    row = self.row(grant["id"])
                    self.assertNotEqual("uploaded", row["state"])
                    with connect() as db:
                        self.assertEqual(0, db.execute("SELECT count(*) AS n FROM g3a1_private.upload_receipts WHERE id=%s", (grant["id"],)).fetchone()["n"])
                    self.assertEqual(self.digest, self.store.stat(self.org, self.key(grant))["sha256"])
                finally:
                    with connect() as db:
                        db.execute(sql.SQL("DROP TRIGGER IF EXISTS {} ON g3a1_private.{}").format(sql.Identifier(name), sql.Identifier(table)))
                        db.execute(sql.SQL("DROP FUNCTION IF EXISTS g3a1_private.{}()").format(sql.Identifier(name)))
                self.wait_for_lease(grant["id"])
                self.make_authority().reconcile(self.org, self.user, grant["id"], self.store)
                self.assertEqual("uploaded", self.row(grant["id"])["state"])
        state = self.snapshot()
        self.assertEqual((len(self.data) * 2, 2, 2), (state["bytes"], state["count"], len(state["receipts"])))

    def test_actual_database_counter_trigger_failure_rolls_back_entire_reservation(self):
        name = "fault_" + uuid4().hex
        before = self.snapshot()
        with connect() as db:
            db.execute(sql.SQL("CREATE FUNCTION g3a1_private.{}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.organization_id = {}::uuid THEN RAISE EXCEPTION 'injected real quota counter rollback'; END IF; RETURN NEW; END $$").format(sql.Identifier(name), sql.Literal(str(self.org))))
            db.execute(sql.SQL("CREATE TRIGGER {} AFTER UPDATE ON g3a1_private.upload_limits FOR EACH ROW EXECUTE FUNCTION g3a1_private.{}()").format(sql.Identifier(name), sql.Identifier(name)))
        try:
            with self.assertRaises(psycopg.errors.RaiseException) as caught:
                self.reserve("counter-write-fault")
            self.assertIn("injected real quota counter rollback", str(caught.exception))
            self.assertEqual(before, self.snapshot())
            with connect() as db:
                self.assertEqual(0, db.execute("SELECT count(*) AS n FROM g3a1_private.upload_reservations WHERE organization_id=%s", (self.org,)).fetchone()["n"])
                self.assertEqual(0, db.execute("SELECT count(*) AS n FROM g3a1_private.upload_events WHERE organization_id=%s", (self.org,)).fetchone()["n"])
        finally:
            with connect() as db:
                db.execute(sql.SQL("DROP TRIGGER IF EXISTS {} ON g3a1_private.upload_limits").format(sql.Identifier(name)))
                db.execute(sql.SQL("DROP FUNCTION IF EXISTS g3a1_private.{}()").format(sql.Identifier(name)))
        self.reserve("counter-write-fault")
        self.assert_charged_once()

    def test_expiration_counter_failure_rolls_back_state_audit_and_refund(self):
        grant = self.reserve(authority=self.make_authority(ttl_seconds=3))
        self.wait_for_ttl(grant["id"])
        name = "fault_" + uuid4().hex
        with connect() as db:
            db.execute(sql.SQL("CREATE FUNCTION g3a1_private.{}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.organization_id = {}::uuid THEN RAISE EXCEPTION 'injected real expiration refund rollback'; END IF; RETURN NEW; END $$").format(sql.Identifier(name), sql.Literal(str(self.org))))
            db.execute(sql.SQL("CREATE TRIGGER {} AFTER UPDATE ON g3a1_private.upload_limits FOR EACH ROW EXECUTE FUNCTION g3a1_private.{}()").format(sql.Identifier(name), sql.Identifier(name)))
        try:
            with self.assertRaises(psycopg.errors.RaiseException) as caught:
                self.authority.expire_unused(self.org, self.user)
            self.assertIn("injected real expiration refund rollback", str(caught.exception))
            self.assertEqual("reserved", self.row(grant["id"])["state"])
            self.assert_charged_once()
            with connect() as db:
                self.assertEqual(0, db.execute("SELECT count(*) AS n FROM g3a1_private.upload_events WHERE reservation_id=%s AND event_type='expired'", (grant["id"],)).fetchone()["n"])
        finally:
            with connect() as db:
                db.execute(sql.SQL("DROP TRIGGER IF EXISTS {} ON g3a1_private.upload_limits").format(sql.Identifier(name)))
                db.execute(sql.SQL("DROP FUNCTION IF EXISTS g3a1_private.{}()").format(sql.Identifier(name)))
        self.assertEqual(1, self.authority.expire_unused(self.org, self.user))
        self.assertEqual(0, self.authority.expire_unused(self.org, self.user))
        self.assertEqual("expired", self.row(grant["id"])["state"])
        self.assertEqual((0, 0), (self.snapshot()["bytes"], self.snapshot()["count"]))
