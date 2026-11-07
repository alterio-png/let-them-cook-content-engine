"""Additive G2 persistence helpers; historical G1 behavior remains unchanged."""
from .control_plane import ControlPlane, StaleLease, _audit


class MediaControlPlane(ControlPlane):
    def get_spec(self, job_id):
        with self.transaction() as db:
            return db.execute(
                """SELECT a.*,b.caption_key,b.caption_sha256,b.caption_bytes,b.binding_digest,s.media_digest
                   FROM public.media_job_specs s JOIN public.assets a ON a.id=s.asset_id
                   AND a.organization_id=s.organization_id JOIN public.media_bindings b
                   ON b.asset_id=a.id AND b.organization_id=a.organization_id WHERE s.job_id=%s""",
                (job_id,)
            ).fetchone()

    def claim_next_media_job(self, lease_seconds=30, scan_limit=100):
        if type(scan_limit) is not int or not 1 <= scan_limit <= 1000:
            raise ValueError("scan_limit outside [1,1000]")
        with self.transaction() as db:
            candidates = db.execute(
                """SELECT j.id FROM public.render_jobs j JOIN public.media_job_specs s ON s.job_id=j.id
                   JOIN public.org_quotas q ON q.organization_id=j.organization_id
                   WHERE j.available_at<=clock_timestamp() AND (j.state='queued' OR
                    (j.state='running' AND j.lease_expires_at<=clock_timestamp()))
                   AND (SELECT count(*) FROM public.render_jobs a WHERE a.organization_id=j.organization_id
                    AND a.state='running' AND a.lease_expires_at>clock_timestamp())<q.max_active_jobs
                   ORDER BY j.available_at,j.created_at,j.id LIMIT %s""", (scan_limit,)
            ).fetchall()
        for item in candidates:
            row = self.claim_job(item["id"], lease_seconds)
            if row and row["state"] == "running":
                return row
        return None

    def record_metrics(self, claim, stage, elapsed_ms, read_bytes, written_bytes, error_code=None):
        with self.transaction() as db:
            row = self._owned(db, claim["id"], claim["lease_token"], claim["fence"])
            if row["state"] != "completed" and (row["state"] != "running" or not row["lease_live"]):
                raise StaleLease("metrics attempt expired")
            db.execute(
                """INSERT INTO public.media_attempt_metrics(job_id,organization_id,fence,stage,
                   elapsed_ms,read_bytes,written_bytes,error_code) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(job_id,fence) DO UPDATE SET stage=EXCLUDED.stage,
                   elapsed_ms=EXCLUDED.elapsed_ms,read_bytes=EXCLUDED.read_bytes,
                   written_bytes=EXCLUDED.written_bytes,error_code=EXCLUDED.error_code,
                   updated_at=clock_timestamp()""",
                (claim["id"], claim["organization_id"], claim["fence"], stage,
                 max(0, int(elapsed_ms)), max(0, int(read_bytes)), max(0, int(written_bytes)), error_code)
            )
            _audit(db, row, "media_" + stage, {"fence": claim["fence"], "error": error_code,
                    "elapsed_ms": int(elapsed_ms), "read_bytes": int(read_bytes),
                    "written_bytes": int(written_bytes)})

    def reject_job(self, claim, error_code):
        with self.transaction() as db:
            row = self._owned(db, claim["id"], claim["lease_token"], claim["fence"])
            updated = db.execute(
                """UPDATE public.render_jobs SET state='failed',last_error=%s,
                   lease_token=NULL,lease_expires_at=NULL,updated_at=clock_timestamp()
                   WHERE id=%s AND state='running' AND lease_token=%s AND fence=%s
                   AND lease_expires_at>clock_timestamp() RETURNING *""",
                (error_code, claim["id"], claim["lease_token"], claim["fence"])
            ).fetchone()
            if not updated:
                raise StaleLease("rejection attempt expired")
            db.execute("""UPDATE public.render_attempts SET result='failed',ended_at=clock_timestamp()
                          WHERE job_id=%s AND fence=%s AND ended_at IS NULL""", (row["id"], row["fence"]))
            _audit(db, row, "media_rejected", {"fence": row["fence"], "error": error_code})
            return updated
