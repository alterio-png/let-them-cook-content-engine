"""Blocking G2 integration tests use actual PostgreSQL and FFmpeg, never render mocks."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import unittest
from uuid import uuid4

import psycopg
from psycopg import sql

from engine.cli import demo
from engine.control_plane import StaleLease
from engine.core import ReconciliationRequired, sha256_file
from engine.handoff import Dispatcher
from engine.local_queue import LocalQueue
from engine.media_runtime import Limits, MediaError, inspect_media
from engine.object_store import InvalidObjectKey
from g1.support import connect
from g2.support import PipelineFixture, crash_after_object


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.synthetic = tempfile.TemporaryDirectory()
        cls.paths = demo(Path(cls.synthetic.name), "synthetic")

    @classmethod
    def tearDownClass(cls):
        cls.synthetic.cleanup()

    def setUp(self):
        self.f = PipelineFixture(self.paths["video"], self.paths["caption"])
        self.addCleanup(self.f.cleanup)

    def assert_outputs(self, result):
        self.assertEqual("completed", result["state"])
        manifest = result["output_manifest"]
        self.assertEqual(f"{result['organization_id']}/{result['id']}/{result['fence']}", manifest["generation"])
        self.assertEqual(3, len(manifest["outputs"]))
        expected = {"vertical": (270, 480), "square": (320, 320), "feed": (320, 400)}
        paths = {}
        for output in manifest["outputs"]:
            name = Path(output["key"]).stem
            dest = self.f.root / (uuid4().hex + ".mp4")
            copied = self.f.store.get_file(result["organization_id"], output["key"], dest, max_bytes=10*1024*1024)
            info = inspect_media(dest, Limits())
            self.assertEqual(expected[name], (info["input_width"], info["input_height"]))
            self.assertTrue(info["has_audio"])
            self.assertAlmostEqual(0.8, info["duration_seconds"], delta=0.15)
            self.assertEqual(output["sha256"], sha256_file(dest))
            self.assertEqual(output["sha256"], copied["sha256"])
            self.assertEqual(output["size_bytes"], dest.stat().st_size)
            self.assertEqual((output["width"], output["height"]), expected[name])
            paths[name] = dest
        manifest_path = self.f.root / (uuid4().hex + ".json")
        self.f.store.get_file(result["organization_id"], manifest["generation"] + "/manifest.json", manifest_path, max_bytes=65536)
        self.assertEqual(manifest, json.loads(manifest_path.read_text()))
        return paths

    @staticmethod
    def frame_digest(path):
        # Decode an actual frame during the supplied subtitle interval.
        result = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-threads", "1", "-ss", "0.5", "-i", str(path),
                                 "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                                check=True, capture_output=True, timeout=15)
        if not result.stdout:
            raise AssertionError("FFmpeg decoded no frame")
        return hashlib.sha256(result.stdout).hexdigest()

    def test_full_dispatch_render_store_manifest_and_idempotent_repeat(self):
        asset = self.f.ingest()
        job = self.f.submit_media(asset)
        queue = LocalQueue(self.f.root / "queue.sqlite3")
        dispatch = Dispatcher(self.f.dsn).dispatch_one(queue)
        self.assertEqual("dispatched", dispatch["state"])
        self.assertEqual(1, len(queue.pending()))
        self.assertEqual(str(job["id"]), str(queue.pending()[0]["job_id"]))
        # Reopening validates the queue is durable across process/adapter lifetime.
        self.assertEqual(queue.pending(), LocalQueue(self.f.root / "queue.sqlite3").pending())
        worker = self.f.worker()
        complete = worker.run(job["id"])
        self.assert_outputs(complete)
        before_keys = self.f.store.list_keys(self.f.org_a)
        repeat = worker.run(job["id"])
        self.assertEqual(complete["output_manifest"], repeat["output_manifest"])
        self.assertEqual(1, repeat["attempt_count"])
        self.assertEqual(before_keys, self.f.store.list_keys(self.f.org_a))
        with connect() as db:
            n = db.execute("SELECT count(*) AS n FROM public.audit_events WHERE job_id=%s AND event_type='job_completed'", (job["id"],)).fetchone()["n"]
        self.assertEqual(1, n)
        metrics = self.f.metrics(job)
        self.assertEqual(1, len(metrics))
        self.assertGreater(metrics[0]["read_bytes"], 0)
        self.assertGreater(metrics[0]["written_bytes"], 0)
        self.assertGreaterEqual(metrics[0]["elapsed_ms"], 0)

    def test_provided_srt_changes_actual_decoded_pixels(self):
        with_caption = self.f.submit_media(self.f.ingest(), "captioned")
        without_caption = self.f.submit_media(self.f.ingest(caption=False), "plain")
        with_paths = self.assert_outputs(self.f.worker().run(with_caption["id"]))
        without_paths = self.assert_outputs(self.f.worker().run(without_caption["id"]))
        self.assertNotEqual(self.frame_digest(with_paths["vertical"]), self.frame_digest(without_paths["vertical"]))

    def test_same_immutable_input_reproduces_exact_bytes_in_two_operations(self):
        asset = self.f.ingest()
        first = self.f.submit_media(asset, "repro-first")
        second = self.f.submit_media(asset, "repro-second")
        a = self.f.worker().run(first["id"])
        b = self.f.worker().run(second["id"])
        self.assert_outputs(a)
        self.assert_outputs(b)
        hashes_a = {o["preset"]: o["sha256"] for o in a["output_manifest"]["outputs"]}
        hashes_b = {o["preset"]: o["sha256"] for o in b["output_manifest"]["outputs"]}
        # Deterministic fixed-runtime smoke recipe, not a cross-version promise.
        self.assertEqual(hashes_a, hashes_b)

    def test_100_submissions_one_job_reservation_and_authoritative_manifest(self):
        asset = self.f.ingest()
        gate = threading.Barrier(100)

        def submit(_):
            with connect("authenticated", self.f.user_a) as db:
                gate.wait(timeout=30)
                return db.execute("SELECT * FROM public.submit_media_render(%s,'same-real-media',%s,800,'smoke')",
                                  (self.f.org_a, asset["id"])).fetchone()

        with ThreadPoolExecutor(max_workers=100) as pool:
            submitted = list(pool.map(submit, range(100)))
        self.assertEqual(1, len({r["id"] for r in submitted}))
        self.assertEqual({"jobs": 1, "outbox": 1, "audit": 1, "reserved": 3}, self.f.counts())
        self.assert_outputs(self.f.worker().run(submitted[0]["id"]))
        with connect() as db:
            specs = db.execute("SELECT count(*) AS n FROM public.media_job_specs WHERE job_id=%s", (submitted[0]["id"],)).fetchone()["n"]
        self.assertEqual(1, specs)

    def test_two_tenants_execute_concurrently_without_object_or_sql_escape(self):
        first = self.f.submit_media(self.f.ingest(), "tenant-a")
        second = self.f.submit_media(self.f.ingest(other=True), "tenant-b", other=True)
        gate = threading.Barrier(2)

        def run(job):
            gate.wait(timeout=15)
            return self.f.worker().run(job["id"])

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, (first, second)))
        for result in results:
            self.assert_outputs(result)
            org = result["organization_id"]
            self.assertTrue(all(o["key"].startswith(str(org) + "/") for o in result["output_manifest"]["outputs"]))
        with self.assertRaises(InvalidObjectKey):
            self.f.store.stat(self.f.org_a, results[1]["output_manifest"]["outputs"][0]["key"])
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with connect("authenticated", self.f.user_a) as db:
                db.execute("SELECT * FROM public.submit_media_render(%s,'escape',%s,800,'smoke')", (self.f.org_b, second["source_asset_id"]))
        with connect("authenticated", self.f.user_a) as db:
            rows = db.execute("SELECT organization_id FROM public.media_job_specs").fetchall()
        self.assertEqual({self.f.org_a}, {r["organization_id"] for r in rows})

    def test_actual_sigkill_after_object_then_takeover_and_orphan_cleanup(self):
        job = self.f.submit_media(self.f.ingest())
        context = multiprocessing.get_context("spawn")
        read, write = context.Pipe(duplex=False)
        process = context.Process(target=crash_after_object,
                                  args=(self.f.dsn, str(self.f.store_root), str(self.f.scratch), str(job["id"]), write))
        process.start()
        self.addCleanup(lambda: process.kill() if process.is_alive() else None)
        self.assertTrue(read.poll(20), "worker did not reach durable object checkpoint")
        checkpoint = read.recv()
        process.kill()
        process.join(10)
        self.assertEqual(-signal.SIGKILL, process.exitcode)
        old_generation = f"{self.f.org_a}/{job['id']}/{checkpoint['fence']}"
        self.assertTrue(self.f.store.list_keys(self.f.org_a, old_generation))
        self.assertEqual("running", self.f.row(job)["state"])
        self.f.expire(job)
        worker = self.f.worker()
        complete = worker.run(job["id"])
        self.assertEqual(checkpoint["fence"] + 1, complete["fence"])
        self.assert_outputs(complete)
        # Model a killed upload's private fragment at the obsolete generation,
        # alongside a current-generation fragment which must remain protected.
        old_fragment_key = old_generation + "/.part-" + uuid4().hex
        current_fragment_key = complete["output_manifest"]["generation"] + "/.part-" + uuid4().hex
        for key in (old_fragment_key, current_fragment_key):
            fragment = self.f.store_root / key
            fragment.write_bytes(b"interrupted private upload")
            fragment.chmod(0o600)
        orphan = worker.reconcile_orphans(self.f.org_a, job["id"])
        self.assertTrue(orphan["objects"])
        self.assertTrue(orphan["scratch"])
        self.assertEqual([old_fragment_key], orphan["staging"])
        self.assertTrue(all(k.startswith(old_generation + "/") for k in orphan["objects"]))
        worker.reconcile_orphans(self.f.org_a, job["id"], delete=True)
        self.assertEqual([], self.f.store.list_keys(self.f.org_a, old_generation))
        self.assertFalse((self.f.store_root / old_fragment_key).exists())
        self.assertTrue((self.f.store_root / current_fragment_key).exists())
        self.assert_outputs(worker.run(job["id"]))
        metrics = self.f.metrics(job)
        self.assertEqual([1, 2], [m["fence"] for m in metrics])
        self.assertEqual("publishing", metrics[0]["stage"])
        self.assertEqual("completed", metrics[1]["stage"])
        with connect() as db:
            attempts = db.execute("SELECT result FROM public.render_attempts WHERE job_id=%s ORDER BY attempt_no", (job["id"],)).fetchall()
        self.assertEqual(["expired", "completed"], [a["result"] for a in attempts])

    def test_expired_worker_cannot_replace_newer_valid_generation(self):
        job = self.f.submit_media(self.f.ingest())
        successor = []
        triggered = False

        def takeover(stage, claim):
            nonlocal triggered
            if stage == "after_object" and not triggered:
                triggered = True
                self.f.expire(job)
                successor.append(self.f.worker().run(job["id"]))

        with self.assertRaises(StaleLease):
            self.f.worker(fault_hook=takeover).run(job["id"])
        self.assert_outputs(successor[0])
        current = self.f.row(job)
        self.assertEqual(successor[0]["output_manifest"], current["output_manifest"])
        self.assertEqual(2, current["fence"])

    def test_download_object_manifest_failures_are_audited_and_recoverable(self):
        # Real pipeline runs between durable boundaries. The hook only injects
        # I/O-like failures, never fakes decoding/encoding or output verification.
        for stage in ("before_download", "after_object", "after_manifest"):
            with self.subTest(stage=stage):
                asset = self.f.ingest()
                job = self.f.submit_media(asset, stage)

                def fault(where, claim):
                    if where == stage:
                        raise OSError("synthetic I/O failure at " + stage)

                result = self.f.worker(fault_hook=fault).run(job["id"])
                self.assertEqual("queued", result["state"])
                self.assertIsNone(result["output_manifest"])
                self.assertTrue(self.f.metrics(job))
                self.f.ready(job)
                complete = self.f.worker().run(job["id"])
                self.assertEqual(2, complete["attempt_count"])
                self.assert_outputs(complete)
                self.f.worker().reconcile_orphans(self.f.org_a, job["id"], delete=True)

    def test_database_commit_failure_rolls_back_and_new_worker_recovers(self):
        job = self.f.submit_media(self.f.ingest())
        function = "g2_test_fail_" + uuid4().hex
        with connect() as db:
            db.execute(sql.SQL("""CREATE FUNCTION public.{}() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN IF NEW.event_type='job_completed' AND NEW.job_id={job_id}::uuid THEN RAISE EXCEPTION 'synthetic G2 commit failure'; END IF;
                RETURN NEW; END $$""").format(sql.Identifier(function), job_id=sql.Literal(str(job["id"]))))
            db.execute(sql.SQL("CREATE TRIGGER {} BEFORE INSERT ON public.audit_events FOR EACH ROW EXECUTE FUNCTION public.{}()").format(sql.Identifier(function), sql.Identifier(function)))
        try:
            result = self.f.worker().run(job["id"])
            self.assertNotEqual("completed", result["state"])
            self.assertIsNone(self.f.row(job)["output_manifest"])
        finally:
            with connect() as db:
                db.execute(sql.SQL("DROP TRIGGER {} ON public.audit_events").format(sql.Identifier(function)))
                db.execute(sql.SQL("DROP FUNCTION public.{}()").format(sql.Identifier(function)))
        self.f.ready(job)
        self.assert_outputs(self.f.worker().run(job["id"]))

    def test_lost_completion_response_reconciles_no_new_generation(self):
        job = self.f.submit_media(self.f.ingest())

        def lost(stage, claim):
            if stage == "after_commit":
                raise TimeoutError("synthetic lost response after durable DB commit")

        result = self.f.worker(fault_hook=lost).run(job["id"])
        self.assert_outputs(result)
        self.assertEqual(1, result["attempt_count"])
        replay = self.f.worker().run(job["id"])
        self.assertEqual(result["output_manifest"], replay["output_manifest"])
        self.assertEqual(1, replay["attempt_count"])

    def test_invalid_media_rights_and_caps_rejected_before_submission(self):
        corrupt = self.f.root / "bad.mp4"
        corrupt.write_bytes(b"not a video")
        for options in ({"video": corrupt}, {"rights": False},
                        {"limits": replace(Limits(), max_bytes=8)},
                        {"limits": replace(Limits(), max_pixels=100)}):
            with self.subTest(options=options), self.assertRaises((MediaError, PermissionError)):
                self.f.ingest(**options)
        self.assertEqual({"jobs": 0, "outbox": 0, "audit": 0, "reserved": 0}, self.f.counts())

    def test_ingestion_requires_actor_membership_and_captions_are_immutable(self):
        from engine.media_ingest import ingest_media
        with self.assertRaises(PermissionError):
            with connect() as db:
                ingest_media(db, self.f.store, self.f.org_b, self.f.user_a,
                             self.f.video, self.f.caption, rights_confirmed=True)
        self.assertEqual([], self.f.store.list_keys(self.f.org_b))
        asset = self.f.ingest()
        job = self.f.submit_media(asset)
        with connect() as db:
            binding = db.execute("SELECT * FROM public.media_bindings WHERE asset_id=%s", (asset["id"],)).fetchone()
            spec = db.execute("SELECT * FROM public.media_job_specs WHERE job_id=%s", (job["id"],)).fetchone()
        self.assertEqual(sha256_file(self.f.caption), binding["caption_sha256"])
        self.assertEqual(64, len(spec["media_digest"]))
        for statement, params in (
            ("UPDATE public.media_bindings SET caption_sha256=%s WHERE asset_id=%s", ("b"*64, asset["id"])),
            ("UPDATE public.media_job_specs SET media_digest=%s WHERE job_id=%s", ("c"*64, job["id"])),
        ):
            with self.assertRaises(psycopg.errors.CheckViolation):
                with connect() as db:
                    db.execute(statement, params)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            with connect("authenticated", self.f.user_a) as db:
                db.execute("UPDATE public.media_bindings SET caption_key=NULL WHERE asset_id=%s", (asset["id"],))

    def test_ingestion_rollback_reports_private_unreferenced_input_objects(self):
        from engine.media_ingest import inventory_inputs
        function = "g2_ingest_fail_" + uuid4().hex
        with connect() as db:
            db.execute(sql.SQL("""CREATE FUNCTION public.{}() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN IF NEW.organization_id={org}::uuid THEN RAISE EXCEPTION 'synthetic ingest rollback'; END IF; RETURN NEW; END $$""").format(sql.Identifier(function), org=sql.Literal(str(self.f.org_a))))
            db.execute(sql.SQL("CREATE TRIGGER {} BEFORE INSERT ON public.media_bindings FOR EACH ROW EXECUTE FUNCTION public.{}()").format(sql.Identifier(function), sql.Identifier(function)))
        try:
            with self.assertRaises(psycopg.errors.RaiseException):
                self.f.ingest()
        finally:
            with connect() as db:
                db.execute(sql.SQL("DROP TRIGGER {} ON public.media_bindings").format(sql.Identifier(function)))
                db.execute(sql.SQL("DROP FUNCTION public.{}()").format(sql.Identifier(function)))
        with connect() as db:
            orphans = inventory_inputs(db, self.f.store, self.f.org_a)
        self.assertEqual(2, len(orphans))
        self.assertTrue(all(k.startswith(str(self.f.org_a) + "/assets/") for k in orphans))
        self.assertEqual(0, self.f.counts()["jobs"])
        self.assertEqual(0, self.f.counts()["reserved"])

    def test_actual_native_limits_and_cancellation_leave_no_authoritative_outputs(self):
        for label, limits, cancel in (
            ("timeout", replace(Limits(), timeout_seconds=0.001), None),
            ("ffmpeg", replace(Limits(), max_output_bytes=8192), None),
            ("cancelled", Limits(), threading.Event()),
        ):
            with self.subTest(label=label):
                if cancel:
                    cancel.set()
                job = self.f.submit_media(self.f.ingest(), key=label)
                result = self.f.worker(limits=limits, cancel_event=cancel).run(job["id"])
                self.assertIn(result["state"], {"queued", "failed"})
                self.assertIsNone(result["output_manifest"])
                self.assertEqual([], self.f.store.list_keys(self.f.org_a, f"{self.f.org_a}/{job['id']}/"))
                metrics = self.f.metrics(job)
                self.assertTrue(metrics)
                self.assertEqual(label, metrics[-1]["error_code"])
                self.assertFalse(any(self.f.scratch.rglob("*.mp4")))

    def test_corrupt_completed_object_requires_reconciliation_without_rerender(self):
        job = self.f.submit_media(self.f.ingest())
        complete = self.f.worker().run(job["id"])
        key = complete["output_manifest"]["outputs"][0]["key"]
        self.f.store.delete(self.f.org_a, key, expected_sha256=complete["output_manifest"]["outputs"][0]["sha256"])
        with self.assertRaises(ReconciliationRequired):
            self.f.worker().run(job["id"])
        self.assertEqual(1, self.f.row(job)["attempt_count"])
        self.assertEqual("completed", self.f.row(job)["state"])


if __name__ == "__main__":
    unittest.main()
