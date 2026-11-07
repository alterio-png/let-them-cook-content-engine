"""Real loopback HTTP + real PostgreSQL/RLS + local cryptographic JWT contracts.

JWTs and session callback here are deliberately synthetic CONTRACT fixtures.
Official local Supabase Auth issuance/revocation is a separate blocking suite;
these tests alone must never be reported as a Supabase Auth integration PASS.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError as URLHTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

import jwt
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from engine.cli import demo
from engine.cloud_auth import AuthVerifier, TrustedKey
from engine.cloud_http import HTTPConfig, MediaHTTPApplication, local_http_server
from engine.upload_authority import UploadAuthority, UploadQuotaExceeded
from g1.support import connect
from g2.support import PipelineFixture


class HTTPBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.synthetic = tempfile.TemporaryDirectory()
        cls.paths = demo(Path(cls.synthetic.name), "http-synthetic")
        cls.login = "g3a_http_" + uuid4().hex
        cls.password = "local-http-fixture-only"
        with connect() as db:
            db.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION").format(
                sql.Identifier(cls.login), sql.Literal(cls.password)))
            db.execute(sql.SQL("GRANT authenticated TO {}").format(sql.Identifier(cls.login)))
        cls.dsn = make_conninfo(os.environ["G1_TEST_DSN"], user=cls.login, password=cls.password)

    @classmethod
    def tearDownClass(cls):
        with connect() as db:
            db.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(cls.login)))
        cls.synthetic.cleanup()

    def setUp(self):
        self.f = PipelineFixture(self.paths["video"], self.paths["caption"])
        self.addCleanup(self.f.cleanup)
        self.sessions = {self.f.user_a: uuid4(), self.f.user_b: uuid4()}
        self.secret = "local-contract-signing-key-not-production" * 2
        self.issuer = "http://127.0.0.1/local-contract-issuer"
        self.verifier = AuthVerifier(issuer=self.issuer, keys={"local-contract": TrustedKey("HS256", self.secret)},
                                     session_checker=lambda user, session: self.sessions.get(user) == session)
        self.authority = UploadAuthority(self.f.root / "authority" / "uploads.sqlite3", b"local-capability-key-" * 3,
                                         max_org_uploads=4, max_org_bytes=4096)
        self.config = HTTPConfig(max_connections=16, max_db_connections=4, request_timeout_seconds=1)
        self.app = MediaHTTPApplication(dsn=self.dsn, verifier=self.verifier, store=self.f.store,
                                        authority=self.authority, config=self.config)
        self.server = local_http_server(self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.base = "http://127.0.0.1:" + str(self.server.server_port)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def token(self, user=None, **overrides):
        user = self.f.user_a if user is None else user
        now = int(time.time())
        claims = {"iss": self.issuer, "aud": "authenticated", "iat": now, "exp": now + 120,
                  "sub": str(user), "session_id": str(self.sessions[user]), "role": "authenticated",
                  "is_anonymous": False}
        claims.update(overrides)
        return jwt.encode(claims, self.secret, algorithm="HS256", headers={"kid": "local-contract"})

    def call(self, method, path, body=None, *, user=None, token=None, headers=None, raw=False):
        payload = body if isinstance(body, bytes) else None if body is None else json.dumps(body).encode()
        request_headers = {"Authorization": "Bearer " + (token or self.token(user))}
        request_headers.update(headers or {})
        request = Request(self.base + path, data=payload, headers=request_headers, method=method)
        try:
            response = urlopen(request, timeout=5)
        except URLHTTPError as error:
            response = error
        with response:
            data = response.read()
            self.assertEqual("no-store", response.headers.get("Cache-Control"))
            return response.code, data if raw else json.loads(data), response.headers

    def reserve(self, data=b"unvalidated synthetic bytes", *, user=None, org=None):
        org = self.f.org_a if org is None else org
        return self.call("POST", f"/v1/orgs/{org}/uploads", {"size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}, user=user)

    def request_render(self, asset, *, key="http-op", org=None):
        return self.call("POST", f"/v1/orgs/{org or self.f.org_a}/renders",
                         {"request_key": key, "asset_id": str(asset), "duration_ms": 800, "quality": "smoke"})

    def test_restricted_database_login_and_loopback_only(self):
        with psycopg.connect(self.dsn) as db:
            principal = db.execute("SELECT session_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=session_user").fetchone()
            self.assertEqual((self.login, False, False), principal)
            db.execute("SET LOCAL ROLE authenticated")
            self.assertEqual("authenticated", db.execute("SELECT current_user").fetchone()[0])
        with self.assertRaises(ValueError):
            MediaHTTPApplication(dsn=os.environ["G1_TEST_DSN"], verifier=self.verifier, store=self.f.store, authority=self.authority)
        with self.assertRaises(ValueError):
            local_http_server(self.app, host="0.0.0.0")
        with connect() as db:
            db.execute(sql.SQL("GRANT engine_worker TO {}").format(sql.Identifier(self.login)))
        try:
            with self.assertRaises(ValueError):
                MediaHTTPApplication(dsn=self.dsn, verifier=self.verifier, store=self.f.store, authority=self.authority)
        finally:
            with connect() as db:
                db.execute(sql.SQL("REVOKE engine_worker FROM {}").format(sql.Identifier(self.login)))

    def test_database_connection_admission_is_bounded(self):
        for _ in range(self.config.max_db_connections):
            self.app._db_slots.acquire()
        try:
            self.assertEqual(503, self.reserve()[0])
        finally:
            for _ in range(self.config.max_db_connections):
                self.app._db_slots.release()
        self.assertEqual(201, self.reserve()[0])

    def _assert_revocation_waits_for_authorized_io(self, operation, target, attribute):
        entered, release, revoker_started = threading.Event(), threading.Event(), threading.Event()
        original = getattr(target, attribute)
        backend = []
        def paused(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test failed to release authorized I/O")
            return original(*args, **kwargs)
        def revoke():
            with connect() as db:
                backend.append(db.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"])
                revoker_started.set()
                db.execute("DELETE FROM public.memberships WHERE organization_id=%s AND user_id=%s", (self.f.org_a, self.f.user_a))
        setattr(target, attribute, paused)
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                request = executor.submit(operation)
                try:
                    self.assertTrue(entered.wait(3))
                    revocation = executor.submit(revoke)
                    self.assertTrue(revoker_started.wait(3))
                    deadline = time.monotonic() + 3
                    locked = False
                    while time.monotonic() < deadline:
                        with connect() as db:
                            row = db.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s", (backend[0],)).fetchone()
                        if row and row["wait_event_type"] == "Lock":
                            locked = True
                            break
                        time.sleep(0.01)
                    self.assertTrue(locked, "actual PostgreSQL revocation must wait on membership row lock")
                    self.assertFalse(revocation.done())
                finally:
                    release.set()
                self.assertIn(request.result(timeout=5)[0], (200, 201))
                revocation.result(timeout=5)
        finally:
            release.set()
            setattr(target, attribute, original)
        self.assertEqual(403, self.reserve()[0])

    def test_upload_issuance_serializes_with_concurrent_membership_revocation(self):
        self._assert_revocation_waits_for_authorized_io(self.reserve, self.authority, "reserve")

    def test_private_download_serializes_with_concurrent_membership_revocation(self):
        asset = self.f.ingest()
        job = self.f.submit_media(asset)
        self.f.worker().run(job["id"])
        _, grant, _ = self.call("POST", f"/v1/orgs/{self.f.org_a}/renders/{job['id']}/downloads", {"preset": "vertical"})
        self._assert_revocation_waits_for_authorized_io(
            lambda: self.call("GET", grant["download_path"], raw=True), self.f.store, "get_file")

    def test_private_upload_bytes_receipt_no_asset_promotion_and_no_reuse(self):
        data = b"not accepted as verified media metadata"
        status, grant, _ = self.reserve(data)
        self.assertEqual(201, status)
        self.assertNotIn(str(self.f.root), json.dumps(grant))
        status, receipt, _ = self.call("PUT", grant["upload_path"], data)
        self.assertEqual(201, status)
        self.assertEqual("uploaded_pending_validation", receipt["state"])
        status, recovered, _ = self.call("GET", f"/v1/orgs/{self.f.org_a}/uploads/{grant['id']}")
        self.assertEqual(200, status)
        self.assertEqual("uploaded_pending_validation", recovered["state"])
        self.assertEqual(receipt["sha256"], recovered["sha256"])
        keys = self.f.store.list_keys(self.f.org_a)
        self.assertEqual([f"{self.f.org_a}/uploads/{grant['id']}/unvalidated.bin"], keys)
        with connect() as db:
            self.assertEqual(0, db.execute("SELECT count(*) AS n FROM public.media_bindings WHERE organization_id=%s", (self.f.org_a,)).fetchone()["n"])
        self.assertEqual(409, self.call("PUT", grant["upload_path"], data)[0])

    def test_upload_hash_size_tamper_cross_user_and_membership_revocation(self):
        _, grant, _ = self.reserve(b"abc")
        self.assertEqual(400, self.call("PUT", grant["upload_path"], b"bad")[0])
        self.assertEqual(400, self.call("PUT", grant["upload_path"], b"abc", user=self.f.user_b)[0])
        token = grant["upload_path"]
        altered = token[:-1] + ("A" if token[-1] != "A" else "B")
        self.assertEqual(400, self.call("PUT", altered, b"abc")[0])
        with connect() as db:
            db.execute("DELETE FROM public.memberships WHERE organization_id=%s AND user_id=%s", (self.f.org_a, self.f.user_a))
        self.assertEqual(403, self.call("PUT", token, b"abc")[0])
        self.assertEqual([], self.f.store.list_keys(self.f.org_a))

    def test_concurrent_upload_capability_consumption_is_atomic(self):
        _, grant, _ = self.reserve(b"abc")
        with ThreadPoolExecutor(max_workers=4) as executor:
            statuses = list(executor.map(lambda _: self.call("PUT", grant["upload_path"], b"abc")[0], range(4)))
        self.assertEqual([201, 409, 409, 409], sorted(statuses))
        self.assertEqual(1, len(self.f.store.list_keys(self.f.org_a)))

    def test_upload_storage_reservation_count_and_bytes_persist_on_restart(self):
        for _ in range(4):
            self.assertEqual(201, self.reserve()[0])
        self.assertEqual(429, self.reserve()[0])
        reopened = UploadAuthority(self.authority.database, self.authority.signing_key,
                                   max_org_uploads=4, max_org_bytes=4096)
        with self.assertRaises(UploadQuotaExceeded):
            reopened.reserve(self.f.org_a, self.f.user_a, size_bytes=1, sha256="a" * 64)
        self.assertEqual(201, self.reserve(user=self.f.user_b, org=self.f.org_b)[0])

    def test_upload_expiration_and_object_before_receipt_reconciliation(self):
        now = [int(time.time())]
        self.authority.clock = lambda: now[0]
        _, grant, _ = self.reserve(b"abc")
        claims = self.authority.verify(grant["upload_path"].split("/")[-1], kind="upload", user_id=self.f.user_a)
        source = self.f.root / "crash-input"
        source.write_bytes(b"abc")
        self.f.store.put_file(self.f.org_a, claims["key"], source)
        _, unused, _ = self.reserve(b"def")
        now[0] += 121
        self.assertEqual(400, self.call("PUT", unused["upload_path"], b"def")[0])
        self.assertEqual({"expired_unused": 1, "recovered_uploaded": 1}, self.authority.reconcile_expired(self.f.store))
        self.assertEqual([claims["key"]], self.f.store.list_keys(self.f.org_a))

    def test_upload_body_and_http_framing_are_bounded(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        connection.request("PUT", "/v1/uploads/fake", body=b"", headers={"Content-Length": str(self.config.max_body_bytes + 1)})
        response = connection.getresponse()
        self.assertEqual(413, response.status)
        response.read()
        connection.close()
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        connection.request("POST", "/v1/orgs/fake/uploads", body=b"", headers={"Transfer-Encoding": "chunked"})
        response = connection.getresponse()
        self.assertEqual(400, response.status)
        response.read()
        connection.close()

    def test_body_timeout_and_bounded_http_concurrency(self):
        sockets = []
        try:
            for _ in range(self.config.max_connections):
                sock = socket.create_connection(("127.0.0.1", self.server.server_port), timeout=3)
                sock.sendall(b"POST / HTTP/1.0\r\nContent-Length: 10\r\n\r\n")
                sockets.append(sock)
            time.sleep(0.1)
            extra = socket.create_connection(("127.0.0.1", self.server.server_port), timeout=3)
            try:
                extra.sendall(b"GET / HTTP/1.0\r\n\r\n")
                self.assertIn(b"503", extra.recv(4096))
            finally:
                extra.close()
            self.assertIn(b"408", sockets[0].recv(4096))
        finally:
            for sock in sockets:
                sock.close()

    def test_progressing_slow_body_cannot_extend_absolute_receive_deadline(self):
        sock = socket.create_connection(("127.0.0.1", self.server.server_port), timeout=3)
        try:
            sock.sendall(b"POST / HTTP/1.0\r\nContent-Length: 10\r\n\r\na")
            time.sleep(0.4)
            sock.sendall(b"b")
            time.sleep(0.4)
            sock.sendall(b"c")
            start = time.monotonic()
            self.assertIn(b"408", sock.recv(4096))
            self.assertLess(time.monotonic() - start, 0.7)
        finally:
            sock.close()

    def test_forged_headers_metadata_service_role_and_revoked_session_rejected(self):
        self.assertEqual(401, self.reserve_with_token("not-a-jwt")[0])
        self.assertEqual(401, self.reserve_with_token(self.token(role="service_role"))[0])
        self.assertEqual(401, self.reserve_with_token(self.token(exp=int(time.time()) - 1))[0])
        forged = self.token(self.f.user_b, user_metadata={"organization_id": str(self.f.org_a), "role": "owner"})
        self.assertEqual(403, self.call("POST", f"/v1/orgs/{self.f.org_a}/uploads", {"size_bytes": 1, "sha256": "a" * 64},
                                       token=forged, headers={"X-User-ID": str(self.f.user_a), "X-Organization-ID": str(self.f.org_a)})[0])
        token = self.token()
        del self.sessions[self.f.user_a]
        self.assertEqual(401, self.reserve_with_token(token)[0])

    def reserve_with_token(self, token):
        return self.call("POST", f"/v1/orgs/{self.f.org_a}/uploads", {"size_bytes": 1, "sha256": "a" * 64}, token=token)

    def test_viewer_can_read_but_cannot_submit_or_reserve(self):
        asset = self.f.ingest()
        job = self.f.submit_media(asset)
        with connect() as db:
            db.execute("UPDATE public.memberships SET role='viewer' WHERE organization_id=%s AND user_id=%s", (self.f.org_a, self.f.user_a))
        self.assertEqual(200, self.call("GET", f"/v1/orgs/{self.f.org_a}/renders/{job['id']}")[0])
        self.assertEqual(403, self.reserve()[0])
        self.assertEqual(403, self.request_render(asset["id"])[0])

    def test_render_requires_verified_binding_and_cross_org_isolation(self):
        self.assertEqual(409, self.request_render(self.f.asset_a)[0])
        other_asset = self.f.ingest(other=True)
        self.assertEqual(409, self.request_render(other_asset["id"])[0])
        other_job = self.f.submit_media(other_asset, other=True)
        self.assertEqual(403, self.call("GET", f"/v1/orgs/{self.f.org_b}/renders/{other_job['id']}")[0])
        self.assertEqual(404, self.call("GET", f"/v1/orgs/{self.f.org_a}/renders/{other_job['id']}")[0])

    def test_concurrent_http_submission_one_job_outbox_and_reservation(self):
        asset = self.f.ingest()
        def submit(_):
            for attempt in range(20):
                response = self.request_render(asset["id"])
                if response[0] != 503:
                    return response
                time.sleep(0.02)
            return response
        with ThreadPoolExecutor(max_workers=8) as executor:
            responses = list(executor.map(submit, range(16)))
        self.assertEqual([202] * 16, [row[0] for row in responses])
        self.assertEqual(1, len({row[1]["id"] for row in responses}))
        counts = self.f.counts()
        self.assertEqual((1, 1, 1, 3), (counts["jobs"], counts["outbox"], counts["audit"], counts["reserved"]))
        self.assertEqual(409, self.request_render(self.f.asset_a)[0])

    def test_real_media_worker_then_private_download_and_current_authority(self):
        asset = self.f.ingest()
        status, job, _ = self.request_render(asset["id"])
        self.assertEqual(202, status)
        # Rendering is explicitly outside the HTTP process/request path.
        completed = self.f.worker().run(job["id"])
        endpoint = f"/v1/orgs/{self.f.org_a}/renders/{job['id']}/downloads"
        status, grant, _ = self.call("POST", endpoint, {"preset": "vertical"})
        self.assertEqual(200, status)
        status, video, headers = self.call("GET", grant["download_path"], raw=True)
        self.assertEqual(200, status)
        self.assertEqual("video/mp4", headers["Content-Type"])
        expected = next(o for o in completed["output_manifest"]["outputs"] if o["preset"] == "vertical")
        self.assertEqual(expected["sha256"], hashlib.sha256(video).hexdigest())
        self.assertEqual(expected["size_bytes"], len(video))
        self.assertEqual(400, self.call("GET", grant["download_path"], user=self.f.user_b)[0])
        with connect() as db:
            db.execute("DELETE FROM public.memberships WHERE organization_id=%s AND user_id=%s", (self.f.org_a, self.f.user_a))
        self.assertEqual(403, self.call("GET", grant["download_path"])[0])

    def test_download_expired_corrupt_or_uncompleted_never_served(self):
        asset = self.f.ingest()
        job = self.f.submit_media(asset)
        endpoint = f"/v1/orgs/{self.f.org_a}/renders/{job['id']}/downloads"
        self.assertEqual(409, self.call("POST", endpoint, {"preset": "vertical"})[0])
        completed = self.f.worker().run(job["id"])
        _, grant, _ = self.call("POST", endpoint, {"preset": "vertical"})
        now = int(time.time())
        self.authority.clock = lambda: now + 121
        self.assertEqual(400, self.call("GET", grant["download_path"])[0])
        self.authority.clock = time.time
        output = next(o for o in completed["output_manifest"]["outputs"] if o["preset"] == "vertical")
        self.f.store.delete(self.f.org_a, output["key"], expected_sha256=output["sha256"])
        self.assertEqual(409, self.call("GET", grant["download_path"])[0])

    def test_post_rejects_untrusted_media_metadata_and_unknown_fields(self):
        status, _, _ = self.call("POST", f"/v1/orgs/{self.f.org_a}/uploads", {"size_bytes": 1, "sha256": "a" * 64, "rights_confirmed": True})
        self.assertEqual(400, status)
        asset = self.f.ingest()
        self.assertEqual(400, self.call("POST", f"/v1/orgs/{self.f.org_a}/renders", {"request_key": "x", "asset_id": str(asset["id"]),
                                       "duration_ms": 800, "quality": "smoke", "organization_id": str(self.f.org_b)})[0])


if __name__ == "__main__":
    unittest.main()
