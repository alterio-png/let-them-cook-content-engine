"""Real signup/password sign-in/logout via pinned official GoTrue HTTP API."""
import json
import hashlib
import os
from pathlib import Path
import secrets
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from uuid import UUID, uuid4

import jwt
import psycopg
from psycopg.rows import dict_row

from engine.cloud_auth import AuthError, AuthVerifier, PostgresSessionChecker, TrustedKey
from engine.cloud_http import MediaHTTPApplication, local_http_server
from engine.cli import demo
from engine.media_ingest import ingest_media
from engine.media_pipeline import MediaWorker
from engine.object_store import LocalObjectStore
from engine.upload_authority import UploadAuthority


@unittest.skipUnless(os.environ.get("G3A_AUTH_URL"), "Official Auth stack required: scripts/run_local_auth.sh")
class OfficialAuthIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.url = os.environ["G3A_AUTH_URL"]
        cls.dsn = os.environ["G3A_AUTH_ADMIN_DSN"]
        cls.api_dsn = os.environ["G3A_API_DSN"]
        cls.password = "Synthetic-local-password-49!"
        cls.emails = [f"synthetic-{uuid4()}@example.invalid" for _ in range(2)]
        cls.user_ids = []
        for email in cls.emails:
            response = cls.request("/signup", {"email": email, "password": cls.password,
                                             "data": {"organization_id": str(uuid4()), "role": "service_role"}})
            cls.user_ids.append(UUID(response["user"]["id"]))
        cls.orgs = [uuid4(), uuid4()]
        with psycopg.connect(cls.dsn) as db:
            for org, user in zip(cls.orgs, cls.user_ids):
                db.execute("INSERT INTO organizations(id,name) VALUES(%s,%s)", (org, "synthetic-local-auth"))
                db.execute("INSERT INTO memberships VALUES(%s,%s,'owner')", (org, user))
                db.execute("INSERT INTO org_quotas(organization_id) VALUES(%s)", (org,))
        cls.temp = tempfile.TemporaryDirectory(prefix="g3a-official-auth-")
        cls.root = Path(cls.temp.name)
        cls.store = LocalObjectStore(cls.root / "objects")
        media = demo(cls.root / "synthetic", "official-auth")
        with psycopg.connect(cls.dsn, row_factory=dict_row) as db:
            cls.asset = ingest_media(db, cls.store, cls.orgs[0], cls.user_ids[0],
                                     media["video"], media["caption"], rights_confirmed=True)
        cls.http_verifier = AuthVerifier(issuer=os.environ["G3A_AUTH_ISSUER"],
                                        keys={"": TrustedKey("HS256", os.environ["G3A_AUTH_JWT_SECRET"])},
                                        session_checker=PostgresSessionChecker(cls.api_dsn))
        cls.app = MediaHTTPApplication(dsn=cls.api_dsn, verifier=cls.http_verifier, store=cls.store,
                                       authority=UploadAuthority(cls.root / "private" / "uploads.sqlite3",
                                                                 secrets.token_bytes(32)))
        cls.server = local_http_server(cls.app)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.http_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.temp.cleanup()

    def http_request(self, method, path, body=None, token=None):
        data = json.dumps(body).encode() if isinstance(body, dict) else body
        request = urllib.request.Request(self.http_url + path, data=data, method=method,
                                         headers={"Authorization": "Bearer " + (token or self.token),
                                                  "Content-Type": "application/json"})
        try:
            response = urllib.request.urlopen(request, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            data = response.read()
            if response.headers.get_content_type() == "application/json":
                data = json.loads(data)
            return response.status, data

    @classmethod
    def request(cls, path, body=None, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        data = json.dumps(body).encode() if body is not None else b""
        request = urllib.request.Request(cls.url + path, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = response.read()
            return json.loads(payload) if payload else None

    def setUp(self):
        self.verifier = AuthVerifier(issuer=os.environ["G3A_AUTH_ISSUER"],
                                    keys={"": TrustedKey("HS256", os.environ["G3A_AUTH_JWT_SECRET"])},
                                    session_checker=PostgresSessionChecker(self.api_dsn))
        self.token = self.request("/token?grant_type=password", {"email": self.emails[0],
                                                              "password": self.password})["access_token"]
        self.identity = self.verifier.verify(self.token)

    def test_real_official_signup_signin_signature_and_session(self):
        self.assertEqual(self.identity.user_id, self.user_ids[0])
        with psycopg.connect(self.dsn) as db:
            row = db.execute("SELECT u.encrypted_password,s.user_id FROM auth.users u "
                             "JOIN auth.sessions s ON s.user_id=u.id WHERE s.id=%s",
                             (self.identity.session_id,)).fetchone()
            self.assertTrue(row[0].startswith("$2"))
            self.assertEqual(row[1], self.identity.user_id)
            self.assertGreater(db.execute("SELECT count(*) FROM auth.schema_migrations").fetchone()[0], 20)

    def test_real_logout_revokes_still_unexpired_signed_access_token(self):
        self.request("/logout", token=self.token)
        # Signature remains valid; fresh official auth.sessions lookup rejects it.
        jwt.decode(self.token, os.environ["G3A_AUTH_JWT_SECRET"], algorithms=["HS256"],
                   issuer=os.environ["G3A_AUTH_ISSUER"], audience="authenticated")
        with self.assertRaises(AuthError):
            self.verifier.verify(self.token)

    def test_rls_uses_verified_identity_and_live_membership_not_user_metadata(self):
        with psycopg.connect(self.api_dsn) as db:
            db.execute("SET LOCAL ROLE authenticated")
            db.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (str(self.identity.user_id),))
            rows = db.execute("SELECT id FROM organizations ORDER BY id").fetchall()
            self.assertEqual(rows, [(self.orgs[0],)])
        with psycopg.connect(self.dsn) as db:
            db.execute("DELETE FROM memberships WHERE organization_id=%s AND user_id=%s",
                       (self.orgs[0], self.identity.user_id))
        try:
            self.assertEqual(self.verifier.verify(self.token), self.identity)
            with psycopg.connect(self.api_dsn) as db:
                db.execute("SET LOCAL ROLE authenticated")
                db.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (str(self.identity.user_id),))
                self.assertEqual(db.execute("SELECT id FROM organizations").fetchall(), [])
        finally:
            with psycopg.connect(self.dsn) as db:
                db.execute("INSERT INTO memberships VALUES(%s,%s,'owner')", (self.orgs[0], self.identity.user_id))

    def test_narrow_session_reader_cannot_read_passwords_or_metadata(self):
        with psycopg.connect(self.api_dsn) as db:
            db.execute("SET LOCAL ROLE g3a_session_reader")
            self.assertEqual(db.execute("SELECT id FROM auth.sessions").fetchall(), [])
            db.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (str(self.identity.user_id),))
            self.assertEqual(db.execute("SELECT id FROM auth.sessions WHERE id=%s",
                                        (self.identity.session_id,)).fetchone()[0], self.identity.session_id)
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                db.execute("SELECT encrypted_password FROM auth.users")
        with psycopg.connect(self.api_dsn) as db:
            db.execute("SET LOCAL ROLE g3a_session_reader")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                db.execute("SELECT * FROM auth.sessions")

    def test_expired_retained_session_fails_closed(self):
        with psycopg.connect(self.dsn) as db:
            db.execute("UPDATE auth.sessions SET not_after=now()-interval '1 second' WHERE id=%s",
                       (self.identity.session_id,))
        with self.assertRaises(AuthError):
            self.verifier.verify(self.token)

    def test_deleted_user_revokes_session(self):
        email = f"synthetic-delete-{uuid4()}@example.invalid"
        result = self.request("/signup", {"email": email, "password": self.password})
        token = result["access_token"]
        identity = self.verifier.verify(token)
        with psycopg.connect(self.dsn) as db:
            db.execute("DELETE FROM auth.users WHERE id=%s", (identity.user_id,))
        with self.assertRaises(AuthError):
            self.verifier.verify(token)

    def test_modified_real_token_signature_rejected(self):
        parts = self.token.split(".")
        parts[2] = ("A" if parts[2][0] != "A" else "B") + parts[2][1:]
        with self.assertRaises(AuthError):
            self.verifier.verify(".".join(parts))

    def test_real_http_submit_idempotency_worker_status_and_private_download(self):
        path = f"/v1/orgs/{self.orgs[0]}/renders"
        body = {"request_key": "official-auth-e2e", "asset_id": str(self.asset["id"]),
                "duration_ms": 800, "quality": "smoke"}
        status, submitted = self.http_request("POST", path, body)
        self.assertEqual(status, 202, submitted)
        status, repeated = self.http_request("POST", path, body)
        self.assertEqual(status, 202, repeated)
        self.assertEqual(submitted["id"], repeated["id"])
        status, queued = self.http_request("GET", path + "/" + submitted["id"])
        self.assertEqual((status, queued["state"]), (200, "queued"))
        result = MediaWorker(self.dsn, self.store, self.root / "scratch").run(submitted["id"])
        self.assertEqual(result["state"], "completed")
        self.assertEqual(len(result["output_manifest"]["outputs"]), 3)
        status, current = self.http_request("GET", path + "/" + submitted["id"])
        self.assertEqual((status, current["state"]), (200, "completed"))
        status, download = self.http_request("POST", path + "/" + submitted["id"] + "/downloads",
                                             {"preset": "vertical"})
        self.assertEqual(status, 200, download)
        status, content = self.http_request("GET", download["download_path"])
        self.assertEqual(status, 200)
        expected = next(o for o in result["output_manifest"]["outputs"] if o["preset"] == "vertical")
        self.assertEqual(hashlib.sha256(content).hexdigest(), expected["sha256"])
        self.assertEqual(len(content), expected["size_bytes"])

    def test_real_http_metadata_cross_tenant_and_membership_downgrade(self):
        body = {"size_bytes": 4, "sha256": hashlib.sha256(b"test").hexdigest()}
        path = f"/v1/orgs/{self.orgs[0]}/uploads"
        status, grant = self.http_request("POST", path, body)
        self.assertEqual(status, 201, grant)
        self.assertEqual(self.http_request("POST", f"/v1/orgs/{self.orgs[1]}/uploads", body)[0], 403)
        with psycopg.connect(self.dsn) as db:
            db.execute("UPDATE memberships SET role='viewer' WHERE organization_id=%s AND user_id=%s",
                       (self.orgs[0], self.identity.user_id))
        try:
            self.assertEqual(self.http_request("POST", path, body)[0], 403)
            self.assertEqual(self.http_request("PUT", grant["upload_path"], b"test")[0], 403)
        finally:
            with psycopg.connect(self.dsn) as db:
                db.execute("UPDATE memberships SET role='owner' WHERE organization_id=%s AND user_id=%s",
                           (self.orgs[0], self.identity.user_id))

    def test_real_http_logout_and_service_role_rejected(self):
        path = f"/v1/orgs/{self.orgs[0]}/uploads"
        body = {"size_bytes": 4, "sha256": hashlib.sha256(b"test").hexdigest()}
        claims = jwt.decode(self.token, options={"verify_signature": False})
        claims["role"] = "service_role"
        forged = jwt.encode(claims, os.environ["G3A_AUTH_JWT_SECRET"], algorithm="HS256")
        self.assertEqual(self.http_request("POST", path, body, token=forged)[0], 401)
        self.assertEqual(self.http_request("POST", path, body)[0], 201)
        self.request("/logout", token=self.token)
        self.assertEqual(self.http_request("POST", path, body)[0], 401)


if __name__ == "__main__":
    unittest.main()
