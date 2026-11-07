"""Independent synthetic PostgreSQL/local-store review probes; outside source."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import multiprocessing
import os
from pathlib import Path
import signal
import threading
import time
import unittest

from engine.object_store import LocalObjectStore, ObjectMissing
from engine.shared_upload_authority import UploadBusy
from g1.support import connect
from g3a1.support import SharedHTTPFixture
from g3a1.test_ledger_failures import _paused_process_publish


class Probe(unittest.TestCase):
    def setUp(self):
        self.fx = SharedHTTPFixture(ttl_seconds=30, lease_seconds=1)
        self.addCleanup(self.fx.cleanup)
        self.org, self.user = self.fx.f.org_a, self.fx.f.user_a
        self.body = b"independent synthetic backend probe: immutable bytes"
        self.sha = hashlib.sha256(self.body).hexdigest()
        self.source = self.fx.root / "probe-source.bin"
        self.source.write_bytes(self.body)
        self.source.chmod(0o600)

    def reserve(self, authority, key):
        return authority.reserve(self.org, self.user, request_key=key,
                                 size_bytes=len(self.body), sha256=self.sha)

    def row(self, grant):
        with connect() as db:
            return db.execute("SELECT *, lease_expires_at<=clock_timestamp() AS expired "
                              "FROM g3a1_private.upload_reservations WHERE id=%s",
                              (grant["id"],)).fetchone()

    def evidence(self, grant):
        with connect() as db:
            q = db.execute("SELECT used_bytes,used_count FROM g3a1_private.upload_limits "
                           "WHERE organization_id=%s", (self.org,)).fetchone()
            rows = db.execute("SELECT id,object_key,size_bytes,sha256,generation,uploaded_at "
                              "FROM g3a1_private.upload_receipts WHERE id=%s", (grant["id"],)).fetchall()
        self.assertEqual({"used_bytes": len(self.body), "used_count": 1}, q)
        return rows

    def test_actual_heartbeat_lock_failure_fails_closed_and_recovers(self):
        entered, release = threading.Event(), threading.Event()
        def checkpoint(stage, _):
            if stage == "after_store":
                entered.set()
                if not release.wait(8):
                    raise TimeoutError("review barrier")
        a = self.fx.authority(lease_seconds=3, operation_timeout_seconds=10, fault_hook=checkpoint)
        grant = self.reserve(a, "heartbeat-lock-failure")
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(a.publish, grant["authorization"], self.user, self.source, self.fx.store)
            try:
                self.assertTrue(entered.wait(3))
                with connect() as blocker:
                    blocker.execute("SELECT used_bytes FROM g3a1_private.upload_limits "
                                    "WHERE organization_id=%s FOR UPDATE", (self.org,))
                    deadline = time.monotonic() + 5
                    seen = False
                    while time.monotonic() < deadline:
                        blocker.execute("SELECT pg_stat_clear_snapshot()")
                        seen = blocker.execute("SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                            "WHERE usename=%s AND wait_event_type='Lock' "
                            "AND query LIKE '%%heartbeat_upload_before%%') AS seen", (self.fx.login,)).fetchone()["seen"]
                        if seen:
                            break
                        time.sleep(.02)
                    self.assertTrue(seen, "real heartbeat must wait on lock")
                    time.sleep(2.2)  # Exceeds the adapter's actual 2 s lock_timeout.
                    self.assertEqual([], self.evidence(grant))
                release.set()
                with self.assertRaises(UploadBusy):
                    pending.result(timeout=4)
            finally:
                release.set()
        self.assertEqual([], self.evidence(grant))
        recovered = self.fx.authority().reconcile(self.org, self.user, grant["id"], self.fx.store)
        self.assertEqual("uploaded_pending_validation", recovered["state"])
        self.assertEqual(1, len(self.evidence(grant)))

    def test_old_object_writer_cannot_change_new_valid_receipt(self):
        entered, release = threading.Event(), threading.Event()
        base = self.fx.store
        class PauseBeforeImmutablePut:
            def put_file(inner, *args):
                entered.set()
                if not release.wait(8):
                    raise TimeoutError("review barrier")
                return base.put_file(*args)
            def get_file(inner, *args, **kwargs):
                return base.get_file(*args, **kwargs)
        old = self.fx.authority(lease_seconds=1, operation_timeout_seconds=1)
        grant = self.reserve(old, "stale-object-writer")
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(old.publish, grant["authorization"], self.user, self.source, PauseBeforeImmutablePut())
            try:
                self.assertTrue(entered.wait(3))
                time.sleep(1.2)
                receipt = self.fx.authority().resume(self.org, self.user, grant["id"], self.source, base)
                before = self.evidence(grant)
                current = self.row(grant)
                self.assertEqual(2, current["fence"])
                release.set()
                with self.assertRaises(UploadBusy):
                    pending.result(timeout=4)
            finally:
                release.set()
        self.assertEqual(before, self.evidence(grant))
        self.assertEqual(receipt, self.fx.authority().verified_receipt(self.org, self.user, grant["id"], base))
        self.assertEqual("uploaded", self.row(grant)["state"])

    def test_sql_receipt_trigger_wait_expires_and_rolls_back(self):
        installed = False
        name = "review_receipt_deadline"
        def checkpoint(stage, _):
            nonlocal installed
            if stage != "before_commit":
                return
            with connect() as db:
                db.execute("CREATE FUNCTION g3a1_private.review_receipt_deadline() RETURNS trigger "
                           "LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(1.2); RETURN NEW; END $$")
                db.execute("CREATE TRIGGER review_receipt_deadline AFTER INSERT ON "
                           "g3a1_private.upload_receipts FOR EACH ROW EXECUTE FUNCTION "
                           "g3a1_private.review_receipt_deadline()")
            installed = True
        authority = self.fx.authority(lease_seconds=3, operation_timeout_seconds=1, fault_hook=checkpoint)
        grant = self.reserve(authority, "receipt-trigger-deadline")
        try:
            with self.assertRaises(UploadBusy):
                authority.publish(grant["authorization"], self.user, self.source, self.fx.store)
            self.assertEqual([], self.evidence(grant))
            self.assertNotEqual("uploaded", self.row(grant)["state"])
        finally:
            if installed:
                with connect() as db:
                    db.execute("DROP TRIGGER review_receipt_deadline ON g3a1_private.upload_receipts")
                    db.execute("DROP FUNCTION g3a1_private.review_receipt_deadline()")
        self.assertEqual("uploaded_pending_validation", self.fx.authority().reconcile(
            self.org, self.user, grant["id"], self.fx.store)["state"])
        self.assertEqual(1, len(self.evidence(grant)))

    def test_claim_before_ttl_can_finish_under_live_lease_after_original_ttl(self):
        def checkpoint(stage, _):
            if stage == "after_store":
                time.sleep(1.2)
        authority = self.fx.authority(ttl_seconds=1, lease_seconds=1,
                                      operation_timeout_seconds=4, fault_hook=checkpoint)
        grant = self.reserve(authority, "admitted-before-ttl")
        result = authority.publish(grant["authorization"], self.user, self.source, self.fx.store)
        self.assertEqual("uploaded_pending_validation", result["state"])
        self.assertEqual(1, len(self.evidence(grant)))
        self.assertEqual(result, self.fx.authority().resume(self.org, self.user, grant["id"], self.source, self.fx.store))

    def test_sigkill_after_sql_commit_before_client_result_replays(self):
        authority = self.fx.authority()
        grant = self.reserve(authority, "sigkill-after-commit")
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(target=_paused_process_publish, args=({
            "dsn": self.fx.dsn, "signing_key": self.fx.signing_key,
            "ttl_seconds": 30, "token": grant["authorization"], "user_id": str(self.user),
            "source": str(self.source), "store_root": str(self.fx.store_root),
            "checkpoint": "after_commit"}, sender))
        process.start()
        sender.close()
        try:
            self.assertTrue(receiver.poll(8), "child must commit before SIGKILL")
            message = receiver.recv()
            self.assertEqual("after_commit", message.get("checkpoint"), message)
            self.assertNotEqual(os.getpid(), message["pid"])
            before = self.evidence(grant)
            self.assertEqual(1, len(before))
            process.kill()
            process.join(5)
            self.assertEqual(-signal.SIGKILL, process.exitcode)
            fresh = self.fx.authority()
            recovered = fresh.publish(grant["authorization"], self.user, self.source,
                                      LocalObjectStore(self.fx.store_root))
            self.assertEqual("uploaded_pending_validation", recovered["state"])
            self.assertEqual(before, self.evidence(grant))
            self.assertEqual(1, self.row(grant)["fence"])
        finally:
            if process.is_alive():
                process.kill()
                process.join(5)
            receiver.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
