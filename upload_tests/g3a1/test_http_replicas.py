"""Blocking real PostgreSQL + real independent-process HTTP upload contracts.

Identity here is LOCAL_CRYPTO fixture coverage. Official local Supabase Auth
issuance/session revocation is exercised by a separate blocking integration.
No service/emulator result is described as a real cloud integration.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import threading
import time
import unittest

from engine.cloud_auth import AuthVerifier, TrustedKey
from engine.cloud_http import HTTPConfig
from engine.object_store import LocalObjectStore
from engine.shared_upload_http import SharedMediaHTTPApplication, shared_http_server
from engine.upload_authority import UploadAuthority
from integration.g1.support import connect
from upload_tests.g3a1.support import HTTPReplica, SharedHTTPFixture


class SharedHTTPReplicaTests(unittest.TestCase):
    def setUp(self):
        self.lab = SharedHTTPFixture()
        self.addCleanup(self.lab.cleanup)
        self.a, self.b = self.lab.new_replica(), self.lab.new_replica()
        self.assertNotEqual(self.a.pid, self.b.pid)
        self.assertNotIn(os.getpid(), (self.a.pid, self.b.pid))

    def _concurrent_reservations(self, requests):
        barrier = threading.Barrier(len(requests))
        def submit(index):
            replica = (self.a, self.b)[index % 2]
            barrier.wait(timeout=10)
            deadline = time.monotonic() + 20
            while True:
                result = self.lab.reserve(replica, **requests[index])
                if result[0] != 503 or time.monotonic() >= deadline:
                    return result
                # The fixed replica SQL connection caps remain active. A retry
                # of the same immutable request must never add another charge.
                time.sleep(0.015)
        with ThreadPoolExecutor(max_workers=len(requests)) as executor:
            return list(executor.map(submit, range(len(requests))))

    def _quota(self):
        with connect() as db:
            return db.execute("SELECT used_bytes,used_count FROM g3a1_private.upload_limits WHERE organization_id=%s",
                              (self.lab.f.org_a,)).fetchone()

    def test_100_identical_requests_across_two_processes_one_grant_one_charge(self):
        data = b"same immutable synthetic bytes"
        results = self._concurrent_reservations([{"request_key": "barrier-100", "data": data}] * 100)
        self.assertEqual([201] * 100, [result[0] for result in results])
        grants = [result[1] for result in results]
        for field in ("id", "expires_at", "upload_path", "size_bytes"):
            self.assertEqual(1, len({grant[field] for grant in grants}), field)
        authority = self.lab.authority()
        claims = authority.verify(grants[0]["upload_path"].split("/")[-1], kind="upload", user_id=self.lab.f.user_a)
        self.assertEqual(f"{self.lab.f.org_a}/uploads/{grants[0]['id']}/unvalidated.bin", claims["key"])
        with connect() as db:
            self.assertEqual(1, db.execute("SELECT count(*) AS n FROM g3a1_private.upload_reservations WHERE organization_id=%s",
                                          (self.lab.f.org_a,)).fetchone()["n"])
            self.assertEqual(1, db.execute("SELECT count(*) AS n FROM g3a1_private.upload_events WHERE organization_id=%s AND event_type='reserved'",
                                          (self.lab.f.org_a,)).fetchone()["n"])
        self.assertEqual({"used_bytes": len(data), "used_count": 1}, self._quota())
        self.assertEqual(409, self.lab.reserve(self.b, request_key="barrier-100", data=b"different payload")[0])
        self.assertEqual({"used_bytes": len(data), "used_count": 1}, self._quota())

    def test_20_distinct_requests_admit_exact_shared_count_and_byte_quota(self):
        self.lab.provision_quota(max_uploads=10, max_bytes=40)
        results = self._concurrent_reservations([{"request_key": f"distinct-{i}", "data": b"1234"} for i in range(20)])
        self.assertEqual([201] * 10 + [429] * 10, sorted(result[0] for result in results))
        self.assertEqual(10, len({body["id"] for status, body in results if status == 201}))
        self.assertEqual({"used_bytes": 40, "used_count": 10}, self._quota())
        # Another organization has independent quota and cannot borrow these IDs.
        self.assertEqual(201, self.lab.reserve(self.b, org=self.lab.f.org_b, user=self.lab.f.user_b)[0])

    def test_publish_on_a_read_on_b_kill_a_restart_c_recover_same_grant_receipt(self):
        data = b"persistent private unvalidated upload across replicas"
        status, grant = self.lab.reserve(self.a, request_key="persistent", data=data)
        self.assertEqual(201, status)
        status, receipt = self.a.call("PUT", grant["upload_path"], data)
        self.assertEqual(201, status)
        self.assertEqual("uploaded_pending_validation", receipt["state"])
        path = f"/v1/orgs/{self.lab.f.org_a}/uploads/{grant['id']}"
        self.assertEqual((200, receipt), self.b.call("GET", path))
        self.a.stop(kill=True)
        self.assertEqual(-9, self.a.process.exitcode)
        c = self.lab.new_replica()
        self.assertNotEqual(self.a.pid, c.pid)
        self.assertNotEqual(self.a.work_dir, c.work_dir)
        self.assertEqual((200, receipt), c.call("GET", path))
        status, duplicate = self.lab.reserve(c, request_key="persistent", data=data)
        self.assertEqual(201, status)
        for field in ("id", "expires_at", "upload_path"):
            self.assertEqual(grant[field], duplicate[field])
        self.assertEqual((201, receipt), c.call("PUT", grant["upload_path"], data))
        self.assertEqual({"used_bytes": len(data), "used_count": 1}, self._quota())
        self.assertEqual([receipt["object_key"]], self.lab.store.list_keys(self.lab.f.org_a))
        self.assertEqual([], list(self.lab.root.rglob("*.sqlite*")))
        for work_dir in (self.a.work_dir, self.b.work_dir, c.work_dir):
            self.assertEqual([], list(work_dir.iterdir()), "replica private state cannot be the upload authority")
        with connect() as db:
            self.assertEqual(0, db.execute("SELECT count(*) AS n FROM public.media_bindings WHERE organization_id=%s",
                                          (self.lab.f.org_a,)).fetchone()["n"])
        self.assertNotIn(str(self.lab.root), json.dumps(receipt))
        self.assertNotIn(self.lab.signing_key.decode(), json.dumps(receipt))

    def test_organizations_owners_revocation_and_forged_identity_headers(self):
        _, grant = self.lab.reserve(self.a, request_key="owner-only", data=b"abc")
        path = f"/v1/orgs/{self.lab.f.org_a}/uploads/{grant['id']}"
        self.assertEqual(201, self.a.call("PUT", grant["upload_path"], b"abc")[0])
        self.assertEqual(403, self.b.call("GET", path, user=self.lab.f.user_b)[0])
        self.assertEqual(400, self.b.call("GET", path, user=self.lab.user_c)[0])
        self.assertEqual(400, self.b.call("POST", path + "/reconcile", user=self.lab.user_c)[0])
        self.assertEqual(409, self.lab.reserve(self.b, request_key="owner-only", data=b"abc", user=self.lab.user_c)[0])
        self.assertEqual(400, self.b.call("PUT", grant["upload_path"], b"abc", user=self.lab.user_c)[0])
        forged = self.lab.token(self.lab.f.user_b, user_metadata={"organization_id": str(self.lab.f.org_a), "role": "owner"})
        self.assertEqual(403, self.b.call("GET", path, token=forged, headers={
            "X-User-ID": str(self.lab.f.user_a), "X-Organization-ID": str(self.lab.f.org_a)})[0])
        with connect() as db:
            db.execute("DELETE FROM public.memberships WHERE organization_id=%s AND user_id=%s",
                       (self.lab.f.org_a, self.lab.f.user_a))
        for replica in (self.a, self.b):
            self.assertEqual(403, replica.call("GET", path)[0])
            self.assertEqual(403, replica.call("PUT", grant["upload_path"], b"abc")[0])
            self.assertEqual(403, self.lab.reserve(replica, request_key="after-revoke")[0])
        self.assertEqual({"used_bytes": 3, "used_count": 1}, self._quota())

    def test_unused_expiration_refunds_exactly_once_across_replicas(self):
        self.lab.provision_quota(max_uploads=1, max_bytes=3, ttl_seconds=1)
        _, grant = self.lab.reserve(self.a, request_key="unused", data=b"abc")
        self.assertEqual(429, self.lab.reserve(self.b, request_key="too-soon", data=b"abc")[0])
        time.sleep(1.2)
        path = f"/v1/orgs/{self.lab.f.org_a}/uploads/{grant['id']}"
        self.assertEqual("expired", self.b.call("POST", path + "/reconcile")[1]["state"])
        self.assertEqual("expired", self.a.call("POST", path + "/reconcile")[1]["state"])
        self.assertEqual({"used_bytes": 0, "used_count": 0}, self._quota())
        self.assertEqual(400, self.a.call("PUT", grant["upload_path"], b"abc")[0])
        self.assertEqual(400, self.b.call("POST", path + "/resume", b"abc")[0])
        self.assertEqual(201, self.lab.reserve(self.b, request_key="after-expiration", data=b"abc")[0])
        with connect() as db:
            self.assertEqual(1, db.execute("SELECT count(*) AS n FROM g3a1_private.upload_events WHERE reservation_id=%s AND event_type='expired'",
                                          (grant["id"],)).fetchone()["n"])

    def test_explicit_resume_uses_binary_cap_and_never_recharges_claimed_payload(self):
        data = b"x" * 20000  # Larger than the historical JSON body cap.
        _, grant = self.lab.reserve(self.a, request_key="resume-binary", data=data)
        authority = self.lab.authority()
        claim = authority._call(self.lab.f.user_a, "claim_upload", self.lab.f.org_a, grant["id"], 2)
        authority._call(self.lab.f.user_a, "mark_upload_uncertain", self.lab.f.org_a, grant["id"], claim["lease_token"], claim["fence"], "TEST_INTERRUPTION")
        path = f"/v1/orgs/{self.lab.f.org_a}/uploads/{grant['id']}"
        self.assertEqual("uncertain", self.b.call("POST", path + "/reconcile")[1]["state"])
        self.assertEqual({"used_bytes": len(data), "used_count": 1}, self._quota())
        self.assertEqual(400, self.b.call("POST", path + "/resume", b"y" * len(data))[0])
        status, receipt = self.b.call("POST", path + "/resume", data)
        self.assertEqual(201, status)
        self.assertEqual(hashlib.sha256(data).hexdigest(), receipt["sha256"])
        self.assertEqual((200, receipt), self.a.call("GET", path))
        self.assertEqual({"used_bytes": len(data), "used_count": 1}, self._quota())

    def test_schema_validation_jwt_security_and_render_delegation(self):
        path = f"/v1/orgs/{self.lab.f.org_a}/uploads"
        self.assertEqual(400, self.a.call("POST", path, {"size_bytes": 1, "sha256": "a" * 64})[0])
        for value in (True, 0, -1, "1"):
            self.assertEqual(400, self.a.call("POST", path, {"request_key": "invalid", "size_bytes": value, "sha256": "a" * 64})[0])
        self.assertEqual(401, self.a.call("POST", path, {}, token="not-a-jwt")[0])
        self.assertEqual(401, self.a.call("POST", path, {}, token=self.lab.token(role="service_role"))[0])
        self.assertEqual(401, self.a.call("POST", path, {}, token=self.lab.token(exp=int(time.time()) - 1))[0])
        self.assertEqual(400, self.a.call("POST", path, {"request_key": "extra", "size_bytes": 1, "sha256": "a" * 64, "rights_confirmed": True})[0])
        # Historical render admission still requires a trusted G2 media binding.
        self.assertEqual(409, self.a.call("POST", f"/v1/orgs/{self.lab.f.org_a}/renders", {
            "request_key": "unverified", "asset_id": str(self.lab.f.asset_a), "duration_ms": 800, "quality": "smoke"})[0])

    def test_resume_transport_body_framing_limits_and_absolute_timeout(self):
        slow = self.lab.new_replica(request_timeout_seconds=1, max_connections=2)
        path = f"/v1/orgs/{self.lab.f.org_a}/uploads/{self.lab.f.asset_a}/resume"
        connection = http.client.HTTPConnection("127.0.0.1", slow.port, timeout=3)
        try:
            connection.request("POST", path, b"", headers={"Content-Length": str(1048577)})
            response = connection.getresponse()
            self.assertEqual(413, response.status)
            response.read()
        finally:
            connection.close()
        connection = http.client.HTTPConnection("127.0.0.1", slow.port, timeout=3)
        try:
            connection.request("POST", path, b"", headers={"Transfer-Encoding": "chunked"})
            response = connection.getresponse()
            self.assertEqual(400, response.status)
            response.read()
        finally:
            connection.close()
        sock = socket.create_connection(("127.0.0.1", slow.port), timeout=3)
        try:
            sock.sendall(f"POST {path} HTTP/1.0\r\nContent-Length: 10\r\n\r\na".encode())
            time.sleep(0.4)
            sock.sendall(b"b")
            time.sleep(0.4)
            sock.sendall(b"c")
            started = time.monotonic()
            self.assertIn(b"408", sock.recv(4096))
            self.assertLess(time.monotonic() - started, 0.7)
        finally:
            sock.close()

    def test_fail_closed_backend_and_privileged_dsn(self):
        verifier = AuthVerifier(issuer=self.lab.issuer, keys={"local": TrustedKey("HS256", self.lab.trust_secret)}, session_checker=lambda user, session: True)
        sqlite = UploadAuthority(self.lab.root / "legacy-test-only.sqlite3", self.lab.signing_key)
        with self.assertRaises(ValueError):
            SharedMediaHTTPApplication(dsn=self.lab.dsn, verifier=verifier, store=self.lab.store, authority=sqlite)
        with self.assertRaises(ValueError):
            SharedMediaHTTPApplication(dsn=os.environ["G1_TEST_DSN"], verifier=verifier, store=self.lab.store, authority=self.lab.authority())
        app = SharedMediaHTTPApplication(dsn=self.lab.dsn, verifier=verifier, store=self.lab.store, authority=self.lab.authority())
        with self.assertRaises(ValueError):
            shared_http_server(app, host="0.0.0.0")

    def test_revocation_during_real_http_write_has_no_nested_transaction_deadlock(self):
        entered, release = threading.Event(), threading.Event()
        class PausedStore(LocalObjectStore):
            def put_file(store, *args, **kwargs):
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("test did not release storage publication")
                return super().put_file(*args, **kwargs)
        verifier = AuthVerifier(issuer=self.lab.issuer, keys={"g3a1-local-crypto": TrustedKey("HS256", self.lab.trust_secret)},
                                session_checker=lambda user, session: self.lab.sessions.get(str(user)) == str(session))
        app = SharedMediaHTTPApplication(dsn=self.lab.dsn, verifier=verifier, store=PausedStore(self.lab.store_root), authority=self.lab.authority(),
                                         config=HTTPConfig(request_timeout_seconds=3))
        server = shared_http_server(app)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        client = object.__new__(HTTPReplica)
        client.port, client._token_factory = server.server_port, self.lab.token
        try:
            _, grant = self.lab.reserve(self.a, request_key="revoke-during-io", data=b"abc")
            with ThreadPoolExecutor(max_workers=2) as executor:
                request = executor.submit(client.call, "PUT", grant["upload_path"], b"abc")
                self.assertTrue(entered.wait(3), "real store publication was not reached")
                def revoke():
                    with connect() as db:
                        db.execute("SET LOCAL lock_timeout='1500ms'")
                        db.execute("DELETE FROM public.memberships WHERE organization_id=%s AND user_id=%s", (self.lab.f.org_a, self.lab.f.user_a))
                revocation = executor.submit(revoke)
                try:
                    revocation.result(timeout=2)
                finally:
                    release.set()
                status, _ = request.result(timeout=5)
            self.assertIn(status, (403, 409), "revoked member cannot commit a successful receipt")
            with connect() as db:
                self.assertEqual(0, db.execute("SELECT count(*) AS n FROM g3a1_private.upload_receipts WHERE id=%s", (grant["id"],)).fetchone()["n"])
            self.assertEqual({"used_bytes": 3, "used_count": 1}, self._quota())
            self.assertEqual(403, self.b.call("GET", f"/v1/orgs/{self.lab.f.org_a}/uploads/{grant['id']}")[0])
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
