"""Actual local HTTP fault server tests. This is NOT a GCS service/emulator.

Only our REST/precondition/recovery contract is exercised. Remote GCS, IAM,
resumable service semantics and quotas remain NOT_VALIDATED until G3B.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import socket
import tempfile
import threading
import time
import unittest
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import uuid4

from engine.cloud_transport import (
    CloudResponseLimit, CloudTransportError, UrllibCloudTransport,
)
from engine.gcs_store import GCSObjectStore, GCSProviderError, GCSReconciliationRequired
from engine.object_store import (
    InvalidObjectKey, ObjectConflict, ObjectLimitExceeded, ObjectMissing,
)


class _State:
    def __init__(self):
        self.lock = threading.RLock()
        self.objects, self.sessions, self.requests = {}, {}, []
        self.counter = 0
        self.faults = {}
        self.origin = None

    def metadata(self, key):
        item = self.objects[key]
        return {"name": key, "size": str(len(item["body"])), "generation": str(item["generation"]),
                "metadata": {"sha256": item["sha256"]}}

    def publish(self, key, body, sha256=None):
        self.counter += 1
        self.objects[key] = {"body": body, "generation": self.counter,
                             "sha256": sha256 or hashlib.sha256(body).hexdigest()}

    def take(self, fault):
        value = self.faults.get(fault)
        if isinstance(value, int) and value:
            self.faults[fault] -= 1
            return True
        return False


class _HTTPFaultHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _response(self, status, body=b"", **headers):
        if isinstance(body, dict):
            body = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                # Deliberate response-bound tests close before reading the body.
                self.close_connection = True

    def _disconnect(self):
        self.close_connection = True
        self.connection.shutdown(socket.SHUT_RDWR)
        self.connection.close()

    def _handle(self):
        state = self.server.state
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        with state.lock:
            state.requests.append({"method": self.command, "path": self.path,
                                   "headers": dict(self.headers), "body": body})
            if self.headers.get("Authorization") != "Bearer isolated-fake-token":
                return self._response(401)
            if state.take("429"):
                return self._response(429)
            if state.take("redirect"):
                return self._response(302, Location="http://127.0.0.1:1/credential-theft")
            objects_path = "/storage/v1/b/isolated-test-bucket/o"
            upload_path = "/upload/storage/v1/b/isolated-test-bucket/o"
            if self.command == "POST" and parsed.path == upload_path:
                metadata = json.loads(body)
                if query.get("ifGenerationMatch") != ["0"] or "acl" in metadata:
                    return self._response(400)
                key = query["name"][0]
                if key in state.objects:
                    return self._response(412)
                identifier = uuid4().hex
                state.sessions[identifier] = {"key": key, "body": bytearray(),
                                               "sha256": metadata["metadata"]["sha256"],
                                               "total": int(self.headers["X-Upload-Content-Length"])}
                location = state.faults.get("session_location") or state.origin + upload_path + "?upload_id=" + identifier
                return self._response(200, Location=location)
            if parsed.path == upload_path and "upload_id" in query:
                identifier = query["upload_id"][0]
                session = state.sessions.get(identifier)
                if session is None:
                    return self._response(404)
                if self.command != "PUT":
                    return self._response(405)
                if state.take("expire_session"):
                    del state.sessions[identifier]
                    return self._response(404)
                content_range = self.headers.get("Content-Range", "")
                if content_range == f'bytes */{session["total"]}' and not body:
                    if session.get("complete"):
                        return self._response(200, state.metadata(session["key"]))
                    if session["total"] == 0:
                        state.publish(session["key"], b"", session["sha256"])
                        session["complete"] = True
                        return self._response(200, state.metadata(session["key"]))
                    headers = {"Range": f'bytes=0-{len(session["body"]) - 1}'} if session["body"] else {}
                    return self._response(308, **headers)
                match = re.fullmatch(r"bytes ([0-9]+)-([0-9]+)/([0-9]+)", content_range)
                if not match or int(match[1]) != len(session["body"]) or len(body) != int(match[2]) - int(match[1]) + 1:
                    return self._response(400)
                if state.take("partial_chunk"):
                    session["body"].extend(body[:len(body) // 2])
                else:
                    session["body"].extend(body)
                if len(session["body"]) == session["total"]:
                    key = session["key"]
                    if key in state.objects:
                        return self._response(412)
                    state.publish(key, bytes(session["body"]), session["sha256"])
                    session["complete"] = True
                    if state.take("lost_final"):
                        return self._disconnect()
                    if state.take("lost_final_expired_session"):
                        del state.sessions[identifier]
                        return self._disconnect()
                    return self._response(200, state.metadata(key))
                if state.take("lost_chunk"):
                    return self._disconnect()
                if state.take("delay_chunk"):
                    time.sleep(0.12)
                return self._response(308, Range=f'bytes=0-{len(session["body"]) - 1}')
            if parsed.path == objects_path and self.command == "GET":
                prefix = query.get("prefix", [""])[0]
                names = sorted(key for key in state.objects if key.startswith(prefix))
                if state.faults.get("inventory_cross_org"):
                    names.append(state.faults["inventory_cross_org"])
                page_size = state.faults.get("page_size", 1000)
                start = int(query.get("pageToken", ["0"])[0])
                result = {"items": [{"name": key} for key in names[start:start + page_size]]}
                if start + page_size < len(names):
                    result["nextPageToken"] = str(start + page_size)
                if state.faults.get("repeated_page"):
                    result["nextPageToken"] = "0"
                return self._response(200, result)
            if parsed.path.startswith(objects_path + "/"):
                key = unquote(parsed.path[len(objects_path) + 1:])
                if key not in state.objects:
                    return self._response(404)
                if self.command == "DELETE" and state.take("replace_before_delete"):
                    state.publish(key, b"replacement")
                item = state.objects[key]
                generation = query.get("ifGenerationMatch", [None])[0]
                if generation is not None and generation != str(item["generation"]):
                    return self._response(412)
                if self.command == "DELETE":
                    del state.objects[key]
                    if state.take("lost_delete"):
                        return self._disconnect()
                    return self._response(204)
                if self.command == "GET" and query.get("alt") == ["media"]:
                    body = item["body"]
                    if state.take("corrupt_media"):
                        body = b"x" * len(body)
                    return self._response(200, body)
                if self.command == "GET":
                    return self._response(200, state.metadata(key))
            return self._response(404)

    do_GET = do_POST = do_PUT = do_DELETE = _handle


class GCSLocalHTTPContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _HTTPFaultHandler)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        self.state = self.server.state = _State()
        self.state.origin = self.origin
        self.directory = tempfile.TemporaryDirectory(prefix="g3a-gcs-http-contract-")
        self.base = Path(self.directory.name)
        self.org, self.other = uuid4(), uuid4()
        self.key = f"{self.org}/{uuid4()}/1/vertical.mp4"
        self.source = self.base / "synthetic.bin"
        self.source.write_bytes((bytes(range(256)) * 2200) + b"end")
        self.transport = UrllibCloudTransport(lambda: "isolated-fake-token", allow_loopback_http=True)
        self.store = self.make_store()

    def tearDown(self):
        self.directory.cleanup()

    def make_store(self, **kwargs):
        return GCSObjectStore("isolated-test-bucket", self.transport, endpoint=self.origin,
                              allow_loopback_http=True, max_object_bytes=2 * 1024 * 1024,
                              chunk_size=256 * 1024, sleeper=lambda _: None, **kwargs)

    def test_private_resumable_roundtrip_actual_byte_hash_and_private_download(self):
        metadata = self.store.put_file(self.org, self.key, self.source)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), metadata["sha256"])
        self.assertEqual(self.source.stat().st_size, metadata["size_bytes"])
        self.assertEqual(metadata, self.store.stat(self.org, self.key))
        target = self.base / "download.bin"
        self.assertEqual(metadata, self.store.get_file(self.org, self.key, target, max_bytes=600000))
        self.assertEqual(self.source.read_bytes(), target.read_bytes())
        self.assertEqual(0o600, target.stat().st_mode & 0o777)
        uploads = [r for r in self.state.requests if r["method"] == "POST"]
        self.assertEqual(1, len(uploads))
        self.assertEqual(["0"], parse_qs(urlsplit(uploads[0]["path"]).query)["ifGenerationMatch"])
        self.assertNotIn("acl", json.loads(uploads[0]["body"]))
        self.assertEqual(3, len([r for r in self.state.requests if r["method"] == "PUT"]))
        self.assertTrue(all(r["headers"]["Authorization"] == "Bearer isolated-fake-token" for r in self.state.requests))
        self.assertNotIn("upload_id", str(metadata))

    def test_immutable_same_bytes_replay_and_conflicting_bytes(self):
        original = self.store.put_file(self.org, self.key, self.source)
        self.assertEqual(original, self.store.put_file(self.org, self.key, self.source))
        self.source.write_bytes(b"conflicting payload")
        with self.assertRaises(ObjectConflict):
            self.store.put_file(self.org, self.key, self.source)
        self.assertEqual(original, self.store.stat(self.org, self.key))

    def test_concurrent_identical_create_only_uploads_publish_one_generation(self):
        gate = threading.Barrier(8)
        def put(_):
            gate.wait(timeout=10)
            return self.store.put_file(self.org, self.key, self.source)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(put, range(8)))
        self.assertEqual(1, len({r["generation"] for r in results}))
        self.assertEqual(1, self.state.counter)
        self.assertEqual([self.key], self.store.list_keys(self.org))

    def test_transient_429_is_bounded_and_recovers(self):
        self.state.faults["429"] = 2
        self.store.put_file(self.org, self.key, self.source)
        self.assertEqual(1, len(self.state.objects))
        self.state.faults["429"] = 100
        with self.assertRaises(GCSReconciliationRequired):
            self.store.stat(self.org, self.key)
        self.assertEqual(96, self.state.faults["429"])

    def test_lost_chunk_response_queries_range_before_resuming(self):
        self.state.faults["lost_chunk"] = 1
        self.store.put_file(self.org, self.key, self.source)
        puts = [r for r in self.state.requests if r["method"] == "PUT"]
        self.assertEqual(f'bytes */{self.source.stat().st_size}', puts[1]["headers"]["Content-Range"])
        self.assertEqual(0, len(puts[1]["body"]))
        self.assertTrue(puts[2]["headers"]["Content-Range"].startswith("bytes 262144-"))
        self.assertEqual(self.source.read_bytes(), self.state.objects[self.key]["body"])

    def test_actual_socket_timeout_queries_range_before_resuming(self):
        self.state.faults["delay_chunk"] = 1
        store = self.make_store(timeout=0.05, upload_deadline=2.0)
        store.put_file(self.org, self.key, self.source)
        puts = [r for r in self.state.requests if r["method"] == "PUT"]
        self.assertTrue(any(r["headers"]["Content-Range"].startswith("bytes */") for r in puts))
        self.assertEqual(self.source.read_bytes(), self.state.objects[self.key]["body"])
        self.assertEqual(1, self.state.counter)

    def test_partial_acknowledgement_resends_only_remaining_bytes(self):
        self.state.faults["partial_chunk"] = 1
        self.store.put_file(self.org, self.key, self.source)
        puts = [r for r in self.state.requests if r["method"] == "PUT"]
        self.assertTrue(puts[1]["headers"]["Content-Range"].startswith("bytes 131072-"))
        self.assertEqual(self.source.read_bytes(), self.state.objects[self.key]["body"])

    def test_lost_success_and_expired_session_reconcile_named_verified_object(self):
        for fault in ("lost_final", "lost_final_expired_session"):
            with self.subTest(fault=fault):
                key = f"{self.org}/{uuid4()}/1/square.mp4"
                self.state.faults[fault] = 1
                metadata = self.store.put_file(self.org, key, self.source)
                self.assertEqual(metadata, self.store.stat(self.org, key))
        self.assertEqual(2, self.state.counter)

    def test_unknown_session_does_not_overwrite_or_report_success(self):
        self.state.faults["expire_session"] = 1
        with self.assertRaises(GCSReconciliationRequired):
            self.store.put_file(self.org, self.key, self.source)
        self.assertEqual({}, self.state.objects)
        # A new authorized caller retry uses the same immutable create precondition.
        self.store.put_file(self.org, self.key, self.source)
        self.assertEqual(1, self.state.counter)

    def test_remote_metadata_sha_is_untrusted_until_actual_bytes_match(self):
        self.store.put_file(self.org, self.key, self.source)
        self.state.faults["corrupt_media"] = 1
        with self.assertRaises(GCSProviderError):
            self.store.stat(self.org, self.key)
        self.state.objects[self.key]["sha256"] = "0" * 64
        target = self.base / "corrupt.bin"
        with self.assertRaises(GCSProviderError):
            self.store.get_file(self.org, self.key, target, max_bytes=600000)
        self.assertFalse(target.exists())

    def test_tenant_boundaries_and_unsafe_keys_fail_before_network(self):
        operations = (
            lambda: self.store.stat(self.other, self.key),
            lambda: self.store.put_file(self.other, self.key, self.source),
            lambda: self.store.get_file(self.other, self.key, self.base / "stolen", max_bytes=600000),
            lambda: self.store.delete(self.other, self.key),
            lambda: self.store.list_keys(self.other, str(self.org) + "/"),
        )
        for operation in operations:
            with self.assertRaises(InvalidObjectKey):
                operation()
        for key in (f"{self.org}/../x", f"{self.org}/%2f/x", f"{self.org}/x\\y", str(self.org),
                    f"{self.org}//x", f"{self.org}/x/", "/absolute"):
            with self.assertRaises(InvalidObjectKey):
                self.store.put_file(self.org, key, self.source)
        with self.assertRaises(InvalidObjectKey):
            self.store.list_keys(str(self.org).upper())
        self.assertEqual([], self.state.requests)

    def test_malicious_session_urls_never_receive_credentials(self):
        malicious = ("https://evil.example/upload?upload_id=x",
                     "http://169.254.169.254/upload?upload_id=x",
                     self.origin + "/different-bucket?upload_id=x",
                     self.origin.replace("127.0.0.1", "localhost") + "/upload?upload_id=x",
                     "https://storage.googleapis.com/other?upload_id=x",
                     self.origin + "/upload/storage/v1/b/isolated-test-bucket/o?upload_id=x#secret")
        for url in malicious:
            with self.subTest(url=url):
                self.state.faults["session_location"] = url
                before = len(self.state.requests)
                with self.assertRaises((CloudTransportError, GCSProviderError)):
                    self.store.put_file(self.org, self.key, self.source)
                self.assertEqual(2, len(self.state.requests) - before)
                self.assertNotIn("isolated-fake-token", str(self.state.objects))
        self.assertEqual({}, self.state.objects)

    def test_redirect_cannot_forward_bearer_to_second_endpoint(self):
        self.state.faults["redirect"] = 1
        with self.assertRaises(GCSProviderError):
            self.store.stat(self.org, self.key)
        self.assertEqual(1, len(self.state.requests))

    def test_loopback_transport_requires_explicit_test_capability(self):
        transport = UrllibCloudTransport(lambda: "must-not-leak")
        for url in (self.origin, "http://storage.googleapis.com", "https://evil.example",
                    "https://storage.googleapis.com:444", "https://user@storage.googleapis.com"):
            with self.assertRaises(CloudTransportError):
                transport.request("GET", url, {}, b"", 1)
        with self.assertRaises(CloudTransportError):
            transport.request("GET", "https://storage.googleapis.com", {"Authorization": "Bearer attacker"}, b"", 1)
        self.assertEqual([], self.state.requests)

    def test_generation_conditional_delete_protects_replacement_race(self):
        metadata = self.store.put_file(self.org, self.key, self.source)
        self.state.faults["replace_before_delete"] = 1
        with self.assertRaises(ObjectConflict):
            self.store.delete_generation(self.org, self.key, expected_generation=metadata["generation"],
                                         expected_sha256=metadata["sha256"])
        self.assertEqual(b"replacement", self.state.objects[self.key]["body"])
        with self.assertRaises(ObjectConflict):
            self.store.delete_generation(self.org, self.key, expected_generation=metadata["generation"],
                                         expected_sha256=metadata["sha256"])

    def test_lost_delete_response_is_retried_without_deleting_new_generation(self):
        self.store.put_file(self.org, self.key, self.source)
        self.state.faults["lost_delete"] = 1
        self.store.delete(self.org, self.key)
        self.assertNotIn(self.key, self.state.objects)
        deletes = [r for r in self.state.requests if r["method"] == "DELETE"]
        self.assertEqual(2, len(deletes))
        self.assertEqual(deletes[0]["path"], deletes[1]["path"])

    def test_paginated_inventory_is_tenant_scoped_and_fail_closed(self):
        for suffix in ("a", "b", "c"):
            self.state.publish(f"{self.org}/assets/{suffix}", suffix.encode())
        self.state.publish(f"{self.other}/assets/private", b"other-tenant")
        self.state.faults["page_size"] = 1
        inventory = self.store.inventory(self.org)
        self.assertEqual(3, len(inventory))
        self.assertTrue(all(item["key"].startswith(str(self.org) + "/") for item in inventory))
        with self.assertRaises(ObjectLimitExceeded):
            self.make_store(max_list_pages=2).inventory(self.org)
        self.state.faults["inventory_cross_org"] = f"{self.other}/assets/private"
        with self.assertRaises(InvalidObjectKey):
            self.store.inventory(self.org)
        self.assertEqual(4, len(self.state.objects))

    def test_inventory_page_cycle_does_not_return_partial_orphans(self):
        self.state.publish(f"{self.org}/assets/a", b"a")
        self.state.faults["repeated_page"] = True
        with self.assertRaises(GCSProviderError):
            self.store.inventory(self.org)
        self.assertEqual(1, len(self.state.objects))

    def test_download_and_response_limits_do_not_publish_partial_files(self):
        self.store.put_file(self.org, self.key, self.source)
        target = self.base / "limited.bin"
        with self.assertRaises(ObjectLimitExceeded):
            self.store.get_file(self.org, self.key, target, max_bytes=100)
        self.assertFalse(target.exists())
        with self.assertRaises(CloudResponseLimit):
            self.transport.request("GET", self.origin + "/storage/v1/b/isolated-test-bucket/o/" + self.key.replace("/", "%2F") + "?alt=media",
                                   {}, b"", 1, max_response_bytes=2)
        self.assertEqual([], list(self.base.glob(".part-*")))

    def test_missing_checksum_metadata_and_stale_inventory_fail_closed(self):
        self.store.put_file(self.org, self.key, self.source)
        self.state.objects[self.key]["sha256"] = "not-a-checksum"
        with self.assertRaises(GCSProviderError):
            self.store.inventory(self.org)
        self.assertIn(self.key, self.state.objects)

    def test_upload_size_bound_rejects_before_network(self):
        oversized = self.base / "too-large"
        with oversized.open("wb") as output:
            output.truncate(2 * 1024 * 1024 + 1)
        with self.assertRaises(ObjectLimitExceeded):
            self.store.put_file(self.org, self.key, oversized)
        self.assertEqual([], self.state.requests)

    def test_empty_object_and_missing_completed_object_are_explicit(self):
        self.source.write_bytes(b"")
        metadata = self.store.put_file(self.org, self.key, self.source)
        self.assertEqual(0, metadata["size_bytes"])
        self.store.delete(self.org, self.key)
        with self.assertRaises(ObjectMissing):
            self.store.stat(self.org, self.key)

    def test_destination_and_source_symlinks_cannot_replace_private_files(self):
        linked = self.base / "linked-source"
        linked.symlink_to(self.source)
        with self.assertRaises(InvalidObjectKey):
            self.store.put_file(self.org, self.key, linked)
        self.store.put_file(self.org, self.key, self.source)
        target = self.base / "target"
        target.symlink_to(self.source)
        with self.assertRaises(ObjectConflict):
            self.store.get_file(self.org, self.key, target, max_bytes=600000)
        self.assertEqual(self.source.stat().st_size, len(self.state.objects[self.key]["body"]))


if __name__ == "__main__":
    unittest.main()
