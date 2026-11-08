"""Real GoTrue sessions across independent HTTP processes; no Auth fixture."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import tempfile
import unittest
import urllib.request
from uuid import UUID, uuid4

import jwt
import psycopg
from psycopg.rows import dict_row

from engine.cli import demo
from engine.media_ingest import ingest_media
from engine.media_pipeline import MediaWorker
from engine.object_store import LocalObjectStore
from g3a1.support import HTTPReplica


class OfficialSharedUploadTests(unittest.TestCase):
    @classmethod
    def auth(cls, path, body=None, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(cls.auth_url + path, method="POST", headers=headers,
                                         data=json.dumps(body).encode() if body is not None else b"")
        with urllib.request.urlopen(request, timeout=10) as response:
            data = response.read()
            return json.loads(data) if data else None

    @classmethod
    def setUpClass(cls):
        # Missing official infrastructure is an error, not a passing mock/skip.
        cls.auth_url = os.environ["G3A_AUTH_URL"]
        cls.admin_dsn = os.environ["G3A_AUTH_ADMIN_DSN"]
        cls.api_dsn = os.environ["G3A_API_DSN"]
        cls.password = "Synthetic-shared-auth-only-49!"
        cls.emails = [f"shared-{uuid4()}@example.invalid" for _ in range(3)]
        cls.users = [UUID(cls.auth("/signup", {"email": email, "password": cls.password,
                    "data": {"organization_id": str(uuid4()), "role": "service_role"}})["user"]["id"])
                     for email in cls.emails]
        cls.orgs = [uuid4(), uuid4()]
        with psycopg.connect(cls.admin_dsn) as db:
            for org, user in zip(cls.orgs, cls.users):
                db.execute("INSERT INTO public.organizations(id,name) VALUES(%s,'shared official Auth')", (org,))
                db.execute("INSERT INTO public.memberships VALUES(%s,%s,'owner')", (org, user))
                db.execute("INSERT INTO public.org_quotas(organization_id) VALUES(%s)", (org,))
                cls.provision(db, org)
            db.execute("INSERT INTO public.memberships VALUES(%s,%s,'editor')", (cls.orgs[0], cls.users[2]))
        cls.temp = tempfile.TemporaryDirectory(prefix="g3a1-official-auth-")
        cls.root = Path(cls.temp.name)
        cls.store = LocalObjectStore(cls.root / "objects")
        cls.signing_key = secrets.token_bytes(32)
        cls.replicas = []
        cls.a, cls.b = cls.new_replica(), cls.new_replica()
        media = demo(cls.root / "synthetic", "shared-auth")
        with psycopg.connect(cls.admin_dsn, row_factory=dict_row) as db:
            cls.asset = ingest_media(db, cls.store, cls.orgs[0], cls.users[0],
                                     media["video"], media["caption"], rights_confirmed=True)

    @classmethod
    def provision(cls, db, org):
        db.execute("""INSERT INTO g3a1_private.upload_limits
          (organization_id,max_bytes,max_count,max_upload_bytes,max_ttl_seconds,max_lease_seconds)
          VALUES(%s,8388608,128,1048576,120,60)""", (org,))

    @classmethod
    def new_replica(cls):
        replica = HTTPReplica({
            "dsn": cls.api_dsn, "session_dsn": cls.api_dsn,
            "store_root": str(cls.root / "objects"),
            "work_dir": str(cls.root / ("replica-" + uuid4().hex)),
            "signing_key": cls.signing_key, "trust_secret": os.environ["G3A_AUTH_JWT_SECRET"],
            "issuer": os.environ["G3A_AUTH_ISSUER"], "kid": "",
            "http_options": {"max_connections": 16, "max_db_connections": 4},
            "authority_options": {"lease_seconds": 2},
        })
        cls.replicas.append(replica)
        return replica

    @classmethod
    def tearDownClass(cls):
        for replica in cls.replicas:
            replica.stop()
        cls.temp.cleanup()

    def setUp(self):
        self.token = self.auth("/token?grant_type=password", {"email": self.emails[0],
                               "password": self.password})["access_token"]
        self.path = f"/v1/orgs/{self.orgs[0]}/uploads"

    def grant(self, payload=b"official shared bytes", request_key=None):
        body = {"request_key": request_key or "auth-" + uuid4().hex,
                "size_bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
        status, grant = self.a.call("POST", self.path, body, token=self.token)
        self.assertEqual(201, status, grant)
        return body, grant

    def test_real_token_cross_replica_receipt_and_restart_without_local_ledger(self):
        payload = b"official shared durable bytes"
        body, grant = self.grant(payload)
        self.assertNotEqual(self.a.pid, self.b.pid)
        self.assertEqual(201, self.b.call("POST", self.path, body, token=self.token)[0])
        status, receipt = self.a.call("PUT", grant["upload_path"], payload, token=self.token)
        self.assertEqual(201, status, receipt)
        status, observed = self.b.call("GET", self.path + "/" + grant["id"], token=self.token)
        self.assertEqual(200, status, observed)
        self.assertEqual(hashlib.sha256(payload).hexdigest(), observed["sha256"])
        first_pid = self.a.pid
        self.a.stop(kill=True)
        type(self).a = self.new_replica()
        self.assertNotEqual(first_pid, self.a.pid)
        self.assertEqual(observed, self.a.call("GET", self.path + "/" + grant["id"], token=self.token)[1])
        self.assertEqual([], list(self.root.rglob("*.sqlite*")))

    def test_real_logout_revokes_both_replicas_while_jwt_signature_stays_valid(self):
        self.auth("/logout", token=self.token)
        jwt.decode(self.token, os.environ["G3A_AUTH_JWT_SECRET"], algorithms=["HS256"],
                   issuer=os.environ["G3A_AUTH_ISSUER"], audience="authenticated")
        for replica in (self.a, self.b):
            self.assertEqual(401, replica.call("POST", self.path,
                {"request_key": "logout", "size_bytes": 1, "sha256": "a"*64}, token=self.token)[0])

    def test_membership_revocation_and_user_metadata_cannot_cross_organizations(self):
        _, grant = self.grant()
        self.assertEqual(403, self.b.call("POST", f"/v1/orgs/{self.orgs[1]}/uploads",
            {"request_key": "cross", "size_bytes": 1, "sha256": "a"*64}, token=self.token)[0])
        with psycopg.connect(self.admin_dsn) as db:
            db.execute("DELETE FROM public.memberships WHERE organization_id=%s AND user_id=%s", (self.orgs[0], self.users[0]))
        try:
            for replica in (self.a, self.b):
                self.assertIn(replica.call("PUT", grant["upload_path"], b"official shared bytes", token=self.token)[0], (400, 403))
                self.assertIn(replica.call("GET", self.path + "/" + grant["id"], token=self.token)[0], (403, 404))
        finally:
            with psycopg.connect(self.admin_dsn) as db:
                db.execute("INSERT INTO public.memberships VALUES(%s,%s,'owner')", (self.orgs[0], self.users[0]))

    def test_same_org_other_real_user_does_not_obtain_owner_grant_or_receipt(self):
        body, grant = self.grant()
        peer = self.auth("/token?grant_type=password", {"email": self.emails[2], "password": self.password})["access_token"]
        for replica in (self.a, self.b):
            self.assertIn(replica.call("POST", self.path, body, token=peer)[0], (400, 403, 409))
            status, denied = replica.call("GET", self.path + "/" + grant["id"], token=peer)
            self.assertEqual(400, status)
            self.assertNotIn(grant["id"], json.dumps(denied))
            self.assertNotIn("upload_path", denied)

    def test_service_role_and_fabricated_headers_do_not_bypass_shared_boundary(self):
        claims = jwt.decode(self.token, os.environ["G3A_AUTH_JWT_SECRET"], algorithms=["HS256"],
                            issuer=os.environ["G3A_AUTH_ISSUER"], audience="authenticated")
        claims["role"] = "service_role"
        service = jwt.encode(claims, os.environ["G3A_AUTH_JWT_SECRET"], algorithm="HS256", headers={"typ": "JWT"})
        body = {"request_key": "service", "size_bytes": 1, "sha256": "a"*64}
        for replica in (self.a, self.b):
            self.assertEqual(401, replica.call("POST", self.path, body, token=service)[0])
            self.assertEqual(401, replica.call("POST", self.path, body,
                headers={"X-User-ID": str(self.users[0]), "X-Organization-ID": str(self.orgs[0])})[0])

    def test_delete_real_auth_user_preserves_historical_upload_but_revokes_access(self):
        email = f"shared-delete-{uuid4()}@example.invalid"
        signed = self.auth("/signup", {"email": email, "password": self.password})
        user, org = UUID(signed["user"]["id"]), uuid4()
        with psycopg.connect(self.admin_dsn) as db:
            db.execute("INSERT INTO public.organizations(id,name) VALUES(%s,'deleted synthetic user')", (org,))
            db.execute("INSERT INTO public.memberships VALUES(%s,%s,'owner')", (org, user))
            self.provision(db, org)
        token = signed["access_token"]
        path = f"/v1/orgs/{org}/uploads"
        status, grant = self.a.call("POST", path,
            {"request_key": "delete", "size_bytes": 4, "sha256": hashlib.sha256(b"test").hexdigest()}, token=token)
        self.assertEqual(201, status, grant)
        self.assertEqual(201, self.b.call("PUT", grant["upload_path"], b"test", token=token)[0])
        with psycopg.connect(self.admin_dsn) as db:
            db.execute("DELETE FROM auth.users WHERE id=%s", (user,))
            self.assertEqual((user,), db.execute("SELECT owner_user_id FROM g3a1_private.upload_receipts WHERE id=%s", (grant["id"],)).fetchone())
        for replica in (self.a, self.b):
            self.assertEqual(401, replica.call("GET", path + "/" + grant["id"], token=token)[0])

    def test_shared_application_preserves_real_render_status_and_private_download(self):
        path = f"/v1/orgs/{self.orgs[0]}/renders"
        request = {"request_key": "shared-official-render", "asset_id": str(self.asset["id"]),
                   "duration_ms": 800, "quality": "smoke"}
        status, job = self.a.call("POST", path, request, token=self.token)
        self.assertEqual(202, status, job)
        result = MediaWorker(self.admin_dsn, self.store, self.root / "scratch").run(job["id"])
        self.assertEqual("completed", result["state"])
        self.assertEqual(200, self.b.call("GET", path + "/" + job["id"], token=self.token)[0])
        status, cap = self.b.call("POST", path + "/" + job["id"] + "/downloads", {"preset": "vertical"}, token=self.token)
        self.assertEqual(200, status, cap)
        # HTTPReplica parses JSON responses; use a direct bounded binary request.
        req = urllib.request.Request(self.a.base + cap["download_path"], headers={"Authorization": "Bearer " + self.token})
        with urllib.request.urlopen(req, timeout=10) as response:
            content = response.read(1048576)
        output = next(o for o in result["output_manifest"]["outputs"] if o["preset"] == "vertical")
        self.assertEqual(output["sha256"], hashlib.sha256(content).hexdigest())
