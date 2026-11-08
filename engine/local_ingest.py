"""Local trusted byte validation between shared upload receipts and G2 assets.

Only a NOINHERIT login with the execute-only g3br0_ingester role is accepted.
The browser supplies receipt IDs, never media metadata, object paths or rights
approval. PostgreSQL owns leases/fences and the synthetic approval registry.
Private objects from interrupted attempts are retained and reused by stable
asset identity; neither quota refund nor deletion is part of this worker.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .control_plane import StaleLease
from .media_runtime import Limits, MediaError, inspect_media, validate_srt
from .object_store import ObjectConflict, ObjectLimitExceeded, _external_directory, _private


class IngestIntegrityError(ValueError):
    """Readback bytes do not match the immutable shared receipt."""


class LocalIngestWorker:
    MAX_BYTES = 1048576
    MAX_CAPTION_BYTES = 65536

    def __init__(self, dsn, store, scratch_root=None, *, lease_seconds=30,
                 fault_hook=None, metrics=None):
        if (type(lease_seconds) is not int or not 1 <= lease_seconds <= 30
                or fault_hook is not None and not callable(fault_hook)):
            raise ValueError("ingestion lease must be an integer in [1,30]")
        self.dsn, self.store = dsn, store
        self.scratch_root = Path(scratch_root).absolute() if scratch_root else None
        self.lease_seconds, self.fault_hook, self.metrics = lease_seconds, fault_hook, metrics
        if self.scratch_root:
            with _external_directory(self.scratch_root, create=True) as fd:
                _private(os.fstat(fd), directory=True)
        self._validate_principal()

    def _validate_principal(self):
        with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=3) as db:
            db.execute("SET LOCAL statement_timeout='5s'")
            principal = db.execute("""SELECT oid,rolsuper,rolbypassrls,rolcreaterole,
                rolcreatedb,rolreplication,rolinherit FROM pg_roles WHERE rolname=session_user""").fetchone()
            names = {row["rolname"] for row in db.execute("""WITH RECURSIVE member(roleid) AS (
                SELECT roleid FROM pg_auth_members WHERE member=%s
                UNION SELECT m.roleid FROM pg_auth_members m JOIN member r ON r.roleid=m.member)
                SELECT rolname FROM pg_roles WHERE oid IN (SELECT roleid FROM member)""", (principal["oid"],))}
            owned = db.execute("""SELECT EXISTS(
                SELECT 1 FROM pg_class WHERE relowner IN (%s,'g3br0_ingester'::regrole) AND relnamespace NOT IN
                    ('pg_catalog'::regnamespace,'information_schema'::regnamespace)
                UNION ALL SELECT 1 FROM pg_proc WHERE proowner IN (%s,'g3br0_ingester'::regrole) AND pronamespace NOT IN
                    ('pg_catalog'::regnamespace,'information_schema'::regnamespace)
                UNION ALL SELECT 1 FROM pg_namespace WHERE nspowner IN (%s,'g3br0_ingester'::regrole)) AS unsafe""",
                               (principal["oid"],) * 3).fetchone()["unsafe"]
            writable = db.execute("""SELECT EXISTS(
                SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname IN ('public','auth','g3a_private','g3a1_private','g3br0_private')
                AND c.relkind IN ('r','p') AND EXISTS (
                 SELECT 1 FROM (VALUES(session_user::text),('g3br0_ingester')) AS principal(name)
                 WHERE has_any_column_privilege(principal.name,c.oid,'INSERT') OR
                  has_any_column_privilege(principal.name,c.oid,'UPDATE') OR
                  has_table_privilege(principal.name,c.oid,'DELETE') OR
                  has_table_privilege(principal.name,c.oid,'TRUNCATE'))) AS unsafe""").fetchone()["unsafe"]
            if (any(principal[key] for key in ("rolsuper", "rolbypassrls", "rolcreaterole",
                                               "rolcreatedb", "rolreplication", "rolinherit"))
                    or owned or writable or names != {"g3br0_ingester"}):
                raise ValueError("ingestion requires an execute-only non-owner NOINHERIT login")
            db.execute("SET LOCAL ROLE g3br0_ingester")
            role = db.execute("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user").fetchone()
            if role["rolsuper"] or role["rolbypassrls"]:
                raise ValueError("ingester role must not bypass RLS")

    @contextmanager
    def _connection(self):
        with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=3) as db:
            db.execute("SET LOCAL ROLE g3br0_ingester")
            db.execute("SET LOCAL statement_timeout='5s'")
            db.execute("SET LOCAL lock_timeout='3s'")
            yield db

    def _call(self, function, values):
        # Every caller below supplies a static internal function name.
        from psycopg import sql
        query = sql.SQL("SELECT g3br0_private.{}({}) AS value").format(
            sql.Identifier(function), sql.SQL(",").join(sql.Placeholder() for _ in values))
        with self._connection() as db:
            return db.execute(query, values).fetchone()["value"]

    def _checkpoint(self, stage, claim):
        if self.fault_hook:
            self.fault_hook(stage, claim)

    @staticmethod
    def _identity(claim):
        return (claim["id"], claim["lease_token"], claim["fence"])

    def _readback(self, org, receipt, destination, cap):
        size = receipt["size_bytes"]
        if type(size) is not int or not 0 < size <= cap:
            raise IngestIntegrityError("receipt exceeds the existing object cap")
        metadata = self.store.get_file(org, receipt["object_key"], destination, max_bytes=size)
        actual_size = destination.stat().st_size
        # Bound actual bytes independently of the ObjectStore response.
        with destination.open("rb") as handle:
            actual = handle.read(size + 1)
        actual_hash = hashlib.sha256(actual).hexdigest()
        if (actual_size != size or len(actual) != size or actual_hash != receipt["sha256"]
                or metadata.get("key") != receipt["object_key"]
                or metadata.get("sha256") != actual_hash or metadata.get("size_bytes") != size
                or receipt.get("generation") is not None
                and metadata.get("generation") != receipt["generation"]):
            raise IngestIntegrityError("object bytes or generation differ from shared receipt")
        return metadata

    def run_once(self):
        claim = self._call("claim_ingest", (self.lease_seconds,))
        if claim is None:
            return None
        started = time.monotonic()
        self._checkpoint("after_claim", claim)
        stop = threading.Event()
        lost = threading.Event()

        def renew():
            while not stop.wait(max(0.1, self.lease_seconds / 3)):
                try:
                    live = self._call("heartbeat_ingest", (*self._identity(claim), self.lease_seconds))
                except Exception:
                    live = False
                if not live:
                    lost.set()
                    return

        def heartbeat():
            if lost.is_set() or time.monotonic() - started >= 30:
                raise StaleLease("ingestion lease or operation deadline expired")

        thread = threading.Thread(target=renew, name="local-ingest-lease", daemon=True)
        thread.start()
        try:
            if claim["approval"] is None:
                return self._call("fail_ingest", (*self._identity(claim), "synthetic_not_approved", False))
            org, asset = claim["organization_id"], claim["asset_id"]
            with tempfile.TemporaryDirectory(prefix="ltc-r0-ingest-", dir=self.scratch_root) as directory:
                scratch = Path(directory)
                os.chmod(scratch, 0o700)
                video, caption = scratch / "source.mp4", scratch / "caption.srt"
                self._readback(org, claim["video_receipt"], video, self.MAX_BYTES)
                heartbeat()
                self._readback(org, claim["caption_receipt"], caption, self.MAX_CAPTION_BYTES)
                self._checkpoint("after_readback", claim)
                limits = Limits(max_bytes=self.MAX_BYTES, timeout_seconds=30, cpu_seconds=30)
                media = inspect_media(video, limits, cancel_event=lost, heartbeat=heartbeat)
                if not media["has_audio"]:
                    raise MediaError("invalid_input", "the approved local demonstration requires audio")
                validate_srt(caption)
                heartbeat()
                source_key = f"{org}/assets/{asset}/source.mp4"
                caption_key = f"{org}/assets/{asset}/caption.srt"
                # Stable keys + immutable create make retry after partial publication safe.
                for receipt, key, path, name, cap in (
                    (claim["video_receipt"], source_key, video, "source-check.mp4", self.MAX_BYTES),
                    (claim["caption_receipt"], caption_key, caption, "caption-check.srt", self.MAX_CAPTION_BYTES),
                ):
                    stored = self.store.put_file(org, key, path)
                    if stored.get("sha256") != receipt["sha256"] or stored.get("size_bytes") != receipt["size_bytes"]:
                        raise IngestIntegrityError("asset publication differs from receipt")
                    verification_receipt = {**receipt, "object_key": key, "generation": stored.get("generation")}
                    self._readback(org, verification_receipt, scratch / name, cap)
                    heartbeat()
                self._checkpoint("after_publish", claim)
                caption_info = {"key": caption_key, "sha256": claim["caption_receipt"]["sha256"],
                                "size_bytes": claim["caption_receipt"]["size_bytes"]}
                binding = {"version": "g2-v1", "source_sha256": claim["video_receipt"]["sha256"],
                           "source_bytes": claim["video_receipt"]["size_bytes"], "caption": caption_info}
                metadata = {
                    "source_key": source_key, "source_sha256": binding["source_sha256"],
                    "source_bytes": binding["source_bytes"], "caption_key": caption_key,
                    "caption_sha256": caption_info["sha256"], "caption_bytes": caption_info["size_bytes"],
                    "video_generation": claim["video_receipt"].get("generation"),
                    "caption_generation": claim["caption_receipt"].get("generation"),
                    "approval_id": claim["approval"]["id"],
                    "duration_ms": round(media["duration_seconds"] * 1000),
                    "width": media["input_width"], "height": media["input_height"],
                    "has_audio": media["has_audio"],
                    "binding_digest": hashlib.sha256(json.dumps(binding, sort_keys=True,
                                                                 separators=(",", ":")).encode()).hexdigest(),
                }
                self._checkpoint("before_complete", claim)
                heartbeat()
                result = self._call("complete_ingest", (*self._identity(claim), Jsonb(metadata)))
                self._checkpoint("after_complete", claim)
                return result
        except StaleLease:
            return {"id": claim["id"], "state": "stale", "error_code": "stale_lease"}
        except Exception as error:
            if isinstance(error, psycopg.Error) and error.sqlstate == "55000":
                return {"id": claim["id"], "state": "stale", "error_code": "stale_lease"}
            permanent = isinstance(error, (MediaError, IngestIntegrityError, ObjectConflict, ObjectLimitExceeded))
            code = "invalid_media" if isinstance(error, MediaError) else "integrity_mismatch" if permanent else "ingest_io_uncertain"
            try:
                return self._call("fail_ingest", (*self._identity(claim), code, not permanent))
            except psycopg.Error:
                # No compensating deletion/refund after uncertain I/O or commit.
                # PostgreSQL expiry and the next fenced claimant recover it.
                return {"id": claim["id"], "state": "uncertain", "error_code": "ingest_commit_uncertain"}
        finally:
            stop.set()
            thread.join(timeout=6)
            if self.metrics is not None:
                self.metrics("ingest", "attempt", elapsed_ms=(time.monotonic() - started) * 1000)

    process_one = run_once


# Explicit name retained for the local runtime daemon's factory contract.
LocalIngester = LocalIngestWorker
