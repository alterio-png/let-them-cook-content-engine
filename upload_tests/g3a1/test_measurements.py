"""Measurement interpretation plus a small real multi-process upload workload."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location(
    "benchmark_g3a1", Path(__file__).resolve().parents[2] / "scripts/benchmark_g3a1.py")
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


class SharedUploadMeasurementTests(unittest.TestCase):
    def test_attempts_and_logical_operations_are_reported_separately(self):
        # One operation saw backpressure then succeeded, another hit its quota.
        # Reporting three operations or discarding the 503 biases capacity data.
        summary = benchmark.summarize_http([
            {"status": 503, "elapsed_seconds": .2},
            {"status": 201, "elapsed_seconds": .4},
            {"status": 429, "elapsed_seconds": .6},
        ], [
            {"status": 201, "initial_status": 503, "attempts": 2, "elapsed_seconds": .9},
            {"status": 429, "initial_status": 429, "attempts": 1, "elapsed_seconds": .6},
        ])
        self.assertEqual(2, summary["logical_operations"])
        self.assertEqual(3, summary["http_attempts"])
        self.assertEqual({"201": 1, "429": 1, "503": 1}, summary["http_status_counts"])
        self.assertEqual({"429": 1, "503": 1}, summary["initial_status_counts"])
        self.assertEqual({"201": 1, "429": 1}, summary["final_status_counts"])
        self.assertEqual(1, summary["backpressure_retries"])
        self.assertEqual(.4, summary["http_attempt_latency_seconds"]["p50"])
        self.assertEqual(.75, summary["logical_operation_latency_seconds"]["p50"])

    def test_counter_equality_does_not_hide_an_extra_uncharged_reservation(self):
        correct = {"used_count": 1, "charged_rows": 1, "used_bytes": 128,
                   "charged_bytes": 128, "receipts": 0, "max_count": 2, "max_bytes": 256}
        benchmark.verify_quota(correct, expected_count=1, expected_bytes=128)
        for mutation in ({"charged_rows": 2}, {"charged_bytes": 256},
                         {"receipts": 1}, {"max_bytes": 127}):
            with self.subTest(mutation=mutation), self.assertRaises(AssertionError):
                benchmark.verify_quota({**correct, **mutation}, expected_count=1, expected_bytes=128)

    def test_small_real_http_postgres_store_and_process_restart(self):
        # PostgreSQL is mandatory. No skip/fake substitutes for this execution.
        with tempfile.TemporaryDirectory(prefix="g3a1-measurement-test-") as directory:
            report = benchmark.run_benchmark(directory, duplicate_requests=10,
                                             distinct_requests=4, quota_slots=2)
            self.assertEqual("PASS_LOCAL_ONLY", report["status"])
            self.assertEqual("NOT_VALIDATED", report["cloud_services"])
            self.assertEqual("UNKNOWN", report["monetary_cost"])
            self.assertEqual(10, report["cases"][0]["logical_operations"])
            self.assertEqual({"201": 10}, report["cases"][0]["final_status_counts"])
            self.assertEqual(2, report["cases"][1]["accepted"])
            self.assertEqual(2, report["cases"][1]["quota_denied"])
            self.assertTrue(report["replicas"]["private_workdirs_distinct"])
            self.assertEqual(0, report["replicas"]["sqlite_ledger_files_created"])
            self.assertTrue(report["cases"][2]["actual_private_object_hash_and_size_verified"])
            self.assertGreaterEqual(report["resources"]["process_count_peak"], 3)
            self.assertGreater(report["resources"]["reaped_children_user_seconds"], 0)
            self.assertEqual(report, json.loads((Path(directory) / "summary.json").read_text()))
            events = [json.loads(line) for line in (Path(directory) / "events.jsonl").read_text().splitlines()]
            self.assertGreaterEqual(sum(row["event"] == "http_attempt" for row in events), 19)
            self.assertTrue(any(row["event"] == "resource_sample" for row in events))
