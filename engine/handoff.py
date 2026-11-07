"""Durable outbox handoff with bounded leases and a named, idempotent adapter.

Adapters MUST implement authoritative lookup(event_id) and atomic named creation
ensure_task(event_id, job_id). A network queue with weaker semantics needs its own
reconciliation design. No cloud-provider guarantees are claimed by these stubs.
"""
from __future__ import annotations

import uuid

from .control_plane import ControlPlane, StaleLease, _audit, _lease_seconds


class QueueRateLimited(RuntimeError):
    pass


class Dispatcher(ControlPlane):
    MAX_SEND_ATTEMPTS = 5

    def claim_outbox(self, lease_seconds=30):
        seconds = _lease_seconds(lease_seconds)
        with self.transaction() as connection:
            row = connection.execute(
                """SELECT * FROM public.render_outbox
                   WHERE state='pending' AND dispatched_at IS NULL
                   AND available_at <= clock_timestamp()
                   AND (lease_expires_at IS NULL OR lease_expires_at <= clock_timestamp())
                   ORDER BY available_at,created_at,id FOR UPDATE SKIP LOCKED LIMIT 1"""
            ).fetchone()
            if row is None:
                return None
            # A crashed final send must not create an unbounded delivery loop.
            if row["attempt_count"] >= self.MAX_SEND_ATTEMPTS:
                terminal = connection.execute(
                    """UPDATE public.render_outbox SET state='reconcile',last_error='attempts_exhausted',
                       lease_token=NULL,lease_expires_at=NULL WHERE id=%s RETURNING *""",
                    (row["id"],),
                ).fetchone()
                _audit(connection, terminal, "outbox_reconcile", {"error": "attempts_exhausted"})
                return None
            return connection.execute(
                """UPDATE public.render_outbox SET fence=fence+1,attempt_count=attempt_count+1,
                   lease_token=%s,lease_expires_at=clock_timestamp()+make_interval(secs => %s)
                   WHERE id=%s RETURNING *""", (uuid.uuid4(), seconds, row["id"])
            ).fetchone()

    @staticmethod
    def _owned_outbox(connection, event):
        row = connection.execute(
            """SELECT *,lease_expires_at > clock_timestamp() AS lease_live
               FROM public.render_outbox WHERE id=%s FOR UPDATE""", (event["id"],)
        ).fetchone()
        if (row is None or row["state"] != "pending" or not row["lease_live"]
                or row["fence"] != event["fence"]
                or str(row["lease_token"]) != str(event["lease_token"])):
            raise StaleLease("outbox lease expired or replaced")
        return row

    def ack(self, event):
        with self.transaction() as connection:
            row = self._owned_outbox(connection, event)
            updated = connection.execute(
                """UPDATE public.render_outbox SET state='dispatched',dispatched_at=clock_timestamp(),
                   lease_token=NULL,lease_expires_at=NULL,last_error=NULL
                   WHERE id=%s AND state='pending' AND lease_token=%s AND fence=%s
                   AND lease_expires_at > clock_timestamp() RETURNING *""",
                (row["id"], event["lease_token"], event["fence"]),
            ).fetchone()
            if updated is None:
                raise StaleLease("outbox lease expired before acknowledgment")
            _audit(connection, updated, "outbox_dispatched", {"event_id": str(row["id"])})
            return updated

    def release(self, event, error_code):
        if error_code not in ("timeout", "429", "unknown"):
            raise ValueError("unsupported delivery error")
        with self.transaction() as connection:
            row = self._owned_outbox(connection, event)
            terminal = row["attempt_count"] >= self.MAX_SEND_ATTEMPTS
            state = "reconcile" if terminal else "pending"
            updated = connection.execute(
                """UPDATE public.render_outbox SET state=%s,last_error=%s,
                   lease_token=NULL,lease_expires_at=NULL,
                   available_at=clock_timestamp()+make_interval(secs => %s)
                   WHERE id=%s AND state='pending' AND lease_token=%s AND fence=%s
                   AND lease_expires_at > clock_timestamp() RETURNING *""",
                (state, error_code, 2 ** min(row["attempt_count"], 6), row["id"],
                 event["lease_token"], event["fence"]),
            ).fetchone()
            if updated is None:
                raise StaleLease("outbox lease expired before retry transition")
            _audit(connection, updated, "outbox_reconcile" if terminal else "outbox_retry",
                   {"event_id": str(row["id"]), "error": error_code})
            return updated

    def dispatch_one(self, queue):
        event = self.claim_outbox()
        if event is None:
            return None
        event_id, job_id = str(event["id"]), str(event["job_id"])
        try:
            # A send may have committed remotely before a lost response/crash.
            # Always reconcile its stable name before attempting creation again.
            task = queue.lookup(event_id)
            if task is None:
                queue.ensure_task(event_id, job_id)
            elif str(task["job_id"]) != job_id:
                raise ValueError("named task payload conflicts with durable outbox")
        except QueueRateLimited:
            updated = self.release(event, "429")
        except TimeoutError:
            updated = self.release(event, "timeout")
        except Exception:
            updated = self.release(event, "unknown")
        else:
            self.ack(event)
            return {"state": "dispatched", "event_id": event_id}
        return {"state": "reconcile" if updated["state"] == "reconcile" else "retry",
                "event_id": event_id}
