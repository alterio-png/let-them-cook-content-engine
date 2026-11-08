"""Real PostgreSQL + local objects + native ffprobe ingestion contracts.

These focused internal SQL tests use the existing G1 auth.uid fixture. Separate
browser/HTTP suites exercise official GoTrue authentication end to end; these
tests make no hosted/cloud authentication claim.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from engine.cli import demo
from engine.local_ingest import LocalIngestWorker
from engine.object_store import LocalObjectStore
from engine.shared_upload_authority import PostgresUploadAuthority
from g1.support import Fixture, connect


class SimulatedProcessDeath(BaseException):
    pass


class IngestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media_dir = tempfile.TemporaryDirectory(prefix="ltc-r0-ingest-fixtures-")
        generated = demo(Path(cls.media_dir.name), "demo")
        cls.video, cls.caption = Path(generated["video"]), Path(generated["caption"])

    @classmethod
    def tearDownClass(cls):
        cls.media_dir.cleanup()

    def setUp(self):
        self.f = Fixture()
        self.temp = tempfile.TemporaryDirectory(prefix="ltc-r0-ingest-test-")
        self.root = Path(self.temp.name)
        self.store = LocalObjectStore(self.root / "objects", max_object_bytes=1048576)
        self.api_login, self.service_login = "r0_api_" + uuid4().hex, "r0_ingest_" + uuid4().hex
        with connect() as db:
            for login, role in ((self.api_login, "authenticated"), (self.service_login, "g3br0_ingester")):
                db.execute(sql.SQL("CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS "
                                   "NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD 'r0-disposable-only'")
                           .format(sql.Identifier(login)))
                db.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(role), sql.Identifier(login)))
            for org in (self.f.org_a, self.f.org_b):
                db.execute("INSERT INTO g3a1_private.upload_limits(organization_id,max_bytes,max_count,max_upload_bytes) "
                           "VALUES(%s,8388608,32,1048576)", (org,))
        self.api_dsn = make_conninfo(os.environ["G1_TEST_DSN"], user=self.api_login, password="r0-disposable-only")
        self.service_dsn = make_conninfo(os.environ["G1_TEST_DSN"], user=self.service_login, password="r0-disposable-only")
        self.authority = PostgresUploadAuthority(self.api_dsn, b"r0-test-local-signing-key-" * 2)

    def tearDown(self):
        with connect() as db:
            orgs = [self.f.org_a, self.f.org_b]
            for table in ("ingest_provenance", "ingest_requests", "synthetic_allowlist"):
                db.execute(sql.SQL("DELETE FROM g3br0_private.{} WHERE organization_id=ANY(%s)")
                           .format(sql.Identifier(table)), (orgs,))
            db.execute("DELETE FROM public.media_bindings WHERE organization_id=ANY(%s)", (orgs,))
            for table in ("upload_events", "upload_receipts", "upload_reservations", "upload_limits"):
                db.execute(sql.SQL("DELETE FROM g3a1_private.{} WHERE organization_id=ANY(%s)")
                           .format(sql.Identifier(table)), (orgs,))
        self.f.cleanup()
        with connect() as db:
            for login in (self.api_login, self.service_login):
                db.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(login)))
        self.temp.cleanup()

    def upload(self, path, key, *, org=None, user=None):
        user, org = user or self.f.user_a, org or self.f.org_a
        grant = self.authority.reserve(org, user, request_key=key,
            size_bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        receipt = self.authority.publish(grant["authorization"], user, path, self.store)
        return receipt

    def pair(self, *, video=None, caption=None, approve=True):
        video, caption = video or self.video, caption or self.caption
        v, s = self.upload(video, "video"), self.upload(caption, "caption")
        if approve:
            # Trusted administrative test setup; never an end-user capability.
            with connect() as db:
                db.execute("INSERT INTO g3br0_private.synthetic_allowlist(organization_id,fixture_id,video_sha256,video_bytes,"
                           "caption_sha256,caption_bytes,provenance) VALUES(%s,'local-test',%s,%s,%s,%s,%s)",
                           (self.f.org_a, v["sha256"], v["size_bytes"], s["sha256"], s["size_bytes"],
                            Jsonb({"generator": "engine.cli.demo", "synthetic": True})))
        return v, s

    def request(self, video, caption, *, key="ingest", org=None, user=None):
        with connect("authenticated", user or self.f.user_a) as db:
            return db.execute("SELECT g3br0_private.request_ingest(%s,%s,%s,%s) AS r",
                              (org or self.f.org_a, key, video["id"], caption["id"])).fetchone()["r"]

    def worker(self, **kwargs):
        return LocalIngestWorker(self.service_dsn, self.store, self.root / "scratch", **kwargs)

    def row(self, request):
        with connect() as db:
            return db.execute("SELECT * FROM g3br0_private.ingest_requests WHERE id=%s", (request["id"],)).fetchone()

    def quota(self):
        with connect() as db:
            return db.execute("SELECT used_bytes,used_count FROM g3a1_private.upload_limits WHERE organization_id=%s",
                              (self.f.org_a,)).fetchone()

    def test_real_ffprobe_and_srt_create_one_immutable_g2_binding(self):
        v, s = self.pair()
        request = self.request(v, s)
        self.assertEqual(request, self.request(v, s))
        result = self.worker().run_once()
        self.assertEqual(result["state"], "completed")
        self.assertTrue(result["metadata"]["has_audio"])
        self.assertGreater(result["metadata"]["duration_ms"], 2000)
        with connect() as db:
            row = db.execute("SELECT a.*,b.binding_digest,p.video_receipt_id,p.caption_receipt_id "
                             "FROM public.assets a JOIN public.media_bindings b ON b.asset_id=a.id "
                             "JOIN g3br0_private.ingest_provenance p ON p.asset_id=a.id WHERE a.id=%s",
                             (result["asset_id"],)).fetchone()
            self.assertEqual(str(row["video_receipt_id"]), v["id"])
            self.assertEqual(str(row["caption_receipt_id"]), s["id"])
            self.assertEqual(row["binding_digest"], result["metadata"]["binding_digest"])
            self.assertEqual((row["width"], row["height"]), (640, 360))
        with connect("authenticated", self.f.user_a) as db:
            read = db.execute("SELECT g3br0_private.get_ingest(%s,%s) AS r", (self.f.org_a, request["id"])).fetchone()["r"]
            self.assertEqual(read["asset_id"], result["asset_id"])
        self.assertIsNone(self.worker().run_once())

    def test_concurrent_idempotent_requests_and_workers_single_result(self):
        v, s = self.pair()
        with ThreadPoolExecutor(max_workers=8) as pool:
            requests = list(pool.map(lambda _: self.request(v, s), range(16)))
        self.assertEqual(len({r["id"] for r in requests}), 1)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.worker().run_once(), range(4)))
        self.assertEqual(sum(r is not None and r["state"] == "completed" for r in results), 1)
        self.assertEqual(self.row(requests[0])["attempt_count"], 1)

    def test_cross_organization_and_owner_receipts_denied(self):
        v, s = self.pair()
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.request(v, s, org=self.f.org_b, user=self.f.user_b)
        with connect() as db:
            db.execute("INSERT INTO public.memberships(organization_id,user_id,role) VALUES(%s,%s,'editor')",
                       (self.f.org_a, self.f.user_b))
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.request(v, s, user=self.f.user_b)
        request = self.request(v, s)
        with connect("authenticated", self.f.user_b) as db:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                db.execute("SELECT g3br0_private.get_ingest(%s,%s)", (self.f.org_b, request["id"]))

    def test_unapproved_pair_and_invalid_media_retain_upload_charges(self):
        v, s = self.pair(approve=False)
        self.request(v, s)
        charged = self.quota()
        self.assertEqual(self.worker().run_once()["error_code"], "synthetic_not_approved")
        self.assertEqual(self.quota(), charged)
        self.assertEqual(len(self.store.list_keys(self.f.org_a)), 2)

    def test_approved_malformed_video_rejected_by_real_probe(self):
        invalid = self.root / "invalid.mp4"
        invalid.write_bytes(b"\x00\x00\x00\x18ftyp" + b"invalid actual MP4 payload")
        v, s = self.pair(video=invalid)
        self.request(v, s)
        result = self.worker().run_once()
        self.assertEqual((result["state"], result["error_code"]), ("failed", "invalid_media"))

    def test_approved_invalid_srt_rejected(self):
        invalid = self.root / "invalid.srt"
        invalid.write_text("not a supplied SRT cue", encoding="utf-8")
        v, s = self.pair(caption=invalid)
        self.request(v, s)
        self.assertEqual(self.worker().run_once()["error_code"], "invalid_media")

    def test_corrupt_quarantine_detected_and_never_imported(self):
        v, s = self.pair()
        self.request(v, s)
        with connect() as db:
            key = db.execute("SELECT object_key FROM g3a1_private.upload_receipts WHERE id=%s", (v["id"],)).fetchone()["object_key"]
        target = self.store.root / key
        target.write_bytes(b"X" * target.stat().st_size)
        result = self.worker().run_once()
        self.assertEqual((result["state"], result["error_code"]), ("failed", "integrity_mismatch"))
        self.assertFalse(any("/assets/" in key for key in self.store.list_keys(self.f.org_a)))

    def test_interruption_after_publish_recovers_with_new_fence_and_stable_keys(self):
        v, s = self.pair()
        request = self.request(v, s)
        captured = []
        def die(stage, claim):
            if stage == "after_publish":
                captured.append(claim)
                raise SimulatedProcessDeath()
        with self.assertRaises(SimulatedProcessDeath):
            self.worker(fault_hook=die).run_once()
        old = captured[0]
        with connect() as db:
            self.assertEqual(db.execute("SELECT count(*) AS n FROM public.assets WHERE id=%s", (old["asset_id"],)).fetchone()["n"], 0)
            db.execute("UPDATE g3br0_private.ingest_requests SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",
                       (request["id"],))
        charged = self.quota()
        result = self.worker().run_once()
        self.assertEqual(result["state"], "completed")
        self.assertEqual(self.row(request)["fence"], old["fence"] + 1)
        self.assertEqual(self.quota(), charged)
        with self.assertRaises(psycopg.errors.ObjectNotInPrerequisiteState):
            self.worker()._call("complete_ingest", (*self.worker()._identity(old), Jsonb(result["metadata"])))
        self.assertEqual(len(self.store.list_keys(self.f.org_a)), 4)

    def test_membership_revoked_before_commit_blocks_publication(self):
        v, s = self.pair()
        request = self.request(v, s)
        def revoke(stage, claim):
            if stage == "before_complete":
                with connect() as db:
                    db.execute("DELETE FROM public.memberships WHERE organization_id=%s AND user_id=%s",
                               (self.f.org_a, self.f.user_a))
        result = self.worker(fault_hook=revoke).run_once()
        self.assertEqual(result["state"], "stale")
        with connect() as db:
            self.assertEqual(db.execute("SELECT count(*) AS n FROM public.assets WHERE id=%s",
                                        (self.row(request)["asset_id"],)).fetchone()["n"], 0)

    def test_api_cannot_seed_approval_or_finalize_and_owner_login_rejected(self):
        for query in ("SELECT * FROM g3br0_private.synthetic_allowlist",
                      "SELECT g3br0_private.claim_ingest(30)",
                      "INSERT INTO public.assets(storage_key,sha256,bytes) VALUES('x',repeat('a',64),1)"):
            with psycopg.connect(self.api_dsn) as db:
                db.execute("SET LOCAL ROLE authenticated")
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    db.execute(query)
        with self.assertRaises(ValueError):
            LocalIngestWorker(os.environ["G1_TEST_DSN"], self.store)
        with self.assertRaises(ValueError):
            LocalIngestWorker(self.api_dsn, self.store)

    def test_ingester_login_with_column_dml_grant_rejected(self):
        with connect() as db:
            db.execute(sql.SQL("GRANT INSERT(width) ON public.assets TO {}").format(sql.Identifier(self.service_login)))
        try:
            with self.assertRaises(ValueError):
                self.worker()
        finally:
            with connect() as db:
                db.execute(sql.SQL("REVOKE INSERT(width) ON public.assets FROM {}").format(sql.Identifier(self.service_login)))


if __name__ == "__main__":
    unittest.main()
