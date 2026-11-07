"""G1 PostgreSQL control plane. No renderer, object store or cloud API is invoked.

The DSN must belong to an internal principal allowed to SET ROLE engine_worker.
End-user requests use an already authenticated connection, never this DSN. Local
tests emulate the identity gateway; this module does not verify arbitrary JWTs.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import re
import uuid

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


class StaleLease(RuntimeError):
    """The attempt no longer owns the current, unexpired database lease."""


def _lease_seconds(value):
    if type(value) is not int or not 1 <= value <= 300:
        raise ValueError("lease_seconds must be an integer in [1, 300]")
    return value


def _audit(connection, row, event_type, details=None):
    connection.execute(
        """INSERT INTO public.audit_events
           (organization_id, job_id, event_type, details) VALUES (%s,%s,%s,%s)""",
        (row["organization_id"], row.get("job_id", row["id"]),
         event_type, Jsonb(details or {})),
    )


class ControlPlane:
    def __init__(self, dsn):
        self.dsn = dsn

    @contextmanager
    def transaction(self):
        """Close every connection and commit/rollback the whole state transition."""
        with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=10) as connection:
            connection.execute("SET LOCAL ROLE engine_worker")
            connection.execute("SET LOCAL statement_timeout = '15s'")
            connection.execute("SET LOCAL lock_timeout = '10s'")
            yield connection

    @staticmethod
    def submit(connection, organization_id, request_key, source_asset_id,
               duration_ms, quality="smoke"):
        """Caller owns transaction and verified authentication context."""
        return connection.execute(
            "SELECT * FROM public.submit_render(%s,%s,%s,%s,%s)",
            (organization_id, request_key, source_asset_id, duration_ms, quality),
        ).fetchone()

    def get_job(self, job_id):
        """Internal reconciliation only; user reads must use tenant RLS."""
        with self.transaction() as connection:
            return connection.execute(
                "SELECT * FROM public.render_jobs WHERE id=%s", (job_id,)
            ).fetchone()

    def claim_next_job(self, lease_seconds=60, scan_limit=100):
        """Bounded durable sweep for retries and crashed workers.

        A scheduler must poll this entry point even after queue delivery was
        acknowledged: queued retries and expired attempts live in PostgreSQL.
        Queue redelivery is an acceleration, not the recovery source of truth.
        """
        _lease_seconds(lease_seconds)
        if type(scan_limit) is not int or not 1 <= scan_limit <= 1000:
            raise ValueError("scan_limit must be an integer in [1, 1000]")
        with self.transaction() as connection:
            candidates = connection.execute(
                """SELECT j.id FROM public.render_jobs j
                   JOIN public.org_quotas q ON q.organization_id=j.organization_id
                   WHERE j.available_at <= clock_timestamp()
                   AND (j.state='queued' OR
                        (j.state='running' AND j.lease_expires_at <= clock_timestamp()))
                   AND (SELECT count(*) FROM public.render_jobs active
                        WHERE active.organization_id=j.organization_id
                        AND active.state='running'
                        AND active.lease_expires_at > clock_timestamp()) < q.max_active_jobs
                   ORDER BY j.available_at,j.created_at,j.id LIMIT %s""", (scan_limit,)
            ).fetchall()
        for candidate in candidates:
            claimed = self.claim_job(candidate["id"], lease_seconds)
            if claimed is not None and claimed["state"] == "running":
                return claimed
        return None

    @staticmethod
    def _lock_organization(connection, job_id):
        identity = connection.execute(
            "SELECT organization_id FROM public.render_jobs WHERE id=%s", (job_id,)
        ).fetchone()
        if identity is None:
            return False
        # Claim and renewal take this lock before touching the job row. A renewal
        # must commit before another claim counts live leases, including when the
        # old visible lease expires while the renewal transaction is uncommitted.
        connection.execute(
            "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(%s, 1))",
            (str(identity["organization_id"]),),
        )
        return True

    def claim_job(self, job_id, lease_seconds=60):
        seconds = _lease_seconds(lease_seconds)
        with self.transaction() as connection:
            if not self._lock_organization(connection, job_id):
                return None
            # All claimers for an organization take this lock before any job lock.
            # Quota rows remain read-only to the worker principal. Hash collisions
            # only serialize unrelated orgs; they cannot weaken the cap.
            row = connection.execute(
                """SELECT *, lease_expires_at > clock_timestamp() AS lease_live,
                   available_at <= clock_timestamp() AS ready
                   FROM public.render_jobs WHERE id=%s FOR UPDATE""", (job_id,)
            ).fetchone()
            if row["state"] == "completed":
                return row
            if row["state"] not in ("queued", "running") or not row["ready"]:
                return None
            if row["state"] == "running" and row["lease_live"]:
                return None
            if row["state"] == "running":
                connection.execute(
                    """UPDATE public.render_attempts SET ended_at=clock_timestamp(),result='expired'
                       WHERE job_id=%s AND ended_at IS NULL""", (job_id,)
                )
            if row["attempt_count"] >= row["max_attempts"]:
                final = connection.execute(
                    """UPDATE public.render_jobs SET state='failed',last_error='attempts_exhausted',
                       lease_token=NULL,lease_expires_at=NULL,updated_at=clock_timestamp()
                       WHERE id=%s RETURNING *""", (job_id,)
                ).fetchone()
                _audit(connection, final, "job_failed", {"error": "attempts_exhausted"})
                return None
            quota = connection.execute(
                "SELECT max_active_jobs FROM public.org_quotas WHERE organization_id=%s",
                (row["organization_id"],),
            ).fetchone()
            active = connection.execute(
                """SELECT count(*) AS n FROM public.render_jobs WHERE organization_id=%s
                   AND state='running' AND lease_expires_at > clock_timestamp()""",
                (row["organization_id"],),
            ).fetchone()["n"]
            if quota is None or active >= quota["max_active_jobs"]:
                return None
            token = uuid.uuid4()
            claimed = connection.execute(
                """UPDATE public.render_jobs SET state='running',fence=fence+1,
                   attempt_count=attempt_count+1,lease_token=%s,
                   lease_expires_at=clock_timestamp()+make_interval(secs => %s),
                   updated_at=clock_timestamp() WHERE id=%s RETURNING *""",
                (token, seconds, job_id),
            ).fetchone()
            connection.execute(
                """INSERT INTO public.render_attempts
                   (organization_id,job_id,attempt_no,lease_token,fence,lease_expires_at)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (claimed["organization_id"], job_id, claimed["attempt_count"], token,
                 claimed["fence"], claimed["lease_expires_at"]),
            )
            _audit(connection, claimed, "job_claimed", {"fence": claimed["fence"]})
            return claimed

    @staticmethod
    def _owned(connection, job_id, token, fence):
        row = connection.execute(
            """SELECT *, lease_expires_at > clock_timestamp() AS lease_live
               FROM public.render_jobs WHERE id=%s FOR UPDATE""", (job_id,)
        ).fetchone()
        if row is None or str(row["lease_token"]) != str(token) or row["fence"] != fence:
            raise StaleLease("wrong attempt token or fence")
        return row

    def renew_lease(self, job_id, token, fence, lease_seconds=60):
        seconds = _lease_seconds(lease_seconds)
        with self.transaction() as connection:
            if not self._lock_organization(connection, job_id):
                raise StaleLease("job does not exist")
            row = self._owned(connection, job_id, token, fence)
            if row["state"] != "running" or not row["lease_live"]:
                raise StaleLease("cannot renew an expired or finished attempt")
            renewed = connection.execute(
                """UPDATE public.render_jobs SET lease_expires_at=
                   clock_timestamp()+make_interval(secs => %s),updated_at=clock_timestamp()
                   WHERE id=%s AND state='running' AND lease_token=%s AND fence=%s
                   AND lease_expires_at > clock_timestamp() RETURNING *""",
                (seconds, job_id, token, fence)
            ).fetchone()
            if renewed is None:
                raise StaleLease("lease expired before renewal")
            connection.execute(
                """UPDATE public.render_attempts SET lease_expires_at=%s
                   WHERE job_id=%s AND fence=%s AND ended_at IS NULL""",
                (renewed["lease_expires_at"], job_id, fence),
            )
            return renewed

    @staticmethod
    def _validate_manifest(row, manifest):
        if not isinstance(manifest, dict):
            raise ValueError("manifest must be an object")
        encoded = json.dumps(manifest, allow_nan=False)
        if len(encoded.encode()) > 65536:
            raise ValueError("manifest exceeds 64KiB")
        generation = f'{row["organization_id"]}/{row["id"]}/{row["fence"]}'
        outputs = manifest.get("outputs")
        if manifest.get("generation") != generation or not isinstance(outputs, list) or not 1 <= len(outputs) <= 3:
            raise ValueError("manifest generation or outputs invalid")
        keys = set()
        for output in outputs:
            if not isinstance(output, dict):
                raise ValueError("invalid output")
            key = output.get("key")
            if (not isinstance(key, str) or not key.startswith(generation + "/")
                    or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", key[len(generation)+1:])
                    or key in keys or not isinstance(output.get("sha256"), str)
                    or not re.fullmatch(r"[a-f0-9]{64}", output["sha256"])
                    or type(output.get("size_bytes")) is not int or output["size_bytes"] <= 0):
                raise ValueError("invalid output key, checksum or size")
            keys.add(key)

    def finish_job(self, job_id, token, fence, manifest):
        """Accept verified metadata for this generation; no remote bytes are checked.

        External storage adapters must write immutable generation-scoped objects
        and verify bytes before calling this method. This is a local DB contract.
        """
        with self.transaction() as connection:
            row = self._owned(connection, job_id, token, fence)
            self._validate_manifest(row, manifest)
            if row["state"] == "completed" and row["output_manifest"] == manifest:
                return row  # response lost after commit: reconcile, no new effect
            if row["state"] != "running" or not row["lease_live"]:
                raise StaleLease("expired or finished attempt")
            finished = connection.execute(
                """UPDATE public.render_jobs SET state='completed',output_manifest=%s,
                   updated_at=clock_timestamp() WHERE id=%s AND state='running'
                   AND lease_token=%s AND fence=%s AND lease_expires_at > clock_timestamp()
                   RETURNING *""",
                (Jsonb(manifest), job_id, token, fence),
            ).fetchone()
            if finished is None:
                raise StaleLease("lease expired before completion")
            connection.execute(
                """UPDATE public.render_attempts SET result='completed',ended_at=clock_timestamp()
                   WHERE job_id=%s AND fence=%s AND lease_token=%s AND ended_at IS NULL""",
                (job_id, fence, token),
            )
            _audit(connection, finished, "job_completed", {"fence": fence})
            return finished

    def fail_job(self, job_id, token, fence, error_code):
        if error_code not in ("timeout", "429", "crash"):
            raise ValueError("unsupported retry error")
        with self.transaction() as connection:
            row = self._owned(connection, job_id, token, fence)
            if row["state"] != "running" or not row["lease_live"]:
                raise StaleLease("expired or finished attempt")
            terminal = row["attempt_count"] >= row["max_attempts"]
            state = "failed" if terminal else "queued"
            updated = connection.execute(
                """UPDATE public.render_jobs SET state=%s,last_error=%s,
                   lease_token=NULL,lease_expires_at=NULL,
                   available_at=clock_timestamp()+make_interval(secs => %s),
                   updated_at=clock_timestamp() WHERE id=%s AND state='running'
                   AND lease_token=%s AND fence=%s AND lease_expires_at > clock_timestamp()
                   RETURNING *""",
                (state, error_code, 2 ** min(row["attempt_count"], 6), job_id, token, fence),
            ).fetchone()
            if updated is None:
                raise StaleLease("lease expired before retry transition")
            connection.execute(
                """UPDATE public.render_attempts SET result='failed',ended_at=clock_timestamp()
                   WHERE job_id=%s AND fence=%s AND lease_token=%s AND ended_at IS NULL""",
                (job_id, fence, token),
            )
            _audit(connection, updated, "job_failed" if terminal else "job_retry",
                   {"fence": fence, "error": error_code})
            return updated
