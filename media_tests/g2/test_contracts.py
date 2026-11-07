"""Real PostgreSQL security checks for the additive G2 surface."""
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

import psycopg

from engine.cli import demo
from g1.support import connect
from g2.support import PipelineFixture


class MediaContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        paths = demo(Path(cls.temp.name), "demo")
        cls.video, cls.caption = Path(paths["video"]), Path(paths["caption"])

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.f = PipelineFixture(self.video, self.caption)
        self.addCleanup(self.f.cleanup)

    def test_g2_rls_and_direct_mutation_denials_both_tenants(self):
        assets = [self.f.ingest(), self.f.ingest(other=True)]
        jobs = [self.f.submit_media(assets[0]), self.f.submit_media(assets[1], other=True)]
        for index, (actor, org) in enumerate(((self.f.user_a, self.f.org_a), (self.f.user_b, self.f.org_b))):
            with connect("authenticated", actor) as db:
                for table in ("media_bindings", "media_job_specs"):
                    rows = db.execute(f"SELECT organization_id FROM public.{table}").fetchall()
                    self.assertEqual({org}, {row["organization_id"] for row in rows})
            for statement in (
                "DELETE FROM public.media_bindings WHERE asset_id=%s",
                "UPDATE public.media_bindings SET caption_key=NULL WHERE asset_id=%s",
            ):
                with self.assertRaises(psycopg.errors.InsufficientPrivilege), connect("authenticated", actor) as db:
                    db.execute(statement, (assets[1-index]["id"],))
            with self.assertRaises(psycopg.errors.InsufficientPrivilege), connect("authenticated", actor) as db:
                db.execute("SELECT * FROM public.media_attempt_metrics")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege), connect("authenticated", actor) as db:
                db.execute("INSERT INTO public.media_job_specs(job_id,organization_id,asset_id,media_digest) VALUES(%s,%s,%s,%s)",
                           (jobs[1-index]["id"], org, assets[index]["id"], "a"*64))

    def test_g2_submit_requires_identity_write_role_and_same_org_binding(self):
        asset = self.f.ingest()
        for role, actor, org in (("anon", None, self.f.org_a),
                                 ("authenticated", None, self.f.org_a),
                                 ("authenticated", self.f.user_b, self.f.org_a),
                                 ("authenticated", self.f.user_b, self.f.org_b)):
            with self.subTest(role=role, actor=actor, org=org), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with connect(role, actor) as db:
                    db.execute("SELECT * FROM public.submit_media_render(%s,'denied',%s,800,'smoke')", (org, asset["id"]))
        with connect() as db:
            db.execute("UPDATE public.memberships SET role='viewer' WHERE user_id=%s", (self.f.user_a,))
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.f.submit_media(asset)
        self.assertEqual({"jobs": 0, "outbox": 0, "audit": 0, "reserved": 0}, self.f.counts())

    def test_composite_binding_and_job_foreign_keys_reject_cross_org_writers(self):
        asset = self.f.ingest()
        job = self.f.submit_media(asset)
        with self.assertRaises(psycopg.errors.ForeignKeyViolation), connect() as db:
            db.execute("INSERT INTO public.media_bindings(asset_id,organization_id,binding_digest) VALUES(%s,%s,%s)",
                       (self.f.asset_b, self.f.org_a, "a"*64))
        with self.assertRaises(psycopg.errors.ForeignKeyViolation), connect() as db:
            db.execute("INSERT INTO public.media_job_specs(job_id,organization_id,asset_id,media_digest) VALUES(%s,%s,%s,%s)",
                       (uuid4(), self.f.org_b, asset["id"], "a"*64))
        with self.assertRaises(psycopg.errors.ForeignKeyViolation), connect() as db:
            db.execute("INSERT INTO public.media_attempt_metrics(job_id,organization_id,fence,stage,elapsed_ms,read_bytes,written_bytes) VALUES(%s,%s,1,'forbidden',0,0,0)",
                       (job["id"], self.f.org_b))
