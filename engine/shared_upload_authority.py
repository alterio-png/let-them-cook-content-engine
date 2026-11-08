"""Shared PostgreSQL upload authority; no local ledger or cloud provisioning.

PostgreSQL owns quota, immutable requests, leases and receipts. Storage writes
are immutable and happen only after a committed claim. A previously claimed
upload with an uncertain outcome retains its quota: an absent object is not
proof that a paused writer cannot still publish it. Reconciliation verifies
actual stored bytes before committing a receipt, without promoting media to a
validated G2 asset. All database operations use the caller's current membership.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import time
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from .object_store import ObjectMissing
from .upload_authority import CapabilityError, UploadAlreadyUsed, UploadQuotaExceeded


class UploadBusy(UploadAlreadyUsed):
    """A current lease or a newer fence prevents this publication."""


def _uuid(value):
    try:
        value = str(value)
        if str(UUID(value)) != value:
            raise ValueError
        return value
    except (ValueError, TypeError, AttributeError) as error:
        raise CapabilityError("canonical UUID required") from error


def _encode(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _digest_file(path, *, maximum):
    """Read actual regular-file bytes with a bounded, no-follow descriptor."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise CapabilityError("upload must be a regular file")
        size, digest = 0, hashlib.sha256()
        while chunk := os.read(fd, min(1024 * 1024, maximum + 1 - size)):
            size += len(chunk)
            if size > maximum:
                raise CapabilityError("upload exceeds bounded size")
            digest.update(chunk)
        return size, digest.hexdigest()
    finally:
        os.close(fd)


class _Lease:
    """Renew a committed claim during bounded storage I/O, on separate txns."""

    def __init__(self, authority, user, grant):
        self.authority, self.user, self.grant = authority, user, grant
        self.lease_seconds = grant.get("lease_seconds")
        if type(self.lease_seconds) is not int or self.lease_seconds <= 0:
            raise UploadBusy("database upload lease is no longer usable")
        database_now = datetime.fromisoformat(grant["database_now"])
        if database_now.tzinfo is None:
            raise UploadBusy("database upload clock is not timezone aware")
        self.database_deadline = database_now + timedelta(seconds=authority.operation_timeout_seconds)
        self.stop = threading.Event()
        self.lost = threading.Event()
        self.deadline = time.monotonic() + authority.operation_timeout_seconds
        self.thread = threading.Thread(target=self._run, name="upload-lease", daemon=True)

    def _run(self):
        interval = max(0.1, min(5, self.lease_seconds / 3, self.authority.operation_timeout_seconds / 3))
        while not self.stop.wait(min(interval, max(0, self.deadline - time.monotonic()))):
            if time.monotonic() >= self.deadline:
                self.lost.set()
                return
            try:
                valid = self.authority._call(self.user, "heartbeat_upload_before",
                                             self.grant["organization_id"], self.grant["id"],
                                             self.grant["lease_token"], self.grant["fence"],
                                             self.authority.lease_seconds, self.database_deadline)
                if not valid:
                    self.lost.set()
                    return
            except Exception:
                # A failed heartbeat never grants permission to commit. The
                # caller must recover through the next fenced SQL claim.
                self.lost.set()
                return

    def __enter__(self):
        self.thread.start()
        return self

    def check(self):
        if self.lost.is_set() or time.monotonic() >= self.deadline:
            raise UploadBusy("upload lease lost; reconcile before retrying")

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=8)


