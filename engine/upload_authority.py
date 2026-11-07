"""Persistent local private upload grants; these receipts are NOT verified assets.

The server owns this database and signing key. Grants reserve cumulative tenant
storage, including successful uploads, until an operator-approved retention
policy exists. Expired unused reservations can be reconciled, under the same
SQLite write lock as publication, without losing upload-before-commit objects.
No media parser or administrator PostgreSQL connection belongs in this module.

This is a SINGLE-HOST ledger: restart recovery requires the same private volume
and signing key. Multi-replica/cloud upload quota and receipt persistence are
BLOCKED until a shared durable PostgreSQL-backed authority is implemented and
validated. Independent SQLite files do not enforce a shared organization quota.
"""
from __future__ import annotations

from contextlib import contextmanager
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from uuid import UUID, uuid4

from .object_store import ObjectMissing


class CapabilityError(ValueError):
    pass


class UploadQuotaExceeded(CapabilityError):
    pass


class UploadAlreadyUsed(CapabilityError):
    pass


def _uuid(value):
    value = str(value)
    if str(UUID(value)) != value:
        raise CapabilityError("canonical UUID required")
    return value


def _encode(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class UploadAuthority:
    def __init__(self, database: Path, signing_key: bytes, *, max_upload_bytes=1048576,
                 max_org_bytes=16 * 1048576, max_org_uploads=32, ttl_seconds=120,
                 clock=time.time):
        if not isinstance(signing_key, bytes) or len(signing_key) < 32:
            raise ValueError("a server-owned signing key of at least 32 bytes is required")
        if any(type(n) is not int or n <= 0 for n in
               (max_upload_bytes, max_org_bytes, max_org_uploads, ttl_seconds)) or ttl_seconds > 300:
            raise ValueError("positive bounded limits and TTL <=300 seconds required")
        self.database = Path(database).absolute()
        self.signing_key = signing_key
        self.max_upload_bytes = max_upload_bytes
        self.max_org_bytes = max_org_bytes
        self.max_org_uploads = max_org_uploads
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self.database.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent = self.database.parent.lstat()
        if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid() or parent.st_mode & 0o077:
            raise ValueError("upload authority requires an owned private parent directory")
        # Refuse symlinks and preexisting permissive files before SQLite opens it.
        fd = os.open(self.database, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise ValueError("upload authority requires an owned private database")
        finally:
            os.close(fd)
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS uploads(
                id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, user_id TEXT NOT NULL,
                object_key TEXT UNIQUE NOT NULL, size_bytes INTEGER NOT NULL,
                sha256 TEXT NOT NULL, expires_at INTEGER NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('pending','uploaded','expired')),
                uploaded_at INTEGER)""")
            db.execute("CREATE INDEX IF NOT EXISTS upload_org ON uploads(organization_id,state)")

    @contextmanager
    def _db(self):
        # Parent directory is server-owned; local processes sharing its UID are trusted.
        db = sqlite3.connect(self.database, timeout=5)
        try:
            with db:
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA synchronous=FULL")
                db.execute("BEGIN IMMEDIATE")
                yield db
        finally:
            db.close()

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
            if (claims["kind"] != kind or claims["user_id"] != _uuid(user_id)
                    or claims["verb"] != {"upload": "PUT", "download": "GET"}[kind]):
                raise ValueError
            if type(claims["expires_at"]) is not int or claims["expires_at"] <= self.clock():
                raise ValueError
            _uuid(claims["organization_id"])
            return claims
        except (ValueError, TypeError, KeyError, UnicodeError) as error:
            raise CapabilityError("invalid or expired private authorization") from error

    def reserve(self, org_id, user_id, *, size_bytes, sha256):
        org_id, user_id = _uuid(org_id), _uuid(user_id)
        if type(size_bytes) is not int or not 0 < size_bytes <= self.max_upload_bytes:
            raise CapabilityError("invalid bounded upload size")
        if not isinstance(sha256, str) or not re.fullmatch("[a-f0-9]{64}", sha256):
            raise CapabilityError("SHA-256 required")
        grant_id = str(uuid4())
        claims = {"kind": "upload", "id": grant_id, "organization_id": org_id,
                  "user_id": user_id, "key": f"{org_id}/uploads/{grant_id}/unvalidated.bin",
                  "size_bytes": size_bytes, "sha256": sha256,
                  "expires_at": int(self.clock()) + self.ttl_seconds, "verb": "PUT"}
        with self._db() as db:
            used = db.execute("SELECT count(*) AS n,coalesce(sum(size_bytes),0) AS bytes "
                              "FROM uploads WHERE organization_id=? AND state!='expired'", (org_id,)).fetchone()
            if used["n"] >= self.max_org_uploads or used["bytes"] + size_bytes > self.max_org_bytes:
                raise UploadQuotaExceeded("organization upload storage reservation exceeded")
            db.execute("INSERT INTO uploads VALUES(?,?,?,?,?,?,?,'pending',NULL)",
                       (grant_id, org_id, user_id, claims["key"], size_bytes, sha256, claims["expires_at"]))
        return {"id": grant_id, "authorization": self.sign(claims),
                "expires_at": claims["expires_at"], "size_bytes": size_bytes,
                "state": "awaiting_upload", "method": "PUT"}

    def publish(self, token, user_id, source: Path, store):
        claims = self.verify(token, kind="upload", user_id=user_id)
        with self._db() as db:
            row = db.execute("SELECT * FROM uploads WHERE id=?", (claims["id"],)).fetchone()
            if not row or row["state"] == "expired" or row["expires_at"] <= self.clock():
                raise CapabilityError("upload authorization expired")
            if row["state"] != "pending":
                raise UploadAlreadyUsed("upload authorization already consumed")
            for left, right in (("organization_id", "organization_id"), ("user_id", "user_id"),
                                ("object_key", "key"), ("size_bytes", "size_bytes"), ("sha256", "sha256")):
                if row[left] != claims[right]:
                    raise CapabilityError("upload authorization mismatch")
            size = source.stat().st_size
            if size != row["size_bytes"] or size > self.max_upload_bytes:
                raise CapabilityError("upload actual size mismatch")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            if size != row["size_bytes"] or digest != row["sha256"]:
                raise CapabilityError("upload actual size or SHA-256 mismatch")
            stored = store.put_file(claims["organization_id"], claims["key"], source)
            if stored["size_bytes"] != size or stored["sha256"] != digest:
                raise CapabilityError("storage receipt mismatch")
            db.execute("UPDATE uploads SET state='uploaded',uploaded_at=? WHERE id=?", (int(self.clock()), row["id"]))
        return {"id": claims["id"], "state": "uploaded_pending_validation",
                "size_bytes": size, "sha256": digest}

    def receipt(self, org_id, user_id, grant_id):
        with self._db() as db:
            row = db.execute("SELECT id,state,size_bytes,sha256,expires_at FROM uploads "
                             "WHERE id=? AND organization_id=? AND user_id=?",
                             (_uuid(grant_id), _uuid(org_id), _uuid(user_id))).fetchone()
            if not row:
                return None
            return {**dict(row), "state": "uploaded_pending_validation" if row["state"] == "uploaded" else "awaiting_upload"}

    def reconcile_expired(self, store, *, org_id=None, limit=64):
        """Recover object-before-receipt crash; reclaim only absent unused grants.

        Publication and reconciliation share the durable write lock. Uploaded
        bytes continue counting toward quotas. No media is promoted to assets.
        """
        result = {"expired_unused": 0, "recovered_uploaded": 0}
        if type(limit) is not int or not 1 <= limit <= 256:
            raise ValueError("reconciliation must use a bounded batch")
        with self._db() as db:
            if org_id is None:
                rows = db.execute("SELECT * FROM uploads WHERE state='pending' AND expires_at<=? LIMIT ?", (self.clock(), limit)).fetchall()
            else:
                rows = db.execute("SELECT * FROM uploads WHERE state='pending' AND expires_at<=? AND organization_id=? LIMIT ?",
                                  (self.clock(), _uuid(org_id), limit)).fetchall()
            for row in rows:
                try:
                    obj = store.stat(row["organization_id"], row["object_key"])
                except ObjectMissing:
                    db.execute("UPDATE uploads SET state='expired' WHERE id=?", (row["id"],))
                    result["expired_unused"] += 1
                else:
                    if (obj["sha256"], obj["size_bytes"]) != (row["sha256"], row["size_bytes"]):
                        raise CapabilityError("unvalidated upload requires operator reconciliation")
                    db.execute("UPDATE uploads SET state='uploaded',uploaded_at=? WHERE id=?", (int(self.clock()), row["id"]))
                    result["recovered_uploaded"] += 1
            # Expired, absent grants carry no usable authority and need not make
            # the private grant ledger grow indefinitely. Uploaded receipts stay.
            db.execute("DELETE FROM uploads WHERE state='expired'")
        return result
