"""G2 fixtures: synthetic bytes, real PostgreSQL, private persistent local objects."""
import os
from pathlib import Path
import tempfile
import threading

from engine.media_ingest import ingest_media
from engine.media_pipeline import MediaWorker
from engine.object_store import LocalObjectStore
from g1.support import Fixture, connect


class PipelineFixture(Fixture):
    def __init__(self, video, caption):
        super().__init__()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store_root = self.root / "objects"
        self.scratch = self.root / "scratch"
        self.store = LocalObjectStore(self.store_root)
        self.video = Path(video)
        self.caption = Path(caption)
        self.dsn = os.environ["G1_TEST_DSN"]

    def ingest(self, *, caption=True, other=False, video=None, rights=True, limits=None):
        org, user = (self.org_b, self.user_b) if other else (self.org_a, self.user_a)
        with connect() as db:
            return ingest_media(db, self.store, org, user, video or self.video,
                                self.caption if caption else None,
                                rights_confirmed=rights, limits=limits)

    def submit_media(self, asset, key="media", *, other=False, duration_ms=800):
        org, user = (self.org_b, self.user_b) if other else (self.org_a, self.user_a)
        with connect("authenticated", user) as db:
            return db.execute("SELECT * FROM public.submit_media_render(%s,%s,%s,%s,'smoke')",
                              (org, key, asset["id"], duration_ms)).fetchone()

    def worker(self, **kwargs):
        return MediaWorker(self.dsn, self.store, self.scratch, **kwargs)

    def expire(self, job):
        with connect() as db:
            db.execute("UPDATE public.render_jobs SET lease_expires_at=clock_timestamp()-interval '1 second', "
                       "available_at=clock_timestamp()-interval '1 second' WHERE id=%s", (job["id"],))

    def ready(self, job):
        with connect() as db:
            db.execute("UPDATE public.render_jobs SET available_at=clock_timestamp()-interval '1 second' WHERE id=%s",
                       (job["id"],))

    def row(self, job):
        with connect() as db:
            return db.execute("SELECT * FROM public.render_jobs WHERE id=%s", (job["id"],)).fetchone()

    def metrics(self, job):
        with connect() as db:
            return db.execute("SELECT * FROM public.media_attempt_metrics WHERE job_id=%s ORDER BY fence", (job["id"],)).fetchall()

    def cleanup(self):
        try:
            with connect() as db:
                for table in ("media_attempt_metrics", "media_job_specs", "media_bindings"):
                    db.execute(f"DELETE FROM public.{table} WHERE organization_id=ANY(%s)", ([self.org_a, self.org_b],))
            super().cleanup()
        finally:
            self.temp.cleanup()


def crash_after_object(dsn, store_root, scratch_root, job_id, ready_pipe):
    """Real worker process: parent SIGKILLs after a durable object write."""
    once = False

    def checkpoint(stage, claim):
        nonlocal once
        if stage == "after_object" and not once:
            once = True
            ready_pipe.send({"id": str(claim["id"]), "fence": claim["fence"]})
            threading.Event().wait(30)
            raise RuntimeError("parent did not kill paused test worker")

    MediaWorker(dsn, LocalObjectStore(Path(store_root)), Path(scratch_root),
                fault_hook=checkpoint).run(job_id)
