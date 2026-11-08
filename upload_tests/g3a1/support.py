"""Disposable PostgreSQL and independent-process loopback HTTP lab helpers.

SharedHTTPFixture uses signed LOCAL_CRYPTO JWT fixtures and a fixture session
callback. It proves the cryptographic boundary, not official Supabase Auth.
serve_replica also accepts session_dsn, which selects the real, uncached
PostgresSessionChecker for the separate official local Auth integration suite.
Neither mode substitutes a mock for upload SQL, storage or HTTP execution.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import time
from uuid import UUID, uuid4

import jwt
from psycopg import sql
from psycopg.conninfo import make_conninfo

from engine.cloud_auth import AuthVerifier, PostgresSessionChecker, TrustedKey
from engine.cloud_http import HTTPConfig
from engine.object_store import LocalObjectStore
from engine.shared_upload_authority import PostgresUploadAuthority
from engine.shared_upload_http import SharedMediaHTTPApplication, shared_http_server
from integration.g1.support import Fixture, connect


def serve_replica(control, configuration):
    """Spawn target: build independent authority/semaphores, report port + PID.

    Required configuration: dsn, store_root, work_dir, signing_key,
    trust_secret, issuer. Optional: session_dsn (official Auth), sessions
    (LOCAL_CRYPTO fixture mapping of UUID strings), authority_options and
    http_options. The control channel never returns credentials or tokens.
    """
    server = None
    try:
        os.chdir(configuration["work_dir"])
        if configuration.get("session_dsn"):
            checker = PostgresSessionChecker(configuration["session_dsn"])
        else:
            sessions = configuration["sessions"]
            checker = lambda user, session: sessions.get(str(user)) == str(session)
        verifier = AuthVerifier(
            issuer=configuration["issuer"],
            keys={configuration.get("kid", "g3a1-local-crypto"): TrustedKey("HS256", configuration["trust_secret"])},
            session_checker=checker,
        )
        authority = PostgresUploadAuthority(
            configuration["dsn"], configuration["signing_key"],
            **configuration.get("authority_options", {}),
        )
        app = SharedMediaHTTPApplication(
            dsn=configuration["dsn"], verifier=verifier,
            store=LocalObjectStore(Path(configuration["store_root"])), authority=authority,
            config=HTTPConfig(**configuration.get("http_options", {})),
        )
        server = shared_http_server(app)
        server.timeout = 0.05
        control.send({"pid": os.getpid(), "port": server.server_port})
        while not control.poll():
            server.handle_request()
        control.recv()
    except BaseException as error:
        # No capabilities, DSNs, provider messages or absolute paths in output.
        try:
            control.send({"error": type(error).__name__})
        except (BrokenPipeError, EOFError, OSError):
            pass
        raise
    finally:
        if server is not None:
            server.server_close()
        control.close()


class HTTPReplica:
    """One spawned OS process with a fresh private working directory."""

    def __init__(self, configuration, token_factory=None):
        self.configuration = dict(configuration)
        self.work_dir = Path(configuration["work_dir"])
        self.work_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
        self._token_factory = token_factory
        context = multiprocessing.get_context("spawn")
        self.control, child = context.Pipe()
        self.process = context.Process(target=serve_replica, args=(child, self.configuration), daemon=True)
        self.process.start()
        child.close()
        if not self.control.poll(15):
            self.stop(kill=True)
            raise RuntimeError("independent HTTP replica did not start")
        ready = self.control.recv()
        if "error" in ready:
            self.stop(kill=True)
            raise RuntimeError("independent HTTP replica initialization rejected: " + ready["error"])
        self.pid, self.port = ready["pid"], ready["port"]
        self.base = f"http://127.0.0.1:{self.port}"

    def stop(self, *, kill=False):
        if self.process.is_alive():
            if kill:
                self.process.kill()
            else:
                try:
                    self.control.send("stop")
                except (EOFError, OSError):
                    pass
            self.process.join(timeout=5)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(timeout=5)
        self.control.close()

    def call(self, method, path, body=None, *, user=None, token=None, headers=None, timeout=20):
        """A new connection for every call, safe for concurrent caller threads."""
        payload = body if isinstance(body, bytes) else None if body is None else json.dumps(body).encode()
        if token is None and self._token_factory is not None:
            token = self._token_factory(user)
        request_headers = {"Authorization": "Bearer " + token} if token else {}
        request_headers.update(headers or {})
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            connection.request(method, path, body=payload, headers=request_headers)
            response = connection.getresponse()
            data = response.read()
            if response.getheader("Cache-Control") != "no-store":
                # Admission rejection can occur before the normal handler.
                if response.status != 503:
                    raise AssertionError("private response must disable caching")
            return response.status, json.loads(data) if data else {}
        finally:
            connection.close()


class SharedHTTPFixture:
    """Real PostgreSQL setup plus LOCAL_CRYPTO identity fixtures, no SQLite."""

    def __init__(self, *, max_uploads=128, max_bytes=8 * 1048576, ttl_seconds=120, lease_seconds=2):
        self.f = Fixture()
        self.temporary = tempfile.TemporaryDirectory(prefix="ltc-g3a1-process-lab-")
        self.root = Path(self.temporary.name)
        self.store_root = self.root / "shared-private-objects"
        self.store = LocalObjectStore(self.store_root)
        self.signing_key = b"g3a1-local-capability-key-not-production-" * 2
        self.trust_secret = "g3a1-local-crypto-jwt-key-not-production-" * 2
        self.issuer = "http://127.0.0.1/g3a1-local-crypto-fixture"
        self.ttl_seconds, self.lease_seconds = ttl_seconds, lease_seconds
        self.replicas = []
        self.login = "g3a1_http_" + uuid4().hex
        self.user_c = uuid4()
        with connect() as db:
            db.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION").format(
                sql.Identifier(self.login), sql.Literal("g3a1-local-disposable")))
            db.execute(sql.SQL("GRANT authenticated TO {}").format(sql.Identifier(self.login)))
            db.execute("INSERT INTO auth.users(id) VALUES(%s)", (self.user_c,))
            db.execute("INSERT INTO public.memberships(organization_id,user_id,role) VALUES(%s,%s,'editor')", (self.f.org_a, self.user_c))
        self.dsn = make_conninfo(os.environ["G1_TEST_DSN"], user=self.login, password="g3a1-local-disposable")
        self.sessions = {str(user): str(uuid4()) for user in (self.f.user_a, self.f.user_b, self.user_c)}
        self.provision_quota(max_uploads=max_uploads, max_bytes=max_bytes, ttl_seconds=ttl_seconds)
        self.provision_quota(org=self.f.org_b, max_uploads=max_uploads, max_bytes=max_bytes, ttl_seconds=ttl_seconds)

    def provision_quota(self, *, org=None, max_uploads=128, max_bytes=8 * 1048576, ttl_seconds=None):
        with connect() as db:
            db.execute("""INSERT INTO g3a1_private.upload_limits
                (organization_id,max_bytes,max_count,max_upload_bytes,max_ttl_seconds,max_lease_seconds)
                VALUES(%s,%s,%s,%s,%s,60)
                ON CONFLICT(organization_id) DO UPDATE SET max_bytes=excluded.max_bytes,
                max_count=excluded.max_count,max_upload_bytes=excluded.max_upload_bytes,
                max_ttl_seconds=excluded.max_ttl_seconds""",
                (org or self.f.org_a, max_bytes, max_uploads, min(1048576, max_bytes), ttl_seconds or self.ttl_seconds))

    def authority(self, **options):
        defaults = {"lease_seconds": self.lease_seconds, "ttl_seconds": self.ttl_seconds}
        defaults.update(options)
        return PostgresUploadAuthority(self.dsn, self.signing_key, **defaults)

    def token(self, user=None, **overrides):
        user = self.f.user_a if user is None else UUID(str(user))
        now = int(time.time())
        claims = {"iss": self.issuer, "aud": "authenticated", "iat": now, "exp": now + 600,
                  "sub": str(user), "session_id": self.sessions[str(user)], "role": "authenticated", "is_anonymous": False}
        claims.update(overrides)
        return jwt.encode(claims, self.trust_secret, algorithm="HS256", headers={"kid": "g3a1-local-crypto"})

    def new_replica(self, **http_options):
        options = {"max_connections": 64, "max_db_connections": 4, "request_timeout_seconds": 3}
        options.update(http_options)
        replica = HTTPReplica({
            "dsn": self.dsn, "store_root": str(self.store_root),
            "work_dir": str(self.root / ("replica-" + uuid4().hex)),
            "signing_key": self.signing_key, "trust_secret": self.trust_secret, "issuer": self.issuer,
            "sessions": self.sessions, "authority_options": {"ttl_seconds": self.ttl_seconds, "lease_seconds": self.lease_seconds},
            "http_options": options,
        }, self.token)
        self.replicas.append(replica)
        return replica

    def reserve(self, replica, *, request_key="operation", data=b"synthetic unvalidated bytes", user=None, org=None):
        return replica.call("POST", f"/v1/orgs/{org or self.f.org_a}/uploads", {
            "request_key": request_key, "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}, user=user)

    def cleanup(self):
        for replica in self.replicas:
            replica.stop()
        with connect() as db:
            for table in ("upload_events", "upload_receipts", "upload_reservations", "upload_limits"):
                db.execute(sql.SQL("DELETE FROM g3a1_private.{} WHERE organization_id=ANY(%s)").format(sql.Identifier(table)),
                           ([self.f.org_a, self.f.org_b],))
        self.f.cleanup()
        with connect() as db:
            db.execute("DELETE FROM auth.users WHERE id=%s", (self.user_c,))
            db.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(self.login)))
        self.temporary.cleanup()
