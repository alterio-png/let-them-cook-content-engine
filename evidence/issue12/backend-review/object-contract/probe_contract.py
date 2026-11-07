"""Independent synthetic local checks. No database, credentials, or cloud calls."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from uuid import uuid4

from engine.object_store import LocalObjectStore, ObjectConflict
from engine.shared_upload_authority import PostgresUploadAuthority
from engine.upload_authority import CapabilityError


class Probe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="i12-objects-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.org = str(uuid4())
        self.key = f"{self.org}/uploads/{uuid4()}/unvalidated.bin"
        self.data = b"synthetic-issue12-object-contract" * 4096
        self.src = self.root / "source.bin"
        self.src.write_bytes(self.data)
        self.src.chmod(0o600)
        self.store = LocalObjectStore(self.root / "private", max_object_bytes=len(self.data))
        self.authority = PostgresUploadAuthority.__new__(PostgresUploadAuthority)
        self.grant = {"organization_id": self.org, "object_key": self.key,
                      "size_bytes": len(self.data), "sha256": hashlib.sha256(self.data).hexdigest()}

    def test_original_path_replacement_after_freeze_does_not_change_publication(self):
        with self.authority._source_copy(self.src, self.grant) as frozen:
            self.src.unlink()
            self.src.write_bytes(b"X" * len(self.data))
            self.store.put_file(self.org, self.key, frozen)
            verified = self.authority._read_stored(self.grant, self.store)
            self.assertEqual(self.grant["sha256"], verified["sha256"])
            self.assertNotEqual(self.src.read_bytes(), frozen.read_bytes())
        self.assertFalse(frozen.exists())

    def test_wrong_original_body_and_oversize_rejected_before_publication(self):
        for body in (b"X" * len(self.data), self.data + b"X"):
            self.src.write_bytes(body)
            with self.assertRaises(CapabilityError):
                with self.authority._source_copy(self.src, self.grant):
                    self.fail("wrong body was admitted")
        self.assertEqual([], self.store.list_keys(self.org))

    def test_self_consistent_wrong_download_cannot_bypass_expected_hash(self):
        wrong = b"Y" * len(self.data)
        class WrongStore:
            def get_file(_self, org, key, destination, *, max_bytes):
                destination.write_bytes(wrong)
                return {"key": key, "size_bytes": len(wrong), "sha256": hashlib.sha256(wrong).hexdigest()}
        with self.assertRaises(CapabilityError):
            self.authority._read_stored(self.grant, WrongStore())

    def test_missing_or_changed_provider_metadata_is_rejected(self):
        self.store.put_file(self.org, self.key, self.src)
        delegate = self.store
        class BadMetadata:
            def __init__(_self, field, value):
                _self.field, _self.value = field, value
            def get_file(_self, *args, **kwargs):
                result = delegate.get_file(*args, **kwargs)
                result[_self.field] = _self.value
                return result
        for field, value in (("key", self.key + "x"), ("size_bytes", len(self.data) + 1), ("sha256", "0" * 64)):
            with self.subTest(field=field), self.assertRaises(CapabilityError):
                self.authority._read_stored(self.grant, BadMetadata(field, value))

    def test_committed_generation_cannot_be_dropped_or_changed(self):
        self.store.put_file(self.org, self.key, self.src)
        self.grant["receipt"] = {"generation": "123"}
        delegate = self.store
        class Generation:
            def __init__(_self, generation):
                _self.generation = generation
            def get_file(_self, *args, **kwargs):
                result = delegate.get_file(*args, **kwargs)
                if _self.generation is not None:
                    result["generation"] = _self.generation
                return result
        self.assertEqual("123", self.authority._read_stored(self.grant, Generation("123"))["generation"])
        for generation in (None, "124"):
            with self.subTest(generation=generation), self.assertRaises(CapabilityError):
                self.authority._read_stored(self.grant, Generation(generation))

    def test_conflicting_concurrent_creates_preserve_one_complete_object(self):
        other = self.root / "other.bin"
        other.write_bytes(b"Z" * len(self.data))
        sources = [self.src, other] * 10
        gate = threading.Barrier(len(sources))
        def attempt(source):
            gate.wait(timeout=5)
            try:
                return self.store.put_file(self.org, self.key, source)["sha256"]
            except ObjectConflict:
                return "conflict"
        with ThreadPoolExecutor(max_workers=len(sources)) as pool:
            outcomes = list(pool.map(attempt, sources))
        accepted = set(outcomes) - {"conflict"}
        self.assertEqual(1, len(accepted))
        self.assertEqual(10, outcomes.count("conflict"))
        self.assertEqual(accepted.pop(), self.store.stat(self.org, self.key)["sha256"])
        self.assertEqual([self.key], self.store.list_keys(self.org))
        self.assertEqual([], self.store.list_staging(self.org))


if __name__ == "__main__":
    unittest.main(verbosity=2)
