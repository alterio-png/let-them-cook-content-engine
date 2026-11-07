import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
from psycopg import sql

from g1.support import Fixture, connect


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.addCleanup(self.f.cleanup)

    def test_100_simultaneous_submissions_create_one_logical_operation(self):
        barrier = threading.Barrier(100)

        def submit(_):
            # All 100 sessions are connected before the barrier opens.
            with connect("authenticated", self.f.user_a) as conn:
                barrier.wait(timeout=30)
                return conn.execute("SELECT * FROM public.submit_render(%s,'race',%s,1200,'smoke')",
                                    (self.f.org_a, self.f.asset_a)).fetchone()

        with ThreadPoolExecutor(max_workers=100) as pool:
            rows = list(pool.map(submit, range(100)))
        self.assertEqual(1, len({r["id"] for r in rows}))
        self.assertEqual(1, len({r["request_hash"] for r in rows}))
        self.assertEqual({"jobs": 1, "outbox": 1, "audit": 1, "reserved": 6}, self.f.counts())

    def test_conflicting_key_and_lost_submission_response(self):
        committed = self.f.submit()
        # Discarding a response and resubmitting returns authoritative committed state.
        self.assertEqual(committed["id"], self.f.submit()["id"])
        for changed in ({"duration_ms": 1000}, {"quality": "production"}):
            with self.subTest(changed=changed), self.assertRaises(psycopg.Error):
                self.f.submit(**changed)
        self.assertEqual({"jobs": 1, "outbox": 1, "audit": 1, "reserved": 6}, self.f.counts())

    def test_roles_are_not_superuser_or_rls_bypass(self):
        for role in ("authenticated", "anon", "engine_worker"):
            with self.subTest(role=role), connect(role) as conn:
                row = conn.execute("SELECT current_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user").fetchone()
                self.assertEqual(role, row["current_user"])
                self.assertFalse(row["rolsuper"])
                self.assertFalse(row["rolbypassrls"])

    def test_both_tenants_read_only_their_own_rows(self):
        job_a = self.f.submit()
        job_b = self.f.submit(org=self.f.org_b, user=self.f.user_b, asset=self.f.asset_b)
        for user, org, asset, job in (
            (self.f.user_a, self.f.org_a, self.f.asset_a, job_a),
            (self.f.user_b, self.f.org_b, self.f.asset_b, job_b),
        ):
            with self.subTest(user=user), connect("authenticated", user) as conn:
                for table, column, expected in (("organizations", "id", org), ("assets", "id", asset),
                                               ("render_jobs", "id", job["id"]),
                                               ("org_quotas", "organization_id", org),
                                               ("audit_events", "organization_id", org)):
                    rows = conn.execute(sql.SQL("SELECT {} AS value FROM public.{}").format(
                        sql.Identifier(column), sql.Identifier(table))).fetchall()
                    self.assertTrue(rows)
                    self.assertEqual({expected}, {r["value"] for r in rows})

    def test_tenants_cannot_submit_to_or_reference_another_org(self):
        for kwargs in ({"org": self.f.org_b, "asset": self.f.asset_b}, {"asset": self.f.asset_b}):
            with self.subTest(kwargs=kwargs), self.assertRaises(psycopg.Error):
                self.f.submit(**kwargs)
        self.assertEqual({"jobs": 0, "outbox": 0, "audit": 0, "reserved": 0}, self.f.counts())

    def test_authenticated_direct_mutations_and_service_tables_forbidden(self):
        job = self.f.submit(org=self.f.org_b, user=self.f.user_b, asset=self.f.asset_b)
        for statement, params in (
            ("UPDATE public.assets SET bytes=1 WHERE id=%s", (self.f.asset_b,)),
            ("DELETE FROM public.assets WHERE id=%s", (self.f.asset_b,)),
            ("UPDATE public.render_jobs SET state='completed' WHERE id=%s", (job["id"],)),
            ("DELETE FROM public.render_jobs WHERE id=%s", (job["id"],)),
            ("INSERT INTO public.assets(organization_id,storage_key,sha256,bytes) VALUES (%s,'forbidden',%s,1)", (self.f.org_b, "b" * 64)),
            ("UPDATE public.org_quotas SET reserved_cents=0 WHERE organization_id=%s", (self.f.org_a,)),
            ("SELECT * FROM public.render_outbox", ()),
            ("SELECT * FROM public.render_attempts", ()),
        ):
            with self.subTest(statement=statement), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with connect("authenticated", self.f.user_a) as conn:
                    conn.execute(statement, params)

    def test_service_roles_and_function_grants_do_not_leak(self):
        with connect() as conn:
            self.assertFalse(conn.execute("SELECT pg_has_role('authenticated','engine_worker','MEMBER') AS allowed").fetchone()["allowed"])
            self.assertFalse(conn.execute("SELECT has_function_privilege('anon','public.submit_render(uuid,text,uuid,integer,text)','EXECUTE') AS allowed").fetchone()["allowed"])
            for function in ("public.guard_render_request()", "public.guard_asset_identity()"):
                self.assertFalse(conn.execute("SELECT has_function_privilege('authenticated',%s,'EXECUTE') AS allowed", (function,)).fetchone()["allowed"])

    def test_no_identity_anon_viewer_or_revoked_membership(self):
        for role, user in (("authenticated", None), ("anon", None)):
            with self.subTest(role=role), self.assertRaises(psycopg.Error):
                with connect(role, user) as conn:
                    conn.execute("SELECT * FROM public.submit_render(%s,'missing',%s,1200,'smoke')", (self.f.org_a, self.f.asset_a))
        with connect() as conn:
            conn.execute("UPDATE public.memberships SET role='viewer' WHERE user_id=%s", (self.f.user_a,))
        with self.assertRaises(psycopg.Error):
            self.f.submit()
        with connect() as conn:
            conn.execute("DELETE FROM public.memberships WHERE user_id=%s", (self.f.user_a,))
        with self.assertRaises(psycopg.Error):
            self.f.submit()

    def test_cumulative_budget_and_duplicate_do_not_charge_twice(self):
        with connect() as conn:
            conn.execute("UPDATE public.org_quotas SET budget_cents=9 WHERE organization_id=%s", (self.f.org_a,))
        self.f.submit("first", duration_ms=1000)
        self.f.submit("second", duration_ms=1200)
        self.f.submit("second", duration_ms=1200)
        with self.assertRaises(psycopg.Error):
            self.f.submit("over", duration_ms=1000)
        self.assertEqual({"jobs": 2, "outbox": 2, "audit": 2, "reserved": 9}, self.f.counts())

    def test_budget_is_atomic_under_concurrent_distinct_keys(self):
        with connect() as conn:
            conn.execute("UPDATE public.org_quotas SET budget_cents=6 WHERE organization_id=%s", (self.f.org_a,))
        barrier = threading.Barrier(20)

        def submit(i):
            barrier.wait(timeout=20)
            try:
                self.f.submit(f"unique-{i}", duration_ms=1000)
                return True
            except psycopg.Error:
                return False

        with ThreadPoolExecutor(max_workers=20) as pool:
            outcomes = list(pool.map(submit, range(20)))
        self.assertEqual(2, sum(outcomes))
        self.assertEqual({"jobs": 2, "outbox": 2, "audit": 2, "reserved": 6}, self.f.counts())

    def test_input_caps_and_rights_checked_before_reservation(self):
        for kwargs in ({"duration_ms": 0}, {"duration_ms": -1}, {"duration_ms": 5001},
                       {"duration_ms": None}, {"quality": "unbounded"}, {"key": "bad key"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(psycopg.Error):
                self.f.submit(**kwargs)
        for cap, value in (("max_duration_ms", 1000), ("max_pixels", 100), ("max_bytes", 100)):
            with connect() as conn:
                conn.execute(sql.SQL("UPDATE public.org_quotas SET {}=%s WHERE organization_id=%s").format(sql.Identifier(cap)), (value, self.f.org_a))
            with self.subTest(cap=cap), self.assertRaises(psycopg.Error):
                self.f.submit()
            with connect() as conn:
                conn.execute("UPDATE public.org_quotas SET max_duration_ms=900000,max_pixels=8294400,max_bytes=524288000 WHERE organization_id=%s", (self.f.org_a,))
        new_asset = uuid4()
        with connect() as conn:
            conn.execute("""INSERT INTO public.assets(id,organization_id,storage_key,sha256,bytes,duration_ms,width,height,rights_confirmed)
                          VALUES (%s,%s,%s,%s,1024,5000,640,360,false)""",
                         (new_asset, self.f.org_a, str(new_asset), "c" * 64))
        with self.assertRaises(psycopg.Error):
            self.f.submit(asset=new_asset)
        self.assertEqual({"jobs": 0, "outbox": 0, "audit": 0, "reserved": 0}, self.f.counts())

    def test_job_count_cap_is_atomic(self):
        with connect() as conn:
            conn.execute("UPDATE public.org_quotas SET max_jobs=1 WHERE organization_id=%s", (self.f.org_a,))
        self.f.submit("first")
        with self.assertRaises(psycopg.Error):
            self.f.submit("second")
        self.assertEqual(1, self.f.counts()["jobs"])

    def test_pixel_quota_bounds_output_presets_as_well_as_input(self):
        with connect() as conn:
            # Source 640x360 fits; the largest production rendition 1080x1920 does not.
            conn.execute("UPDATE public.org_quotas SET max_pixels=500000 WHERE organization_id=%s", (self.f.org_a,))
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.f.submit("production", quality="production")
        self.assertEqual(0, self.f.counts()["reserved"])
        self.assertEqual("queued", self.f.submit("smoke")["state"])

    def test_failure_in_any_transactional_insert_rolls_everything_back(self):
        # A real database trigger injects failure after preceding writes, unlike a mock.
        for table in ("render_jobs", "render_outbox", "audit_events"):
            with self.subTest(table=table):
                with connect() as conn:
                    conn.execute("""CREATE OR REPLACE FUNCTION public.g1_test_fail_insert() RETURNS trigger
                                    LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected transaction crash'; END $$""")
                    conn.execute(sql.SQL("CREATE TRIGGER g1_test_fail BEFORE INSERT ON public.{} FOR EACH ROW EXECUTE FUNCTION public.g1_test_fail_insert()").format(sql.Identifier(table)))
                try:
                    with self.assertRaisesRegex(psycopg.Error, "injected transaction crash"):
                        self.f.submit()
                    self.assertEqual({"jobs": 0, "outbox": 0, "audit": 0, "reserved": 0}, self.f.counts())
                finally:
                    with connect() as conn:
                        conn.execute(sql.SQL("DROP TRIGGER g1_test_fail ON public.{}").format(sql.Identifier(table)))
                        conn.execute("DROP FUNCTION public.g1_test_fail_insert()")
        self.assertIsNotNone(self.f.submit())

    def test_foreign_keys_enforce_org_boundary_even_for_trusted_writers(self):
        job = self.f.submit()
        statements = (
            ("""INSERT INTO public.render_jobs(organization_id,request_key,request_hash,source_asset_id)
                VALUES (%s,'cross-fk',%s,%s)""", (self.f.org_b, "a" * 64, self.f.asset_a)),
            ("UPDATE public.render_outbox SET organization_id=%s WHERE job_id=%s", (self.f.org_b, job["id"])),
            ("""INSERT INTO public.render_attempts(job_id,organization_id,attempt_no,lease_token,fence,lease_expires_at)
                VALUES (%s,%s,1,%s,1,now()+interval '1 minute')""", (job["id"], self.f.org_b, uuid4())),
            ("""INSERT INTO public.audit_events(job_id,organization_id,event_type)
                VALUES (%s,%s,'cross_org_test')""", (job["id"], self.f.org_b)),
        )
        for statement, params in statements:
            with self.subTest(statement=statement), self.assertRaises(psycopg.errors.ForeignKeyViolation):
                with connect() as conn:
                    conn.execute(statement, params)

    def test_request_and_asset_fingerprints_are_immutable(self):
        job = self.f.submit()
        for statement, params in (
            ("UPDATE public.render_jobs SET request_hash=%s WHERE id=%s", ("b" * 64, job["id"])),
            ("UPDATE public.render_jobs SET request_payload='{}' WHERE id=%s", (job["id"],)),
            ("UPDATE public.render_jobs SET cost_cents=0 WHERE id=%s", (job["id"],)),
            ("UPDATE public.assets SET sha256=%s WHERE id=%s", ("b" * 64, self.f.asset_a)),
        ):
            with self.subTest(statement=statement), self.assertRaises(psycopg.Error):
                with connect() as conn:
                    conn.execute(statement, params)


if __name__ == "__main__":
    unittest.main()
