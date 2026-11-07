import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from engine.core import (ContractError, ConflictError, ReconciliationRequired, connection,
                         input_in_tenant, run_job, job_status, sha256_file, validate_identifiers)
from engine.cli import demo


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        (self.workspace / "inputs" / "alpha").mkdir(parents=True)
        (self.workspace / "inputs" / "beta").mkdir(parents=True)
        (self.workspace / "inputs" / "alpha" / "sample.mp4").write_bytes(b"fake")
        (self.workspace / "inputs" / "beta" / "secret.mp4").write_bytes(b"secret")

    def tearDown(self):
        self.tmp.cleanup()

    def test_bad_tenant(self):
        for val in ("../beta", "Alpha", "a/b", "", "user@example.com"):
            with self.assertRaises(ContractError):
                validate_identifiers(val, "key")

    def test_bad_idempotency_key(self):
        for val in ("../dir", "", "hello space", "\n", "k"*130):
            with self.assertRaises(ContractError):
                validate_identifiers("alpha", val)

    def test_no_cross_tenant(self):
        with self.assertRaises(ContractError):
            input_in_tenant(self.workspace, "alpha", self.workspace / "inputs" / "beta" / "secret.mp4")

    def test_no_symlink_escape(self):
        link = self.workspace / "inputs" / "alpha" / "link.mp4"
        link.symlink_to(self.workspace / "inputs" / "beta" / "secret.mp4")
        with self.assertRaises(ContractError):
            input_in_tenant(self.workspace, "alpha", link)

    def test_missing_job(self):
        self.assertEqual("not_found", job_status(self.workspace,"alpha","none")["state"])

    def test_sqlite_unique(self):
        conn = connection(self.workspace / ".local" / "jobs.sqlite3")
        row = ("id", "alpha", "op", "digest", "running", "t", "t")
        conn.execute("INSERT INTO render_jobs (job_id,tenant,request_key,fingerprint,state,created_at,updated_at) VALUES (?,?,?,?,?,?,?)", row)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO render_jobs (job_id,tenant,request_key,fingerprint,state,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                         ("id2", "alpha", "op", "digest", "running", "t", "t"))
        conn.close()

    def test_sha(self):
        f = self.workspace / "hash.txt"
        f.write_text("abc")
        self.assertEqual(64, len(sha256_file(f)))


class VideoRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.workspace = Path(cls.tmp.name)
        paths = demo(cls.workspace, "demo")
        cls.source = Path(paths["video"])
        cls.caption = Path(paths["caption"])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_01_three_render_formats_with_audio_and_captions(self):
        result = run_job(self.workspace, "demo", "test-video-001", self.source, caption_arg=self.caption, duration_seconds=1.2)
        self.assertEqual("completed", result["state"])
        self.assertEqual(3, len(result["manifest"]["outputs"]))
        self.assertEqual("supplied-srt-burned", result["manifest"]["caption_mode"])
        self.assertEqual({("vertical",270,480), ("square",320,320), ("feed",320,400)},
                         {(x["preset"], x["width"], x["height"]) for x in result["manifest"]["outputs"]})
        from engine.core import probe_video
        root = self.workspace / "outputs" / "demo" / result["manifest"]["job_id"]
        for item in result["manifest"]["outputs"]:
            p = root / item["file"]
            self.assertTrue(p.exists())
            self.assertEqual(item["sha256"], sha256_file(p))
            self.assertTrue(probe_video(p)["has_audio"])
        self.__class__.rendered = result

    def test_02_retry_same_key_is_cached(self):
        a = run_job(self.workspace,"demo","test-video-001",self.source,caption_arg=self.caption,duration_seconds=1.2)
        self.assertTrue(a["cached"])

    def test_03_conflicting_request_is_rejected(self):
        with self.assertRaises(ConflictError):
            run_job(self.workspace,"demo","test-video-001",self.source,caption_arg=self.caption,duration_seconds=1.0)

    def test_04_corrupted_artifact_requires_manual_reconcile(self):
        item = self.rendered["manifest"]["outputs"][0]
        p = self.workspace / "outputs" / "demo" / self.rendered["manifest"]["job_id"] / item["file"]
        p.write_bytes(b"broken")
        with self.assertRaises(ReconciliationRequired):
            run_job(self.workspace,"demo","test-video-001",self.source,caption_arg=self.caption,duration_seconds=1.2)

    def test_05_parallel_duplicate_requests_do_not_duplicate_render(self):
        # Different key. Render is mocked to keep the race test fast and deterministic.
        from engine.core import run_job as real_run, probe_video
        import threading
        first = threading.Event(); release = threading.Event()
        original = __import__("engine.core",fromlist=["render_one"]).render_one
        def slow_render(*args,**kwargs):
            first.set()
            release.wait(timeout=10)
            return original(*args,**kwargs)
        with patch("engine.core.render_one",side_effect=slow_render) as mock:
            with ThreadPoolExecutor(max_workers=6) as pool:
                futures=[pool.submit(real_run,self.workspace,"demo","parallel-idem-01",self.source,duration_seconds=0.65) for _ in range(6)]
                self.assertTrue(first.wait(7))
                release.set()
                states=[f.result(timeout=40)["state"] for f in futures]
        self.assertEqual(3,mock.call_count)  # exactly one render per preset
        self.assertIn("completed",states)
        self.assertTrue(set(states).issubset({"completed","running"}))


if __name__ == "__main__":
    unittest.main()
