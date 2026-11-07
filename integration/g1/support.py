"""Fixtures use privileged setup, then explicitly exercise non-bypass RLS roles."""
from contextlib import contextmanager
import os
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row


@contextmanager
def connect(role=None, user=None):
    dsn = os.environ.get("G1_TEST_DSN")
    if not dsn:
        raise RuntimeError("G1_TEST_DSN is required; G1 tests never skip PostgreSQL")
    with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=10) as conn:
        conn.execute("SET LOCAL statement_timeout = '20s'")
        conn.execute("SET LOCAL lock_timeout = '15s'")
        if role is not None:
            if role not in {"authenticated", "anon", "engine_worker"}:
                raise ValueError("Unexpected test role")
            conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
        if user is not None:
            conn.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (str(user),))
        yield conn


class Fixture:
    def __init__(self):
        self.user_a, self.user_b = uuid4(), uuid4()
        self.org_a, self.org_b = uuid4(), uuid4()
        self.asset_a, self.asset_b = uuid4(), uuid4()
        with connect() as conn:
            for user, org, asset in (
                (self.user_a, self.org_a, self.asset_a),
                (self.user_b, self.org_b, self.asset_b),
            ):
                conn.execute("INSERT INTO auth.users(id) VALUES (%s)", (user,))
                conn.execute("INSERT INTO public.organizations(id,name) VALUES (%s,'synthetic test')", (org,))
                conn.execute("INSERT INTO public.memberships(organization_id,user_id,role) VALUES (%s,%s,'editor')", (org, user))
                conn.execute("INSERT INTO public.org_quotas(organization_id) VALUES (%s)", (org,))
                conn.execute("""INSERT INTO public.assets
                    (id,organization_id,uploader_id,storage_key,sha256,bytes,duration_ms,width,height,rights_confirmed)
                    VALUES (%s,%s,%s,%s,%s,1024,5000,640,360,true)""",
                    (asset, org, user, f"{org}/{asset}/synthetic.mp4", "a" * 64))

    def submit(self, key="operation", *, org=None, user=None, asset=None,
               duration_ms=1200, quality="smoke"):
        with connect("authenticated", self.user_a if user is None else user) as conn:
            return conn.execute("SELECT * FROM public.submit_render(%s,%s,%s,%s,%s)", (
                self.org_a if org is None else org, key,
                self.asset_a if asset is None else asset, duration_ms, quality,
            )).fetchone()

    def counts(self):
        with connect() as conn:
            row = {}
            for key, table in (("jobs", "render_jobs"), ("outbox", "render_outbox"), ("audit", "audit_events")):
                row[key] = conn.execute(sql.SQL("SELECT count(*) AS n FROM public.{} WHERE organization_id=%s").format(
                    sql.Identifier(table)), (self.org_a,)).fetchone()["n"]
            row["reserved"] = conn.execute("SELECT reserved_cents FROM public.org_quotas WHERE organization_id=%s", (self.org_a,)).fetchone()["reserved_cents"]
            return row
