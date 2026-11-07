"""Loopback-only G3A authenticated HTTP boundary, separate from media workers.

This private local service is an executable contract, not an internet deployment.
It accepts no identity/tenant headers, privileged PostgreSQL DSN, user media
metadata, worker arguments or arbitrary object paths. Upload receipts require a
separate trusted importer before they can become renderable G2 assets.
The local SQLite upload ledger does not provide shared multi-replica quotas;
cloud API enablement is BLOCKED pending a durable shared authority and a
production HTTP/ingress adapter reviewed separately in G3B.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import ipaddress
import json
from pathlib import Path
import re
import socket
from socketserver import ThreadingMixIn
import tempfile
import threading
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from .cloud_auth import AuthError
from .object_store import ObjectStoreError
from .upload_authority import CapabilityError, UploadAlreadyUsed, UploadQuotaExceeded


class HTTPError(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code


@dataclass(frozen=True)
class HTTPConfig:
    max_body_bytes: int = 1048576
    max_json_bytes: int = 16384
    max_download_bytes: int = 32 * 1048576
    max_connections: int = 8
    max_db_connections: int = 4
    request_timeout_seconds: int = 5
    download_ttl_seconds: int = 60

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in self.__dict__.values()):
            raise ValueError("HTTP limits must be positive integers")
        if self.download_ttl_seconds > 120 or self.request_timeout_seconds > 30:
            raise ValueError("HTTP timeout and authorization TTL must remain bounded")


def _uuid(value):
    try:
        if str(UUID(value)) != value:
            raise ValueError
        return value
    except (ValueError, TypeError, AttributeError):
        raise HTTPError(400, "invalid_identifier") from None


def _json(body, fields):
    try:
        value = json.loads(body)
        if not isinstance(value, dict) or set(value) != set(fields):
            raise ValueError
        return value
    except (ValueError, TypeError, UnicodeError):
        raise HTTPError(400, "invalid_request") from None


class MediaHTTPApplication:
    def __init__(self, *, dsn, verifier, store, authority, config=None):
        self.dsn, self.verifier, self.store, self.authority = dsn, verifier, store, authority
        self.config = config or HTTPConfig()
        if self.authority.max_upload_bytes > self.config.max_body_bytes:
            raise ValueError("upload capability exceeds HTTP body limit")
        self._db_slots = threading.BoundedSemaphore(self.config.max_db_connections)
        self._validate_principal()

    def _validate_principal(self):
        """Fail closed before serving if a privileged authenticator DSN is used."""
        with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5) as db:
            principal = db.execute("SELECT oid,rolname,rolsuper,rolbypassrls,rolcreaterole,rolcreatedb,"
                                   "rolreplication,rolinherit FROM pg_roles WHERE rolname=session_user").fetchone()
            ownership = db.execute("SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                                   "WHERE c.relowner IN (%s,'authenticated'::regrole) "
                                   "AND n.nspname IN ('public','auth','g3a_private')) AS unsafe", (principal["oid"],)).fetchone()["unsafe"]
            roles = db.execute("""WITH RECURSIVE member(roleid) AS (
                SELECT roleid FROM pg_auth_members WHERE member=%s
                UNION SELECT m.roleid FROM pg_auth_members m JOIN member r ON r.roleid=m.member)
                SELECT rolname FROM pg_roles WHERE oid IN (SELECT roleid FROM member)""", (principal["oid"],)).fetchall()
            names = {r["rolname"] for r in roles}
            if (any(principal[k] for k in ("rolsuper", "rolbypassrls", "rolcreaterole", "rolcreatedb", "rolreplication", "rolinherit"))
                    or ownership or "authenticated" not in names
                    or not names <= {"authenticated", "g3a_session_reader"}):
                raise ValueError("HTTP requires a non-owner NOINHERIT login with only authenticated/session-reader membership")
            db.execute("SET LOCAL ROLE authenticated")
            row = db.execute("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user").fetchone()
            if row["rolsuper"] or row["rolbypassrls"]:
                raise ValueError("authenticated role must enforce RLS")

    @contextmanager
    def _connection(self, identity):
        if not self._db_slots.acquire(timeout=0.1):
            raise HTTPError(503, "database_busy")
        try:
            with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=3) as db:
                db.execute("SET LOCAL statement_timeout='5s'")
                db.execute("SET LOCAL lock_timeout='2s'")
                db.execute("SET LOCAL ROLE authenticated")
                db.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (str(identity.user_id),))
                db.execute("SELECT set_config('request.jwt.claims',%s,true)",
                           (json.dumps({"sub": str(identity.user_id), "role": "authenticated"}),))
                yield db
        finally:
            self._db_slots.release()

    @staticmethod
    def _membership(db, identity, org, *, write=False):
        # A narrow SECURITY DEFINER function locks only auth.uid()'s own row;
        # authenticated retains no UPDATE privilege on the membership table.
        # Revocation then serializes with grant/publication/download authorization.
        try:
            row = db.execute("SELECT g3a_private.lock_api_membership(%s,%s) AS role", (org, write)).fetchone()
        except psycopg.errors.InsufficientPrivilege:
            raise HTTPError(403, "organization_access_denied") from None
        if not row or write and row["role"] not in {"owner", "admin", "editor"}:
            raise HTTPError(403, "organization_access_denied")

    @staticmethod
    def _job(db, org, job):
        row = db.execute("SELECT id,organization_id,state,created_at,updated_at,output_manifest "
                         "FROM public.render_jobs WHERE organization_id=%s AND id=%s", (org, job)).fetchone()
        if not row:
            raise HTTPError(404, "render_not_found")
        return row

    @staticmethod
    def _output(job, preset):
        if job["state"] != "completed" or not isinstance(job["output_manifest"], dict):
            raise HTTPError(409, "render_not_completed")
        matches = [o for o in job["output_manifest"].get("outputs", []) if o.get("preset") == preset]
        if len(matches) != 1:
            raise HTTPError(404, "output_not_found")
        return matches[0]

    def handle(self, method, path, authorization, body=b""):
        try:
            if not isinstance(authorization, str) or not authorization.startswith("Bearer ") or authorization.count(" ") != 1:
                raise HTTPError(401, "authentication_required")
            identity = self.verifier.verify(authorization[7:])
            if len(path) > 8192 or "?" in path or "#" in path or "%" in path:
                raise HTTPError(400, "invalid_path")
            upload_match = re.fullmatch(r"/v1/uploads/([A-Za-z0-9_.-]+)", path)
            download_match = re.fullmatch(r"/v1/downloads/([A-Za-z0-9_.-]+)", path)
            if upload_match and method == "PUT":
                claims = self.authority.verify(upload_match[1], kind="upload", user_id=identity.user_id)
                with self._connection(identity) as db:
                    self._membership(db, identity, claims["organization_id"], write=True)
                    if len(body) > self.config.max_body_bytes:
                        raise HTTPError(413, "body_too_large")
                    with tempfile.TemporaryDirectory(prefix="ltc-private-upload-") as name:
                        source = Path(name) / "unvalidated.bin"
                        source.write_bytes(body)
                        source.chmod(0o600)
                        receipt = self.authority.publish(upload_match[1], identity.user_id, source, self.store)
                return 201, receipt, "application/json"
            if download_match and method == "GET":
                claims = self.authority.verify(download_match[1], kind="download", user_id=identity.user_id)
                with self._connection(identity) as db:
                    self._membership(db, identity, claims["organization_id"])
                    job = self._job(db, claims["organization_id"], _uuid(claims["job_id"]))
                    output = self._output(job, claims["preset"])
                    if any(output[k] != claims[k] for k in ("key", "sha256", "size_bytes")):
                        raise HTTPError(409, "output_authority_changed")
                    if type(output["size_bytes"]) is not int or not 0 < output["size_bytes"] <= self.config.max_download_bytes:
                        raise HTTPError(413, "download_limit_exceeded")
                    with tempfile.TemporaryDirectory(prefix="ltc-private-download-") as name:
                        target = Path(name) / "output.mp4"
                        stored = self.store.get_file(claims["organization_id"], output["key"], target,
                                                     max_bytes=output["size_bytes"])
                        if any(stored[k] != output[k] for k in ("key", "sha256", "size_bytes")):
                            raise HTTPError(409, "output_integrity_failure")
                        data = target.read_bytes()
                        if hashlib.sha256(data).hexdigest() != output["sha256"]:
                            raise HTTPError(409, "output_integrity_failure")
                return 200, data, "video/mp4"
            match = re.fullmatch(r"/v1/orgs/([^/]+)/(uploads|renders)(?:/([^/]+))?(?:/(downloads))?", path)
            if not match:
                raise HTTPError(404, "route_not_found")
            org, resource, job_id, action = match.groups()
            org = _uuid(org)
            if len(body) > self.config.max_json_bytes:
                raise HTTPError(413, "body_too_large")
            with self._connection(identity) as db:
                self._membership(db, identity, org, write=method == "POST" and action != "downloads")
                if resource == "uploads" and not job_id and method == "POST":
                    request = _json(body, ("size_bytes", "sha256"))
                    self.authority.reconcile_expired(self.store, org_id=org)
                    result = self.authority.reserve(org, identity.user_id, **request)
                    result["upload_path"] = "/v1/uploads/" + result.pop("authorization")
                    return 201, result, "application/json"
                if resource == "uploads" and job_id and not action and method == "GET":
                    self.authority.reconcile_expired(self.store, org_id=org)
                    receipt = self.authority.receipt(org, identity.user_id, _uuid(job_id))
                    if not receipt:
                        raise HTTPError(404, "upload_receipt_not_found")
                    return 200, receipt, "application/json"
                if resource != "renders":
                    raise HTTPError(404, "route_not_found")
                if not job_id and method == "POST":
                    request = _json(body, ("request_key", "asset_id", "duration_ms", "quality"))
                    if (not isinstance(request["request_key"], str) or not 1 <= len(request["request_key"]) <= 128
                            or type(request["duration_ms"]) is not int or request["quality"] not in {"smoke", "production"}):
                        raise HTTPError(400, "invalid_request")
                    row = db.execute("SELECT id,organization_id,state,created_at,updated_at "
                                     "FROM public.submit_media_render(%s,%s,%s,%s,%s)",
                                     (org, request["request_key"], _uuid(request["asset_id"]),
                                      request["duration_ms"], request["quality"])).fetchone()
                    return 202, row, "application/json"
                if job_id:
                    job = self._job(db, org, _uuid(job_id))
                    if method == "GET" and not action:
                        return 200, {k: v for k, v in job.items() if k != "output_manifest"}, "application/json"
                    if method == "POST" and action == "downloads":
                        request = _json(body, ("preset",))
                        if request["preset"] not in {"vertical", "square", "feed"}:
                            raise HTTPError(400, "invalid_request")
                        output = self._output(job, request["preset"])
                        if output["size_bytes"] > self.config.max_download_bytes:
                            raise HTTPError(413, "download_limit_exceeded")
                        claims = {"kind": "download", "organization_id": org, "user_id": str(identity.user_id),
                                  "job_id": job_id, "preset": request["preset"], "verb": "GET",
                                  "expires_at": int(self.authority.clock()) + self.config.download_ttl_seconds,
                                  **{k: output[k] for k in ("key", "sha256", "size_bytes")}}
                        token = self.authority.sign(claims)
                        return 200, {"download_path": "/v1/downloads/" + token,
                                     "expires_at": claims["expires_at"], "size_bytes": output["size_bytes"]}, "application/json"
            raise HTTPError(405, "method_not_allowed")
        except AuthError:
            return 401, {"error": "invalid_or_revoked_session"}, "application/json"
        except HTTPError as error:
            return error.status, {"error": error.code}, "application/json"
        except UploadQuotaExceeded:
            return 429, {"error": "organization_upload_quota"}, "application/json"
        except UploadAlreadyUsed:
            return 409, {"error": "upload_authorization_consumed"}, "application/json"
        except CapabilityError:
            return 400, {"error": "invalid_private_authorization_or_bytes"}, "application/json"
        except ObjectStoreError:
            return 409, {"error": "private_storage_integrity_failure"}, "application/json"
        except psycopg.Error as error:
            # Never expose SQL/schema/DSN/provider details or caller-controlled text.
            code = "render_conflict_or_rejected" if error.sqlstate in {"P0001", "23505", "23514", "42501", "22023"} else "database_unavailable"
            return (409 if code.startswith("render_") else 503), {"error": code}, "application/json"


class _BoundedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    block_on_close = True
    allow_reuse_address = False

    def __init__(self, address, application):
        self.application = application
        self._slots = threading.BoundedSemaphore(application.config.max_connections)
        self.request_queue_size = application.config.max_connections
        super().__init__(address, _Handler)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            request.settimeout(self.application.config.request_timeout_seconds)
            try:
                request.sendall(b"HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class _Handler(BaseHTTPRequestHandler):
    server_version = "LocalContentEngine"
    sys_version = ""

    def setup(self):
        self.request.settimeout(self.server.application.config.request_timeout_seconds)
        super().setup()
        self._deadline_expired = False
        # A socket inactivity timeout alone permits a one-byte-at-a-time client
        # to retain a worker indefinitely. Bound total header/body receive time.
        self._receive_deadline = threading.Timer(
            self.server.application.config.request_timeout_seconds, self._expire_receive)
        self._receive_deadline.daemon = True
        self._receive_deadline.start()

    def _expire_receive(self):
        self._deadline_expired = True
        try:
            self.request.shutdown(socket.SHUT_RD)
        except OSError:
            pass

    def finish(self):
        self._receive_deadline.cancel()
        super().finish()

    def log_message(self, format, *args):
        # Paths contain private capabilities; never emit them to an access log.
        pass

    def _request(self):
        app = self.server.application
        try:
            if self.headers.get_all("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) > 1:
                raise HTTPError(400, "invalid_body_framing")
            length = self.headers.get("Content-Length", "0")
            if not re.fullmatch(r"[0-9]{1,10}", length):
                raise HTTPError(400, "invalid_body_length")
            length = int(length)
            limit = app.config.max_body_bytes if self.command == "PUT" and self.path.startswith("/v1/uploads/") else app.config.max_json_bytes
            if length > limit:
                raise HTTPError(413, "body_too_large")
            if self.command == "GET" and length:
                raise HTTPError(400, "unexpected_body")
            body = self.rfile.read(length)
            if self._deadline_expired:
                raise HTTPError(408, "request_timeout")
            if len(body) != length:
                raise HTTPError(400, "incomplete_body")
            self._receive_deadline.cancel()
            headers = self.headers.get_all("Authorization", [])
            authorization = headers[0] if len(headers) == 1 else None
            status, response, content_type = app.handle(self.command, self.path, authorization, body)
        except HTTPError as error:
            status, response, content_type = error.status, {"error": error.code}, "application/json"
        except (TimeoutError, socket.timeout):
            status, response, content_type = 408, {"error": "request_timeout"}, "application/json"
        except Exception:
            status, response, content_type = 500, {"error": "internal_error"}, "application/json"
        payload = response if isinstance(response, bytes) else json.dumps(response, default=str, separators=(",", ":")).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        self.close_connection = True

    do_GET = do_POST = do_PUT = do_DELETE = _request


def local_http_server(application, *, host="127.0.0.1", port=0):
    """Construct an unstarted server, refusing any non-loopback listener."""
    if not ipaddress.ip_address(host).is_loopback or ":" in host:
        raise ValueError("G3A HTTP boundary is IPv4 loopback only")
    return _BoundedHTTPServer((host, port), application)
