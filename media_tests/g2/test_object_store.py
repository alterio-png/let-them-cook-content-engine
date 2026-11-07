"""Filesystem contract tests run against the persistent local backend, not mocks."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from engine.object_store import (
    InvalidObjectKey, LocalObjectStore, ObjectConflict, ObjectLimitExceeded,
    ObjectMissing,
)


class ObjectStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.root = self.base / "private-store"
        self.store = LocalObjectStore(self.root)
        self.org, self.other = uuid4(), uuid4()
        self.key = f"{self.org}/{uuid4()}/1/vertical.mp4"
        self.source = self.base / "source.bin"
        self.source.write_bytes(b"synthetic-local-media" * 100)

    def test_persistent_private_round_trip_and_checksums(self):
        expected = hashlib.sha256(self.source.read_bytes()).hexdigest()
        metadata = self.store.put_file(self.org, self.key, self.source)
        self.assertEqual({"key": self.key, "sha256": expected,
                          "size_bytes": self.source.stat().st_size}, metadata)
        reopened = LocalObjectStore(self.root)
        self.assertEqual(metadata, reopened.stat(self.org, self.key))
        target = self.base / "download.bin"
        self.assertEqual(metadata, reopened.get_file(self.org, self.key, target, max_bytes=4096))
        self.assertEqual(self.source.read_bytes(), target.read_bytes())
        self.assertEqual(0o600, target.stat().st_mode & 0o777)
        self.assertEqual(0o600, (self.root / self.key).stat().st_mode & 0o777)
        for parent in (self.root / self.key).parents:
            if parent == self.base:
                break
            self.assertEqual(0o700, parent.stat().st_mode & 0o777)

    def test_immutable_replay_and_conflicting_content(self):
        first = self.store.put_file(self.org, self.key, self.source)
        self.assertEqual(first, self.store.put_file(self.org, self.key, self.source))
        changed = self.base / "changed.bin"
        changed.write_bytes(b"different")
        with self.assertRaises(ObjectConflict):
            self.store.put_file(self.org, self.key, changed)
        self.assertEqual(first, self.store.stat(self.org, self.key))
        self.assertEqual([], self.store.list_staging(self.org))

    def test_concurrent_identical_puts_publish_one_immutable_object(self):
        gate = threading.Barrier(12)
        def publish(_):
            gate.wait(timeout=10)
            return self.store.put_file(self.org, self.key, self.source)
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(publish, range(12)))
        self.assertEqual(1, len({item["sha256"] for item in results}))
        self.assertEqual([self.key], self.store.list_keys(self.org))
        self.assertEqual([], self.store.list_staging(self.org))

    def test_cross_organization_read_write_list_and_delete_denied(self):
        self.store.put_file(self.org, self.key, self.source)
        for operation in (
            lambda: self.store.stat(self.other, self.key),
            lambda: self.store.put_file(self.other, self.key, self.source),
            lambda: self.store.get_file(self.other, self.key, self.base / "stolen", max_bytes=4096),
            lambda: self.store.delete(self.other, self.key),
            lambda: self.store.list_keys(self.other, str(self.org) + "/job/"),
        ):
            with self.subTest(operation=operation), self.assertRaises(InvalidObjectKey):
                operation()
        self.assertEqual([], self.store.list_keys(self.other))
        self.assertEqual([self.key], self.store.list_keys(self.org))

    def test_unsafe_keys_and_noncanonical_organizations_rejected(self):
        for key in ("/absolute", "../escape", f"{self.org}/../escape", f"{self.org}/./x",
                    f"{self.org}//x", f"{self.org}/x/", f"{self.org}/x\\y",
                    f"{self.org}/%2e%2e/x", f"{self.org}/a\x00b", str(self.org)):
            with self.subTest(key=key), self.assertRaises(InvalidObjectKey):
                self.store.put_file(self.org, key, self.source)
        for organization in ("not-org", str(self.org).upper(), "../other", ""):
            with self.subTest(org=organization), self.assertRaises(InvalidObjectKey):
                self.store.list_keys(organization)

    def test_root_symlink_ancestor_and_replacement_rejected(self):
        symlink = self.base / "linked-root"
        symlink.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(InvalidObjectKey):
            LocalObjectStore(symlink)
        with self.assertRaises(InvalidObjectKey):
            LocalObjectStore(symlink / "nested")
        old = self.base / "old-root"
        self.root.rename(old)
        self.root.mkdir(mode=0o700)
        with self.assertRaises(InvalidObjectKey):
            self.store.put_file(self.org, self.key, self.source)

    def test_broad_root_permissions_rejected(self):
        wide = self.base / "world-visible"
        wide.mkdir(mode=0o755)
        wide.chmod(0o755)
        with self.assertRaises(InvalidObjectKey):
            LocalObjectStore(wide)

    def test_symlink_tenant_or_parent_cannot_escape_on_write(self):
        outside = self.base / "outside"
        outside.mkdir(mode=0o700)
        (self.root / str(self.org)).symlink_to(outside, target_is_directory=True)
        with self.assertRaises(InvalidObjectKey):
            self.store.put_file(self.org, self.key, self.source)
        self.assertEqual([], list(outside.iterdir()))

    def test_symlink_object_read_write_delete_and_listing_rejected(self):
        self.store.put_file(self.org, self.key, self.source)
        object_path = self.root / self.key
        object_path.unlink()
        object_path.symlink_to(self.source)
        for operation in (
            lambda: self.store.stat(self.org, self.key),
            lambda: self.store.get_file(self.org, self.key, self.base / "download", max_bytes=4096),
            lambda: self.store.put_file(self.org, self.key, self.source),
            lambda: self.store.delete(self.org, self.key),
            lambda: self.store.list_keys(self.org),
        ):
            with self.subTest(operation=operation), self.assertRaises(InvalidObjectKey):
                operation()
        self.assertTrue(self.source.exists())

    def test_source_and_download_parent_symlinks_rejected(self):
        linked = self.base / "linked-source"
        linked.symlink_to(self.source)
        with self.assertRaises(InvalidObjectKey):
            self.store.put_file(self.org, self.key, linked)
        self.store.put_file(self.org, self.key, self.source)
        scratch = self.base / "scratch"
        scratch.mkdir(mode=0o700)
        parent = self.base / "linked-scratch"
        parent.symlink_to(scratch, target_is_directory=True)
        with self.assertRaises(InvalidObjectKey):
            self.store.get_file(self.org, self.key, parent / "download", max_bytes=4096)

    def test_fifo_source_and_object_are_rejected_without_blocking(self):
        fifo = self.base / "fifo"
        os.mkfifo(fifo, 0o600)
        with self.assertRaises(InvalidObjectKey):
            self.store.put_file(self.org, self.key, fifo)
        self.store.put_file(self.org, self.key, self.source)
        object_path = self.root / self.key
        object_path.unlink()
        os.mkfifo(object_path, 0o600)
        with self.assertRaises(InvalidObjectKey):
            self.store.stat(self.org, self.key)

    def test_byte_limits_bound_put_get_and_stat(self):
        small = LocalObjectStore(self.root, max_object_bytes=10)
        with self.assertRaises(ObjectLimitExceeded):
            small.put_file(self.org, self.key, self.source)
        self.store.put_file(self.org, self.key, self.source)
        for operation in (
            lambda: self.store.get_file(self.org, self.key, self.base / "download", max_bytes=10),
            lambda: small.stat(self.org, self.key),
        ):
            with self.assertRaises(ObjectLimitExceeded):
                operation()
        self.assertFalse((self.base / "download").exists())
        self.assertEqual([], self.store.list_staging(self.org))

    def test_download_never_overwrites_existing_file(self):
        self.store.put_file(self.org, self.key, self.source)
        target = self.base / "existing"
        target.write_bytes(b"original")
        with self.assertRaises(ObjectConflict):
            self.store.get_file(self.org, self.key, target, max_bytes=4096)
        self.assertEqual(b"original", target.read_bytes())

    def test_write_interruption_cleans_unpublished_fragment(self):
        real_write = os.write
        count = 0
        def interrupted(fd, data):
            nonlocal count
            count += 1
            if count == 1:
                return real_write(fd, data[:7])
            raise OSError("synthetic disk interruption")
        with patch("engine.object_store.os.write", side_effect=interrupted):
            with self.assertRaisesRegex(OSError, "synthetic disk interruption"):
                self.store.put_file(self.org, self.key, self.source)
        self.assertEqual([], self.store.list_keys(self.org))
        self.assertEqual([], self.store.list_staging(self.org))

    def test_orphaned_private_upload_fragment_is_reported(self):
        self.store.put_file(self.org, self.key, self.source)
        orphan = (self.root / self.key).parent / ".part-interrupted-upload"
        orphan.write_bytes(b"partial")
        orphan.chmod(0o600)
        self.assertEqual([self.key], self.store.list_keys(self.org))
        fragments = self.store.list_staging(self.org)
        self.assertEqual(1, len(fragments))
        self.assertEqual(7, fragments[0]["size_bytes"])
        self.assertTrue(fragments[0]["key"].endswith(".part-interrupted-upload"))

    def test_checksum_guarded_delete_and_missing_objects(self):
        metadata = self.store.put_file(self.org, self.key, self.source)
        with self.assertRaises(ObjectConflict):
            self.store.delete(self.org, self.key, expected_sha256="0" * 64)
        self.store.delete(self.org, self.key, expected_sha256=metadata["sha256"])
        for operation in (
            lambda: self.store.stat(self.org, self.key),
            lambda: self.store.delete(self.org, self.key),
            lambda: self.store.get_file(self.org, self.key, self.base / "gone", max_bytes=4096),
        ):
            with self.assertRaises(ObjectMissing):
                operation()
        self.assertEqual([], self.store.list_keys(self.org))


if __name__ == "__main__":
    unittest.main()
