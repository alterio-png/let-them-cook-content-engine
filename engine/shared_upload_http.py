"""Additive G3A.1 HTTP boundary backed exclusively by shared PostgreSQL.

Historical G3A render/download routes and bounded loopback transport are reused
unchanged. Upload admission and every publication/receipt decision use the
restricted API principal and short transactions in PostgresUploadAuthority.
No SQLite fallback, media promotion, FFmpeg execution or cloud deployment is
part of this boundary.
"""
from __future__ import annotations

from pathlib import Path
import json
import re
import socket
import tempfile

import psycopg

from .cloud_auth import AuthError
from .cloud_http import HTTPError, MediaHTTPApplication, _Handler, _json, _uuid, local_http_server
from .object_store import ObjectStoreError
from .shared_upload_authority import PostgresUploadAuthority
from .upload_authority import CapabilityError, UploadAlreadyUsed, UploadQuotaExceeded


class SharedMediaHTTPApplication(MediaHTTPApplication):
    """Shared upload authority; use ``local_http_server`` for local execution.

    The authority must use this application's unprivileged API DSN. An outer
    membership transaction is deliberately not retained across object I/O:
    claim and receipt commit independently recheck membership under SQL locks.
    A revocation can therefore deny completion without deadlocking a nested
    transaction, and the claimed allocation remains charged for reconciliation.
    """

    def __init__(self, *, dsn, verifier, store, authority, config=None):
        if not isinstance(authority, PostgresUploadAuthority):
            raise ValueError("shared HTTP requires PostgresUploadAuthority")
        if authority.dsn != dsn:
            raise ValueError("shared upload authority must use the restricted HTTP API principal")
        super().__init__(dsn=dsn, verifier=verifier, store=store, authority=authority, config=config)
        if authority.max_db_connections > self.config.max_db_connections:
            raise ValueError("shared upload database connections exceed HTTP limit")

    def handle(self, method, path, authorization, body=b""):
        # Delegate unrelated behavior to the unchanged G3A implementation.
        upload_path = isinstance(path, str) and (
            path.startswith("/v1/uploads/") or re.match(r"/v1/orgs/[^/]+/uploads(?:/|$)", path)
        )
        if not upload_path:
            return super().handle(method, path, authorization, body)
        try:
            if not isinstance(authorization, str) or not authorization.startswith("Bearer ") or authorization.count(" ") != 1:
                raise HTTPError(401, "authentication_required")
            identity = self.verifier.verify(authorization[7:])
            if len(path) > 8192 or any(c in path for c in ("?", "#", "%")):
                raise HTTPError(400, "invalid_path")
            capability = re.fullmatch(r"/v1/uploads/([A-Za-z0-9_.-]+)", path)
            if capability:
                if method != "PUT":
                    raise HTTPError(405, "method_not_allowed")
                if len(body) > self.config.max_body_bytes:
                    raise HTTPError(413, "body_too_large")
                # Verify before buffering bytes to the private temporary file.
                self.authority.verify(capability[1], kind="upload", user_id=identity.user_id)
                with tempfile.TemporaryDirectory(prefix="ltc-private-shared-upload-") as name:
                    source = Path(name) / "unvalidated.bin"
                    source.write_bytes(body)
                    source.chmod(0o600)
                    receipt = self.authority.publish(capability[1], identity.user_id, source, self.store)
                return 201, receipt, "application/json"

            match = re.fullmatch(r"/v1/orgs/([^/]+)/uploads(?:/([^/]+))?(?:/(reconcile|resume))?", path)
            if not match:
                raise HTTPError(404, "route_not_found")
            org, grant_id, action = match.groups()
            org = _uuid(org)
            if action == "resume" and grant_id and method == "POST":
                if len(body) > self.config.max_body_bytes:
                    raise HTTPError(413, "body_too_large")
                with tempfile.TemporaryDirectory(prefix="ltc-private-shared-resume-") as name:
                    source = Path(name) / "unvalidated.bin"
                    source.write_bytes(body)
                    source.chmod(0o600)
                    result = self.authority.resume(org, identity.user_id, _uuid(grant_id), source, self.store)
                return 201, result, "application/json"
            if len(body) > self.config.max_json_bytes:
                raise HTTPError(413, "body_too_large")
            if not grant_id and method == "POST":
                request = _json(body, ("request_key", "size_bytes", "sha256"))
                grant = self.authority.reserve(org, identity.user_id, **request)
                result = dict(grant)
                result["upload_path"] = "/v1/uploads/" + result.pop("authorization")
                return 201, result, "application/json"
            if grant_id == "metrics" and not action and method == "GET":
                return 200, self.authority.metrics(org, identity.user_id), "application/json"
            if grant_id and ((method == "GET" and not action) or (method == "POST" and action == "reconcile")):
                if body:
                    raise HTTPError(400, "invalid_request")
                result = self.authority.reconcile(org, identity.user_id, _uuid(grant_id), self.store)
                if result is None:
                    raise HTTPError(404, "upload_receipt_not_found")
                return 200, result, "application/json"
            raise HTTPError(405, "method_not_allowed")
        except AuthError:
            return 401, {"error": "invalid_or_revoked_session"}, "application/json"
        except HTTPError as error:
            return error.status, {"error": error.code}, "application/json"
        except UploadQuotaExceeded:
            return 429, {"error": "organization_upload_quota"}, "application/json"
        except UploadAlreadyUsed:
            return 409, {"error": "upload_conflict_or_busy"}, "application/json"
        except CapabilityError:
            return 400, {"error": "invalid_private_authorization_or_bytes"}, "application/json"
        except ObjectStoreError:
            return 409, {"error": "private_storage_integrity_failure"}, "application/json"
        except psycopg.errors.InsufficientPrivilege:
            return 403, {"error": "organization_access_denied"}, "application/json"
        except psycopg.Error as error:
            rejected = error.sqlstate in {"P0001", "23505", "23514", "22023"}
            return (409 if rejected else 503), {"error": "upload_conflict_or_rejected" if rejected else "database_unavailable"}, "application/json"


class _SharedHandler(_Handler):
    def _request(self):
        # The historical handler remains responsible for every existing route.
        # Only the new authenticated binary-resume route needs the upload body
        # cap instead of the smaller JSON cap; all transport limits still apply.
        if self.command != "POST" or not re.fullmatch(r"/v1/orgs/[^/]+/uploads/[^/]+/resume", self.path):
            return super()._request()
        app = self.server.application
        try:
            if self.headers.get_all("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) > 1:
                raise HTTPError(400, "invalid_body_framing")
            length = self.headers.get("Content-Length", "0")
            if not re.fullmatch(r"[0-9]{1,10}", length):
                raise HTTPError(400, "invalid_body_length")
            length = int(length)
            if length > app.config.max_body_bytes:
                raise HTTPError(413, "body_too_large")
            body = self.rfile.read(length)
            if self._deadline_expired:
                raise HTTPError(408, "request_timeout")
            if len(body) != length:
                raise HTTPError(400, "incomplete_body")
            self._receive_deadline.cancel()
            headers = self.headers.get_all("Authorization", [])
            status, response, content_type = app.handle(self.command, self.path, headers[0] if len(headers) == 1 else None, body)
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


def shared_http_server(application, *, host="127.0.0.1", port=0):
    """Loopback-only bounded G3A transport with the additive binary-resume cap."""
    if not isinstance(application, SharedMediaHTTPApplication):
        raise ValueError("shared HTTP transport requires the shared PostgreSQL application")
    server = local_http_server(application, host=host, port=port)
    server.RequestHandlerClass = _SharedHandler
    return server