class PostgresUploadAuthority:
    """Restricted web-principal adapter for the additive G3A.1 SQL contract.

    ``max_upload_bytes`` and TTL are only replica-side caps. They cannot raise
    organization limits provisioned by a trusted administrator in PostgreSQL.
    ``receipt`` is a database view; HTTP callers must use ``verified_receipt``
    or ``reconcile`` to verify that a completed receipt still has its bytes.
    Lease renewal and SQL completion have a deadline derived from the database
    clock. SQL rechecks it after admission locks and before receipt insertion;
    its statement timeout also bounds time spent inside completion. A process
    supervisor is still needed for blocked kernel/provider I/O. Commit/fsync
    acknowledgement is not a hard real-time deadline guarantee.
    Fault hooks receive a checkpoint and non-secret identifiers, and are meant
    only for isolated process-kill tests. No exceptions inheriting directly
    from BaseException are intercepted.
    """

    backend = "postgresql"

    def __init__(self, dsn, signing_key: bytes, *, max_upload_bytes=1048576,
                 max_db_connections=4, lease_seconds=30, ttl_seconds=120,
                 operation_timeout_seconds=30, fault_hook=None):
        if not isinstance(signing_key, bytes) or len(signing_key) < 32:
            raise ValueError("a server-owned signing key of at least 32 bytes is required")
        if (type(max_upload_bytes) is not int or max_upload_bytes <= 0
                or type(max_db_connections) is not int or not 1 <= max_db_connections <= 64
                or type(lease_seconds) is not int or not 1 <= lease_seconds <= 300
                or type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 300
                or type(operation_timeout_seconds) is not int or not 1 <= operation_timeout_seconds <= 300
                or fault_hook is not None and not callable(fault_hook)):
            raise ValueError("positive bounded upload, connection, lease and TTL limits required")
        self.dsn, self.signing_key = dsn, signing_key
        self.max_upload_bytes, self.max_db_connections = max_upload_bytes, max_db_connections
        self.lease_seconds, self.ttl_seconds, self.fault_hook = lease_seconds, ttl_seconds, fault_hook
        self.operation_timeout_seconds = operation_timeout_seconds
        self.clock = time.time  # Download capability compatibility; upload time comes from PostgreSQL.
        self._db_slots = threading.BoundedSemaphore(max_db_connections)
        self._validate_principal()

    def _validate_principal(self):
        with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=3) as db:
            db.execute("SET LOCAL statement_timeout='5s'")
            principal = db.execute("SELECT oid,rolsuper,rolbypassrls,rolcreaterole,rolcreatedb,"
                                   "rolreplication,rolinherit FROM pg_roles WHERE rolname=session_user").fetchone()
            ownership = db.execute("""SELECT EXISTS(
                SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE c.relowner IN (%s,'authenticated'::regrole)
                AND n.nspname IN ('public','auth','g3a_private','g3a1_private')
                UNION ALL SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                WHERE p.proowner IN (%s,'authenticated'::regrole)
                AND n.nspname IN ('public','auth','g3a_private','g3a1_private')
                UNION ALL SELECT 1 FROM pg_namespace n
                WHERE n.nspowner IN (%s,'authenticated'::regrole)
                AND n.nspname IN ('public','auth','g3a_private','g3a1_private')) AS unsafe""",
                                   (principal["oid"],) * 3).fetchone()["unsafe"]
            roles = db.execute("""WITH RECURSIVE member(roleid) AS (
                SELECT roleid FROM pg_auth_members WHERE member=%s
                UNION SELECT m.roleid FROM pg_auth_members m JOIN member r ON r.roleid=m.member)
                SELECT rolname FROM pg_roles WHERE oid IN (SELECT roleid FROM member)""",
                               (principal["oid"],)).fetchall()
            names = {row["rolname"] for row in roles}
            if (any(principal[key] for key in ("rolsuper", "rolbypassrls", "rolcreaterole",
                                               "rolcreatedb", "rolreplication", "rolinherit"))
                    or ownership or "authenticated" not in names
                    or not names <= {"authenticated", "g3a_session_reader"}):
                raise ValueError("shared upload authority requires a non-owner NOINHERIT authenticated login")
            db.execute("SET LOCAL ROLE authenticated")
            role = db.execute("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user").fetchone()
            if role["rolsuper"] or role["rolbypassrls"]:
                raise ValueError("authenticated role must enforce RLS")

    @contextmanager
    def _connection(self, user_id):
        if not self._db_slots.acquire(timeout=0.2):
            raise psycopg.OperationalError("upload database connection capacity exhausted")
        try:
            with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=3) as db:
                db.execute("SET LOCAL statement_timeout='5s'")
                db.execute("SET LOCAL lock_timeout='2s'")
                db.execute("SET LOCAL idle_in_transaction_session_timeout='5s'")
                db.execute("SET LOCAL ROLE authenticated")
                db.execute("SELECT set_config('request.jwt.claim.sub',%s,true)", (_uuid(user_id),))
                db.execute("SELECT set_config('request.jwt.claims',%s,true)",
                           (json.dumps({"sub": _uuid(user_id), "role": "authenticated"}),))
                yield db
        finally:
            self._db_slots.release()

    def _call(self, user_id, function, *arguments):
        # Every name is supplied by this module, never by an HTTP request.
        if function not in {"reserve_upload", "claim_upload", "complete_upload", "heartbeat_upload",
                            "complete_upload_before", "heartbeat_upload_before",
                            "mark_upload_uncertain", "get_upload", "reconcile_own_upload",
                            "expire_unused_uploads", "upload_metrics"}:
            raise ValueError("unknown shared upload operation")
        try:
            with self._connection(user_id) as db:
                if function in {"complete_upload_before", "heartbeat_upload_before"}:
                    remaining = db.execute("SELECT floor(extract(epoch FROM (%s::timestamptz-"
                                           "clock_timestamp()))*1000)::bigint AS ms",
                                           (arguments[-1],)).fetchone()["ms"]
                    if remaining <= 0:
                        raise UploadBusy("upload operation deadline expired")
                    db.execute("SELECT set_config('statement_timeout',%s,true)", (str(min(5000, remaining)),))
                result = db.execute(sql.SQL("SELECT {}.{}({}) AS value").format(
                    sql.Identifier("g3a1_private"), sql.Identifier(function),
                    sql.SQL(",").join(sql.Placeholder() for _ in arguments)), arguments).fetchone()["value"]
            return result
        except psycopg.Error as error:
            if error.sqlstate == "54000":
                raise UploadQuotaExceeded("organization upload quota exceeded") from error
            if error.sqlstate in {"55P03", "40001"}:
                raise UploadBusy("upload is leased or a newer fence superseded this attempt") from error
            if error.sqlstate == "57014" and function in {"complete_upload_before", "heartbeat_upload_before"}:
                raise UploadBusy("upload SQL completion deadline exceeded") from error
            if error.sqlstate == "23505":
                raise UploadAlreadyUsed("upload request key conflicts with its immutable payload") from error
            if error.sqlstate in {"22023", "23514", "P0002"}:
                raise CapabilityError("upload authorization or request rejected") from error
            raise

    def sign(self, claims):
        payload = _encode(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode())
        signature = _encode(hmac.digest(self.signing_key, payload.encode(), "sha256"))
        return payload + "." + signature

    def verify(self, token, *, kind, user_id):
        try:
            if not isinstance(token, str) or len(token) > 4096:
                raise ValueError
            payload, signature = token.split(".")
            if not hmac.compare_digest(_encode(hmac.digest(self.signing_key, payload.encode(), "sha256")), signature):
                raise ValueError
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            if (not isinstance(claims, dict) or claims["kind"] != kind
                    or claims["user_id"] != _uuid(user_id)
                    or claims["verb"] != {"upload": "PUT", "download": "GET"}[kind]):
                raise ValueError
            _uuid(claims["organization_id"])
            if kind == "upload":
                _uuid(claims["id"])
                if (claims["key"] != f"{claims['organization_id']}/uploads/{claims['id']}/unvalidated.bin"
                        or type(claims["size_bytes"]) is not int
                        or not 0 < claims["size_bytes"] <= self.max_upload_bytes
                        or not isinstance(claims["sha256"], str)
                        or not re.fullmatch("[a-f0-9]{64}", claims["sha256"])):
                    raise ValueError
                with self._connection(user_id) as db:
                    now = db.execute("SELECT extract(epoch FROM clock_timestamp()) AS now").fetchone()["now"]
            else:
                now = self.clock()
            if type(claims["expires_at"]) is not int or claims["expires_at"] <= now:
                raise ValueError
            return claims
        except (ValueError, TypeError, KeyError, UnicodeError) as error:
            raise CapabilityError("invalid or expired private authorization") from error

    def reserve(self, org_id, user_id, *, request_key, size_bytes, sha256):
        org_id, user_id = _uuid(org_id), _uuid(user_id)
        if type(size_bytes) is not int or not 0 < size_bytes <= self.max_upload_bytes:
            raise CapabilityError("invalid bounded upload size")
        if not isinstance(sha256, str) or not re.fullmatch("[a-f0-9]{64}", sha256):
            raise CapabilityError("SHA-256 required")
        if not isinstance(request_key, str) or not re.fullmatch("[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", request_key):
            raise CapabilityError("bounded request_key required")
        grant = self._call(user_id, "reserve_upload", org_id, request_key, size_bytes, sha256, self.ttl_seconds)
        if grant["state"] == "expired":
            raise CapabilityError("an expired unused request key cannot be renewed")
        claims = {"kind": "upload", "id": grant["id"], "organization_id": org_id,
                  "user_id": user_id, "key": grant["object_key"], "size_bytes": grant["size_bytes"],
                  "sha256": grant["sha256"], "expires_at": grant["expires_at"], "verb": "PUT"}
        return {"id": grant["id"], "authorization": self.sign(claims),
                "expires_at": claims["expires_at"], "size_bytes": grant["size_bytes"],
                "state": self._view(grant)["state"], "method": "PUT"}

    @staticmethod
    def _view(grant):
        if grant is None:
            return None
        if grant.get("receipt") is not None:
            return grant["receipt"]
        return {"id": grant["id"], "state": "awaiting_upload" if grant["state"] == "reserved" else grant["state"],
                "size_bytes": grant["size_bytes"], "sha256": grant["sha256"], "expires_at": grant["expires_at"]}

    @staticmethod
    def _match(grant, claims):
        for field, claim in (("id", "id"), ("organization_id", "organization_id"),
                             ("owner_user_id", "user_id"), ("object_key", "key"),
                             ("size_bytes", "size_bytes"), ("sha256", "sha256"), ("expires_at", "expires_at")):
            if grant[field] != claims[claim]:
                raise CapabilityError("upload authorization differs from immutable reservation")

    def _checkpoint(self, name, grant):
        if self.fault_hook is not None:
            self.fault_hook(name, {"grant_id": grant["id"], "org_id": grant["organization_id"],
                                   "fence": grant.get("fence")})

    @contextmanager
    def _source_copy(self, source, claims):
        # Freeze exactly the verified bytes before claiming, preventing a caller
        # from swapping their source between checksum and storage publication.
        with tempfile.TemporaryDirectory(prefix="g3a1-source-") as directory:
            destination = Path(directory) / "source.bin"
            source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
            try:
                if not stat.S_ISREG(os.fstat(source_fd).st_mode):
                    raise CapabilityError("upload must be a regular file")
                target_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
                try:
                    size, digest = 0, hashlib.sha256()
                    while chunk := os.read(source_fd, min(1024 * 1024, claims["size_bytes"] + 1 - size)):
                        size += len(chunk)
                        if size > claims["size_bytes"]:
                            raise CapabilityError("upload actual size mismatch")
                        digest.update(chunk)
                        with memoryview(chunk) as remaining:
                            while remaining:
                                count = os.write(target_fd, remaining)
                                remaining = remaining[count:]
                    if size != claims["size_bytes"] or digest.hexdigest() != claims["sha256"]:
                        raise CapabilityError("upload actual size or SHA-256 mismatch")
                finally:
                    os.close(target_fd)
            finally:
                os.close(source_fd)
            yield destination

    def _read_stored(self, grant, store):
        with tempfile.TemporaryDirectory(prefix="g3a1-verify-") as directory:
            destination = Path(directory) / "stored.bin"
            stored = store.get_file(grant["organization_id"], grant["object_key"], destination,
                                    max_bytes=grant["size_bytes"])
            size, digest = _digest_file(destination, maximum=grant["size_bytes"])
        if (size != grant["size_bytes"] or digest != grant["sha256"]
                or stored.get("key") != grant["object_key"]
                or stored.get("size_bytes") != size or stored.get("sha256") != digest):
            raise CapabilityError("actual stored upload bytes do not match immutable reservation")
        expected_generation = (grant.get("receipt") or {}).get("generation")
        if expected_generation is not None and stored.get("generation") != expected_generation:
            raise CapabilityError("stored object generation differs from immutable receipt")
        return stored

    def _mark_uncertain(self, user_id, grant):
        try:
            self._call(user_id, "mark_upload_uncertain", grant["organization_id"], grant["id"],
                       grant["lease_token"], grant["fence"], "STORAGE_OR_COMMIT_UNCERTAIN")
        except Exception:
            # Database outage/revocation/stale fence must not mask the original
            # failure or release quota; an expired lease remains reconcilable.
            pass

    def publish(self, token, user_id, source: Path, store):
        user_id = _uuid(user_id)
        claims = self.verify(token, kind="upload", user_id=user_id)
        with self._source_copy(source, claims) as frozen_source:
            self._checkpoint("before_claim", {"id": claims["id"], "organization_id": claims["organization_id"]})
            grant = self._call(user_id, "claim_upload", claims["organization_id"], claims["id"], self.lease_seconds)
            self._match(grant, claims)
            if grant.get("receipt") is not None:
                self._read_stored(grant, store)
                return grant["receipt"]
            return self._publish_claim(user_id, grant, frozen_source, store)

    def _publish_claim(self, user_id, grant, frozen_source, store):
        try:
            with _Lease(self, user_id, grant) as lease:
                self._checkpoint("after_claim", grant)
                lease.check()
                self._checkpoint("before_store", grant)
                lease.check()
                stored = store.put_file(grant["organization_id"], grant["object_key"], frozen_source)
                self._checkpoint("after_store", grant)
                verified = self._read_stored(grant, store)
                if (stored.get("key") != grant["object_key"]
                        or stored.get("size_bytes") != grant["size_bytes"]
                        or stored.get("sha256") != grant["sha256"]
                        or stored.get("generation") is not None
                        and stored.get("generation") != verified.get("generation")):
                    raise CapabilityError("storage publication receipt mismatch")
                lease.check()
                self._checkpoint("before_commit", grant)
                lease.check()
                receipt = self._call(user_id, "complete_upload_before", grant["organization_id"], grant["id"],
                                     grant["lease_token"], grant["fence"], grant["object_key"],
                                     verified["size_bytes"], verified["sha256"], verified.get("generation"),
                                     lease.database_deadline)
                self._checkpoint("after_commit", grant)
                return receipt
        except Exception:
            self._mark_uncertain(user_id, grant)
            raise

    def resume(self, org_id, user_id, grant_id, source: Path, store):
        """Fresh authenticated retry of an admitted grant, even after its TTL.

        This explicit operation does not renew or resurrect an unused expired
        reservation. It can only finish the immutable payload of an earlier
        durable publication claim; quota is never charged a second time.
        """
        org_id, user_id, grant_id = _uuid(org_id), _uuid(user_id), _uuid(grant_id)
        original = self._call(user_id, "get_upload", org_id, grant_id)
        if original is None:
            raise CapabilityError("upload reservation not found")
        claims = {"size_bytes": original["size_bytes"], "sha256": original["sha256"]}
        with self._source_copy(source, claims) as frozen_source:
            if original.get("receipt") is not None:
                self._read_stored(original, store)
                return original["receipt"]
            self._checkpoint("before_claim", original)
            grant = self._call(user_id, "reconcile_own_upload", org_id, grant_id, self.lease_seconds)
            if grant is not None and grant.get("receipt") is not None:
                self._read_stored(grant, store)
                return grant["receipt"]
            if grant is None or grant["state"] in {"reserved", "expired"} or grant.get("lease_token") is None:
                raise CapabilityError("only an earlier admitted upload can be resumed")
            return self._publish_claim(user_id, grant, frozen_source, store)

    def receipt(self, org_id, user_id, grant_id):
        """Current-member, current-owner database metadata; no storage assertion."""
        return self._view(self._call(_uuid(user_id), "get_upload", _uuid(org_id), _uuid(grant_id)))

    def verified_receipt(self, org_id, user_id, grant_id, store):
        return self.reconcile(org_id, user_id, grant_id, store)

    def reconcile(self, org_id, user_id, grant_id, store):
        """Recover own expired claim; absence never refunds a previously claimed grant."""
        org_id, user_id, grant_id = _uuid(org_id), _uuid(user_id), _uuid(grant_id)
        grant = self._call(user_id, "get_upload", org_id, grant_id)
        if grant is None:
            return None
        if grant.get("receipt") is not None:
            self._read_stored(grant, store)
            return grant["receipt"]
        grant = self._call(user_id, "reconcile_own_upload", org_id, grant_id, self.lease_seconds)
        if grant is not None and grant.get("receipt") is not None:
            self._read_stored(grant, store)
            return grant["receipt"]
        if grant is None or grant.get("lease_token") is None or grant["state"] in {"reserved", "expired"}:
            return self._view(grant)
        try:
            with _Lease(self, user_id, grant) as lease:
                try:
                    verified = self._read_stored(grant, store)
                except ObjectMissing:
                    self._mark_uncertain(user_id, grant)
                    grant["state"] = "uncertain"
                    return self._view(grant)
                lease.check()
                return self._call(user_id, "complete_upload_before", org_id, grant_id,
                                  grant["lease_token"], grant["fence"], grant["object_key"],
                                  verified["size_bytes"], verified["sha256"], verified.get("generation"),
                                  lease.database_deadline)
        except Exception:
            self._mark_uncertain(user_id, grant)
            raise

    def expire_unused(self, org_id, user_id, *, limit=64):
        if type(limit) is not int or not 1 <= limit <= 256:
            raise ValueError("reconciliation must use a bounded batch")
        return self._call(_uuid(user_id), "expire_unused_uploads", _uuid(org_id), limit)

    def reconcile_expired(self, store, *, org_id=None, user_id=None, limit=64):
        # Intentionally no broad legacy sweep or filesystem GC. Own grants are
        # reconciled individually, and operator service claims use a separate role.
        if org_id is None or user_id is None:
            raise ValueError("shared reconciliation requires an organization and verified user")
        return self.expire_unused(org_id, user_id, limit=limit)

    def metrics(self, org_id, user_id):
        return self._call(_uuid(user_id), "upload_metrics", _uuid(org_id))
