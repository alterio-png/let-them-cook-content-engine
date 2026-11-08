"""Real PostgreSQL role/RLS/constraint tests, not an Auth issuance fixture.

The trusted subject setting here models the already-verified backend identity.
Official GoTrue issuance, session revocation and HTTP verification run in the
separate official-Auth integration suite; this file claims only SQL isolation.
"""
from contextlib import contextmanager
import hashlib
import os
import time
import unittest
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from engine.upload_authority import CapabilityError
from integration.g1.support import connect
from g3a1.support import SharedHTTPFixture


class LedgerPermissionTests(unittest.TestCase):
    def setUp(self):
        self.fx = SharedHTTPFixture()
        self.addCleanup(self.fx.cleanup)
        self.f = self.fx.f
        self.org, self.user = self.f.org_a, self.f.user_a
        self.data = b"synthetic SQL-permission upload"
        self.sha = hashlib.sha256(self.data).hexdigest()
        self.source = self.fx.root / "permission-source.bin"
        self.source.write_bytes(self.data)
        self.source.chmod(0o600)
        self.authority = self.fx.authority()

    def reserve(self, key=None):
        return self.authority.reserve(self.org, self.user, request_key=key or uuid4().hex,
                                      size_bytes=len(self.data), sha256=self.sha)

    @contextmanager
    def actor(self, user=None, *, role="authenticated", subject=True):
        # The real restricted NOINHERIT login has no table ownership or BYPASSRLS.
        with psycopg.connect(self.fx.dsn, row_factory=dict_row) as db:
            db.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
            if subject:
                db.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (str(user or self.user),))
            yield db

    def test_nonprivileged_login_and_raw_mutations_denied_by_actual_roles(self):
        with psycopg.connect(self.fx.dsn) as db:
            row = db.execute("SELECT rolsuper,rolbypassrls,rolinherit,rolcreaterole,rolcreatedb,rolreplication FROM pg_roles WHERE rolname=session_user").fetchone()
            self.assertEqual((False,) * 6, row)
        grant = self.reserve()
        statements = (
            ("UPDATE g3a1_private.upload_limits SET used_count=0 WHERE organization_id=%s", (self.org,)),
            ("DELETE FROM g3a1_private.upload_reservations WHERE id=%s", (grant["id"],)),
            ("INSERT INTO g3a1_private.upload_receipts(id,organization_id,owner_user_id,object_key,size_bytes,sha256) SELECT id,organization_id,owner_user_id,object_key,size_bytes,sha256 FROM g3a1_private.upload_reservations WHERE id=%s", (grant["id"],)),
            ("DELETE FROM g3a1_private.upload_events WHERE organization_id=%s", (self.org,)),
        )
        for statement, params in statements:
            with self.subTest(statement=statement):
                with self.assertRaises(psycopg.Error) as caught:
                    with self.actor() as db:
                        db.execute(statement, params)
                self.assertEqual("42501", caught.exception.sqlstate)
        with self.assertRaises(ValueError):
            from engine.shared_upload_authority import PostgresUploadAuthority
            PostgresUploadAuthority(os.environ["G1_TEST_DSN"], self.fx.signing_key)

    def test_rls_hides_other_owner_even_when_same_org_member(self):
        grant = self.reserve()
        self.authority.publish(grant["authorization"], self.user, self.source, self.fx.store)
        for user in (self.fx.user_c, self.f.user_b):
            with self.actor(user) as db:
                for table in ("upload_reservations", "upload_receipts"):
                    rows = db.execute(sql.SQL("SELECT id FROM g3a1_private.{} WHERE id=%s").format(sql.Identifier(table)), (grant["id"],)).fetchall()
                    self.assertEqual([], rows)
            for method in (self.authority.receipt,):
                try:
                    result = method(self.org, user, grant["id"])
                except (CapabilityError, psycopg.errors.InsufficientPrivilege):
                    result = None
                self.assertIsNone(result)
        with self.actor() as db:
            self.assertEqual(1, len(db.execute("SELECT id FROM g3a1_private.upload_receipts WHERE id=%s", (grant["id"],)).fetchall()))

    def test_current_membership_revocation_immediately_denies_all_user_paths(self):
        grant = self.reserve()
        with connect() as db:
            db.execute("DELETE FROM public.memberships WHERE organization_id=%s AND user_id=%s", (self.org, self.user))
        for operation in (
            lambda: self.reserve(),
            lambda: self.authority.publish(grant["authorization"], self.user, self.source, self.fx.store),
            lambda: self.authority.receipt(self.org, self.user, grant["id"]),
            lambda: self.authority.reconcile(self.org, self.user, grant["id"], self.fx.store),
            lambda: self.authority.metrics(self.org, self.user),
        ):
            with self.subTest(operation=operation):
                with self.assertRaises((CapabilityError, psycopg.errors.InsufficientPrivilege)):
                    operation()
        with self.actor() as db:
            self.assertEqual([], db.execute("SELECT id FROM g3a1_private.upload_reservations WHERE id=%s", (grant["id"],)).fetchall())
        with connect() as db:
            row = db.execute("SELECT owner_user_id,state FROM g3a1_private.upload_reservations WHERE id=%s", (grant["id"],)).fetchone()
            quota = db.execute("SELECT used_count,used_bytes FROM g3a1_private.upload_limits WHERE organization_id=%s", (self.org,)).fetchone()
        self.assertEqual(self.user, row["owner_user_id"])
        self.assertEqual((1, len(self.data)), (quota["used_count"], quota["used_bytes"]))

    def test_subject_required_for_every_user_security_definer_entrypoint(self):
        grant = self.reserve()
        statements = (
            ("SELECT g3a1_private.reserve_upload(%s,'missing-subject',%s,%s,120)", (self.org, len(self.data), self.sha)),
            ("SELECT g3a1_private.claim_upload(%s,%s,1)", (self.org, grant["id"])),
            ("SELECT g3a1_private.get_upload(%s,%s)", (self.org, grant["id"])),
            ("SELECT g3a1_private.get_upload_receipt(%s,%s)", (self.org, grant["id"])),
            ("SELECT g3a1_private.reconcile_own_upload(%s,%s,1)", (self.org, grant["id"])),
            ("SELECT g3a1_private.upload_metrics(%s)", (self.org,)),
        )
        for statement, params in statements:
            with self.subTest(statement=statement):
                with self.assertRaises(psycopg.Error) as caught:
                    with self.actor(subject=False) as db:
                        db.execute(statement, params)
                self.assertEqual("42501", caught.exception.sqlstate)

    def test_internal_functions_not_callable_by_authenticated(self):
        grant = self.reserve()
        statements = (
            ("SELECT g3a1_private._reservation_json(%s)", (grant["id"],)),
            ("SELECT g3a1_private._receipt_json(%s)", (grant["id"],)),
            ("SELECT g3a1_private.internal_claim_reconciliation(%s,%s,1)", (self.org, grant["id"])),
            ("SELECT g3a1_private.internal_expire_unused_uploads(%s,100)", (self.org,)),
        )
        for statement, params in statements:
            with self.subTest(statement=statement):
                with self.assertRaises(psycopg.Error) as caught:
                    with self.actor() as db:
                        db.execute(statement, params)
                self.assertEqual("42501", caught.exception.sqlstate)

    def test_cross_org_and_owner_receipt_foreign_keys_reject_even_trusted_writer(self):
        grant = self.reserve()
        for organization, owner in ((self.f.org_b, self.user), (self.org, self.fx.user_c)):
            with self.subTest(organization=organization, owner=owner):
                with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                    with connect() as db:
                        db.execute("INSERT INTO g3a1_private.upload_receipts(id,organization_id,owner_user_id,object_key,size_bytes,sha256) VALUES(%s,%s,%s,%s,%s,%s)",
                                   (grant["id"], organization, owner,
                                    f"{organization}/uploads/{grant['id']}/unvalidated.bin", len(self.data), self.sha))

    def test_reservation_identity_and_receipt_are_immutable_for_trusted_writer(self):
        grant = self.reserve()
        for column, value in (("owner_user_id", self.fx.user_c), ("organization_id", self.f.org_b),
                              ("sha256", "f" * 64), ("size_bytes", len(self.data) + 1),
                              ("request_key", "changed-identity")):
            with self.subTest(column=column):
                with self.assertRaises(psycopg.errors.CheckViolation):
                    with connect() as db:
                        db.execute(sql.SQL("UPDATE g3a1_private.upload_reservations SET {}=%s WHERE id=%s").format(sql.Identifier(column)), (value, grant["id"]))
        self.authority.publish(grant["authorization"], self.user, self.source, self.fx.store)
        for column, value in (("sha256", "f" * 64), ("generation", "forged-generation"),
                              ("owner_user_id", self.fx.user_c)):
            with self.subTest(receipt_column=column):
                with self.assertRaises(psycopg.errors.CheckViolation):
                    with connect() as db:
                        db.execute(sql.SQL("UPDATE g3a1_private.upload_receipts SET {}=%s WHERE id=%s").format(sql.Identifier(column)), (value, grant["id"]))

    def test_receipt_payload_foreign_key_rejects_mismatched_size_or_hash(self):
        grant = self.reserve()
        key = f"{self.org}/uploads/{grant['id']}/unvalidated.bin"
        for size, digest in ((len(self.data) + 1, self.sha), (len(self.data), "f" * 64)):
            with self.subTest(size=size, digest=digest):
                with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                    with connect() as db:
                        db.execute("INSERT INTO g3a1_private.upload_receipts(id,organization_id,owner_user_id,object_key,size_bytes,sha256) VALUES(%s,%s,%s,%s,%s,%s)",
                                   (grant["id"], self.org, self.user, key, size, digest))

    def test_dedicated_service_recovers_revoked_owner_without_raw_table_access(self):
        grant = self.reserve()
        with self.actor() as db:
            original = db.execute("SELECT g3a1_private.claim_upload(%s,%s,1) AS value", (self.org, grant["id"])).fetchone()["value"]
        key = self.authority.verify(grant["authorization"], kind="upload", user_id=self.user)["key"]
        self.fx.store.put_file(self.org, key, self.source)
        with connect() as db:
            db.execute("DELETE FROM public.memberships WHERE organization_id=%s AND user_id=%s", (self.org, self.user))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with connect() as db:
                expired = db.execute("SELECT lease_expires_at<=clock_timestamp() AS done FROM g3a1_private.upload_reservations WHERE id=%s", (grant["id"],)).fetchone()["done"]
            if expired:
                break
            time.sleep(0.03)
        else:
            self.fail("actual PostgreSQL lease did not expire")
        with connect() as db:
            db.execute("SET LOCAL ROLE g3a1_upload_reconciler")
            row = db.execute("SELECT g3a1_private.internal_claim_reconciliation(%s,%s,1) AS value", (self.org, grant["id"])).fetchone()["value"]
            self.assertGreater(row["fence"], original["fence"])
            receipt = db.execute("SELECT g3a1_private.internal_complete_upload(%s,%s,%s,%s,%s,%s,%s,NULL) AS value",
                                 (self.org, grant["id"], row["lease_token"], row["fence"], key, len(self.data), self.sha)).fetchone()["value"]
            self.assertEqual("uploaded_pending_validation", receipt["state"])
        with connect() as db:
            quota = db.execute("SELECT used_count,used_bytes FROM g3a1_private.upload_limits WHERE organization_id=%s", (self.org,)).fetchone()
            self.assertEqual((1, len(self.data)), (quota["used_count"], quota["used_bytes"]))
        for statement in ("SELECT * FROM g3a1_private.upload_reservations", "DELETE FROM g3a1_private.upload_receipts"):
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with connect() as db:
                    db.execute("SET LOCAL ROLE g3a1_upload_reconciler")
                    db.execute(statement)
        with connect() as db:
            roles = db.execute("SELECT rolsuper,rolbypassrls,rolinherit,rolcanlogin FROM pg_roles WHERE rolname='g3a1_upload_reconciler'").fetchone()
            self.assertEqual((False, False, False, False), tuple(roles.values()))

    def test_security_definer_search_paths_and_function_acl_are_closed(self):
        with connect() as db:
            functions = db.execute("""SELECT p.proname,p.prosecdef,p.proconfig,
                EXISTS(SELECT 1 FROM aclexplode(p.proacl) a WHERE a.grantee=0 AND a.privilege_type='EXECUTE') AS public_execute
                FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname='g3a1_private' ORDER BY p.proname""").fetchall()
        self.assertTrue(functions)
        for function in functions:
            with self.subTest(function=function["proname"]):
                self.assertFalse(function["public_execute"])
                if function["prosecdef"]:
                    self.assertIn('search_path=""', function["proconfig"])
