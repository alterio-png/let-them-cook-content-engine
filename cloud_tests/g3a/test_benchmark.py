"""Small real PostgreSQL/FFmpeg execution plus measurement boundary checks."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("benchmark_g3a", Path(__file__).resolve().parents[2] / "scripts/benchmark_g3a.py")
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


class BenchmarkTests(unittest.TestCase):
    def test_percentiles_interpolate_actual_observations(self):
        self.assertEqual({"observations": 0, "p50": None, "p95": None, "p99": None}, benchmark.distribution([]))
        self.assertEqual(7, benchmark.percentile([7], .99))
        self.assertEqual(2.5, benchmark.percentile([4, 1, 3, 2], .5))
        self.assertAlmostEqual(3.85, benchmark.percentile([4, 1, 3, 2], .95))
        with self.assertRaises(ValueError):
            benchmark.percentile([1], 1.1)

    def test_production_or_ambiguous_dsn_is_rejected(self):
        for dsn in (None, "postgresql://localhost/production", "postgresql://db.example/g1_test",
                    "host=localhost dbname=g1_test hostaddr=8.8.8.8", "dbname=g1_test"):
            with self.subTest(dsn=dsn), self.assertRaises(ValueError):
                benchmark.require_isolated_dsn(dsn)
        self.assertEqual("g1_test", benchmark.require_isolated_dsn("postgresql://127.0.0.1/g1_test")["dbname"])
        with patch.dict("os.environ", {"PGHOSTADDR": "8.8.8.8"}), self.assertRaises(ValueError):
            benchmark.require_isolated_dsn("postgresql://127.0.0.1/g1_test")

    def test_rss_sampler_includes_live_descendant_memory(self):
        import os
        own = benchmark.proc_tree_snapshot(os.getpid())
        child = subprocess.Popen([sys.executable, "-c", "import time; data=bytearray(24000000); print('ready',flush=True); time.sleep(15)"], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual("ready", child.stdout.readline().strip())
            sampled = benchmark.proc_tree_snapshot(os.getpid())
            self.assertGreaterEqual(sampled["processes"], own["processes"] + 1)
            self.assertGreater(sampled["rss_bytes"], own["rss_bytes"] + 20_000_000)
        finally:
            child.terminate()
            child.wait(timeout=5)
            child.stdout.close()

    def test_small_real_queued_cohort_and_recovery(self):
        # Missing PostgreSQL/FFmpeg is an error. This runs no mock render path.
        with tempfile.TemporaryDirectory() as directory:
            report = benchmark.run_benchmark(directory, jobs=(3,), workers=2,
                                              duplicate_requests=10, include_recovery=True)
            self.assertEqual("PASS_LOCAL_ONLY", report["status"])
            self.assertEqual("NOT_VALIDATED", report["cloud_services"])
            cohort = report["cases"][1]
            self.assertEqual(3, cohort["completed"])
            self.assertGreater(cohort["output_video_bytes"], 0)
            self.assertGreater(cohort["resources"]["native_media_process_count_peak"], 0)
            self.assertGreater(cohort["resources"]["ffmpeg_process_count_peak"], 0)
            self.assertGreater(cohort["resources"]["reaped_children_user_seconds"], 0)
            self.assertEqual(2, cohort["claimed_worker_highwater"])
            self.assertTrue(cohort["concurrency_caps_verified"])
            self.assertIsNone(cohort["local_unit_measurements"]["monetary_cost_per_job"])
            self.assertEqual(2, report["cases"][2]["retry_attempts"])
            self.assertTrue(report["cases"][2]["obsolete_fence_completion_rejected"])
            events = [json.loads(line) for line in (Path(directory) / "events.jsonl").read_text().splitlines()]
            self.assertTrue(any(event["event"] == "resource_sample" for event in events))
            self.assertEqual(report, json.loads((Path(directory) / "summary.json").read_text()))
