"""Real PostgreSQL and immutable disk checks for bounded publication leases."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import threading
import time
import unittest

import psycopg
from psycopg.rows import dict_row

from engine.object_store import LocalObjectStore
from engine.shared_upload_authority import PostgresUploadAuthority, UploadBusy
from g1.support import connect
from g3a1.support import SharedHTTPFixture


class _PersistThenWaitStore(LocalObjectStore):
    """Actual immutable disk publication followed by a bounded I/O pause."""

    def __init__(self, root, written, release):
        super().__init__(root)
        self.written, self.release = written, release

    def put_file(self, org_id, key, source):
        result = super().put_file(org_id, key, source)
        self.written.set()
        if not self.release.wait(6):
            raise TimeoutError("isolated test publication pause exceeded its bound")
        return result


class UploadDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.fx = SharedHTTPFixture(ttl_seconds=30, lease_seconds=1)
        self.addCleanup(self.fx.cleanup)
        self.org, self.user = self.fx.f.org_a, self.fx.f.user_a
        self.data = b"synthetic deadline payload, never a validated media asset"
        self.sha = hashlib.sha256(self.data).hexdigest()
        self.source = self.fx.root / "source.bin"
        self.source.write_bytes(self.data)
        self.source.chmod(0o600)

    def authority(self, **kwargs):
        return PostgresUploadAuthority(self.fx.dsn, self.fx.signing_key, **kwargs)

    def reserve(self, authority):
        return authority.reserve(self.org, self.user, request_key="bounded-publication",
                                 size_bytes=len(self.data), sha256=self.sha)

    def row(self, grant):
        with connect() as db:
            return db.execute("SELECT *,lease_expires_at<=clock_timestamp() AS lease_expired "
                              "FROM g3a1_private.upload_reservations WHERE id=%s", (grant["id"],)).fetchone()

    def assert_conservation(self):
        with connect() as db:
            quota = db.execute("SELECT used_bytes,used_count FROM g3a1_private.upload_limits "
                               "WHERE organization_id=%s", (self.org,)).fetchone()
            count = db.execute("SELECT count(*) AS n FROM g3a1_private.upload_receipts "
                               "WHERE organization_id=%s", (self.org,)).fetchone()["n"]
        self.assertEqual((len(self.data), 1), (quota["used_bytes"], quota["used_count"]))
        return count

    def test_expired_operation_budget_stops_renewal_and_fences_late_writer(self):
        authority = self.authority(lease_seconds=1, operation_timeout_seconds=1)
        grant = self.reserve(authority)
        written, release = threading.Event(), threading.Event()
        store = _PersistThenWaitStore(self.fx.store_root, written, release)
        with ThreadPoolExecutor(max_workers=1) as pool:
            attempt = pool.submit(authority.publish, grant["authorization"], self.user, self.source, store)
            try:
                self.assertTrue(written.wait(3), "real immutable object publication did not finish")
                old = self.row(grant)
                self.assertEqual(self.sha, self.fx.store.stat(self.org, old["object_key"])["sha256"])
                deadline = time.monotonic() + 4
                while not self.row(grant)["lease_expired"] and time.monotonic() < deadline:
                    time.sleep(.03)
                self.assertTrue(self.row(grant)["lease_expired"], "renewals continued after operation budget")
                self.assertEqual(0, self.assert_conservation())
            finally:
                release.set()
            with self.assertRaises(UploadBusy):
                attempt.result(timeout=3)
        recovered = self.authority(lease_seconds=1).reconcile(self.org, self.user, grant["id"], self.fx.store)
        self.assertEqual("uploaded_pending_validation", recovered["state"])
        self.assertEqual(1, self.assert_conservation())
        self.assertGreater(self.row(grant)["fence"], old["fence"])
        with self.assertRaises(psycopg.Error) as rejected:
            with psycopg.connect(self.fx.dsn, row_factory=dict_row) as db:
                db.execute("SET LOCAL ROLE authenticated")
                db.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (str(self.user),))
                db.execute("SELECT g3a1_private.complete_upload(%s,%s,%s,%s,%s,%s,%s,NULL)",
                           (self.org, grant["id"], old["lease_token"], old["fence"], old["object_key"], len(self.data), self.sha))
        self.assertEqual("40001", rejected.exception.sqlstate)

    def test_budget_rechecked_after_before_commit_failure_checkpoint(self):
        def pause(stage, _):
            if stage == "before_commit":
                time.sleep(1.1)
        authority = self.authority(lease_seconds=2, operation_timeout_seconds=1, fault_hook=pause)
        grant = self.reserve(authority)
        with self.assertRaises(UploadBusy):
            authority.publish(grant["authorization"], self.user, self.source, self.fx.store)
        self.assertEqual(0, self.assert_conservation())
        recovered = self.authority(lease_seconds=1).reconcile(self.org, self.user, grant["id"], self.fx.store)
        self.assertEqual("uploaded_pending_validation", recovered["state"])
        self.assertEqual(1, self.assert_conservation())

    def test_database_clamped_one_second_lease_is_renewed_from_effective_duration(self):
        with connect() as db:
            db.execute("UPDATE g3a1_private.upload_limits SET max_lease_seconds=1 WHERE organization_id=%s", (self.org,))
        authority = self.authority(lease_seconds=30, operation_timeout_seconds=5)
        grant = self.reserve(authority)
        written, release = threading.Event(), threading.Event()
        store = _PersistThenWaitStore(self.fx.store_root, written, release)
        with ThreadPoolExecutor(max_workers=1) as pool:
            attempt = pool.submit(authority.publish, grant["authorization"], self.user, self.source, store)
            try:
                self.assertTrue(written.wait(3))
                original_fence = self.row(grant)["fence"]
                time.sleep(1.2)
                self.assertFalse(self.row(grant)["lease_expired"], "SQL-clamped lease was not renewed in time")
            finally:
                release.set()
            receipt = attempt.result(timeout=3)
        self.assertEqual("uploaded_pending_validation", receipt["state"])
        self.assertEqual(original_fence, self.row(grant)["fence"])
        self.assertEqual(1, self.assert_conservation())
