"""Private immutable GCS JSON API adapter; real provider execution NOT_VALIDATED.

The authenticated transport is server-owned. Resumable URIs are bearer secrets
and never returned, persisted in public metadata or included in exception text.
PostgreSQL alone grants job/fence authority; the adapter never changes a key.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat as stat_module
import time
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from uuid import uuid4

from .cloud_transport import CloudTransport, CloudTransportError, validate_cloud_url
from .object_store import (
    InvalidObjectKey, ObjectConflict, ObjectLimitExceeded, ObjectMissing,
    ObjectStoreError, _COMPONENT, _FILE_FLAGS, _external_directory, _organization,
)


class GCSReconciliationRequired(ObjectStoreError):
    """Provider acceptance cannot yet be proven; do not complete the DB job."""


class GCSProviderError(ObjectStoreError):
    pass


_RETRY = {408, 429, 500, 502, 503, 504}
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_GENERATION = re.compile(r"[1-9][0-9]{0,30}\Z")


class GCSObjectStore:
    """G2 ObjectStore contract, create-only and with byte-verified generations.

    The injected transport must honor max_response_bytes, endpoint restrictions
    and authentication. The production transport implements these restrictions;
    replacing it is a privileged dependency-injection capability, not user input.
    A full object read is bounded in memory by max_object_bytes. G3B must measure
    suitable worker memory before selecting a production object-size limit.
    """
    def __init__(self, bucket: str, transport: CloudTransport, *,
                 endpoint="https://storage.googleapis.com", allow_loopback_http=False,
                 max_object_bytes=128 * 1024 * 1024, chunk_size=8 * 1024 * 1024,
                 max_attempts=4, timeout=15.0, upload_deadline=300.0,
                 max_list_pages=100, max_list_items=10000, sleeper=time.sleep,
                 monotonic=time.monotonic):
        if not isinstance(bucket, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]", bucket):
            raise ValueError("Invalid GCS bucket name")
        parsed = validate_cloud_url(endpoint, allowed_hosts={"storage.googleapis.com"},
                                    allow_loopback_http=allow_loopback_http)
        if parsed.path not in {"", "/"} or parsed.query:
            raise ValueError("GCS endpoint must be an origin")
        bounds = (max_object_bytes, chunk_size, max_attempts, max_list_pages, max_list_items)
        if any(type(value) is not int or value <= 0 for value in bounds):
            raise ValueError("GCS limits must be positive integers")
        if chunk_size % (256 * 1024) or chunk_size > max_object_bytes:
            raise ValueError("Upload chunk must be a 256 KiB multiple within the object bound")
        if max_attempts > 10 or not 0 < timeout <= 60 or not timeout <= upload_deadline <= 600:
            raise ValueError("Invalid GCS retry/time bounds")
        self.bucket, self.transport = bucket, transport
        self.endpoint = endpoint.rstrip("/")
        self.allow_loopback_http = allow_loopback_http
        self.max_object_bytes, self.chunk_size = max_object_bytes, chunk_size
        self.max_attempts, self.timeout, self.upload_deadline = max_attempts, timeout, upload_deadline
        self.max_list_pages, self.max_list_items = max_list_pages, max_list_items
        self.sleeper, self.monotonic = sleeper, monotonic
        self._objects = f"{self.endpoint}/storage/v1/b/{quote(bucket, safe='')}/o"
        self._upload_path = f"/upload/storage/v1/b/{quote(bucket, safe='')}/o"

    @staticmethod
    def _key(org_id, key):
        organization = _organization(org_id)
        if not isinstance(key, str) or len(key) > 1024:
            raise InvalidObjectKey("Invalid object key")
        parts = key.split("/")
        if not 2 <= len(parts) <= 16 or parts[0] != organization:
            raise InvalidObjectKey("Cross-organization or unscoped object key")
        if any(not _COMPONENT.fullmatch(part) or part in {".", ".."} for part in parts):
            raise InvalidObjectKey("Unsafe object key component")
        return key

    @staticmethod
    def _json(response):
        try:
            value = json.loads(response.body)
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (ValueError, TypeError, UnicodeDecodeError):
            raise GCSProviderError("Invalid provider JSON response") from None

    def _call(self, method, url, *, headers=None, body=b"", limit=1024 * 1024,
              retry=True, deadline=None):
        attempts = self.max_attempts if retry else 1
        for attempt in range(attempts):
            remaining = self.timeout if deadline is None else min(self.timeout, deadline - self.monotonic())
            if remaining <= 0:
                raise GCSReconciliationRequired("Upload deadline expired; reconcile the named object")
            try:
                response = self.transport.request(method, url, headers or {}, body, remaining,
                                                  max_response_bytes=limit)
            except (CloudTransportError, TimeoutError):
                if attempt + 1 == attempts:
                    raise GCSReconciliationRequired("Cloud response uncertain; reconcile the named object") from None
            else:
                if response.status not in _RETRY or attempt + 1 == attempts:
                    return response
            self.sleeper(min(0.05 * (2 ** attempt), 1.0))
        raise AssertionError("Unreachable retry state")

    @staticmethod
    def _expect(response, statuses=(200,)):
        if response.status in statuses:
            return
        if response.status == 404:
            raise ObjectMissing("Object not found")
        if response.status == 412:
            raise ObjectConflict("Object generation precondition failed")
        if response.status in _RETRY:
            raise GCSReconciliationRequired("Provider unavailable; reconcile before completion")
        raise GCSProviderError(f"Provider request rejected with status {response.status}")

    def _metadata(self, org_id, key):
        self._key(org_id, key)
        response = self._call("GET", self._objects + "/" + quote(key, safe=""))
        self._expect(response)
        raw = self._json(response)
        try:
            generation, size = raw["generation"], raw["size"]
            digest = raw["metadata"]["sha256"]
            if raw["name"] != key or not isinstance(generation, str) or not _GENERATION.fullmatch(generation):
                raise ValueError
            if not isinstance(size, str) or not re.fullmatch(r"0|[1-9][0-9]{0,18}", size):
                raise ValueError
            if not isinstance(digest, str) or not _SHA.fullmatch(digest):
                raise ValueError
            size = int(size)
        except (ValueError, TypeError, KeyError):
            raise GCSProviderError("Invalid or incomplete immutable object metadata") from None
        if size > self.max_object_bytes:
            raise ObjectLimitExceeded("Remote object exceeds byte limit")
        return {"key": key, "generation": generation, "sha256": digest, "size_bytes": size}

    def _read(self, metadata, limit):
        if metadata["size_bytes"] > limit:
            raise ObjectLimitExceeded("Object exceeds download byte limit")
        query = urlencode({"alt": "media", "ifGenerationMatch": metadata["generation"]})
        response = self._call("GET", self._objects + "/" + quote(metadata["key"], safe="") + "?" + query,
                              limit=max(1, limit))
        self._expect(response)
        if len(response.body) > limit:
            raise ObjectLimitExceeded("Object exceeds download byte limit")
        if len(response.body) != metadata["size_bytes"] or hashlib.sha256(response.body).hexdigest() != metadata["sha256"]:
            raise GCSProviderError("Remote bytes do not match immutable size/SHA-256")
        return response.body

    def stat(self, org_id, key):
        metadata = self._metadata(org_id, key)
        self._read(metadata, self.max_object_bytes)
        return metadata

    def _reconcile(self, org_id, key, expected):
        try:
            found = self.stat(org_id, key)
        except ObjectMissing:
            return None
        if any(found[name] != expected[name] for name in ("key", "sha256", "size_bytes")):
            raise ObjectConflict("Immutable object already exists with different bytes")
        return found

    def _session(self, response):
        location = next((v for k, v in response.headers.items() if k.lower() == "location"), None)
        if not isinstance(location, str):
            raise GCSProviderError("Provider omitted private resumable session")
        parsed = validate_cloud_url(location, allowed_hosts={"storage.googleapis.com"},
                                    allow_loopback_http=self.allow_loopback_http)
        configured = urlsplit(self.endpoint)
        # Same origin and exact bucket upload endpoint; no credential forwarding
        # to another Google API, bucket, localhost port or attacker-controlled URL.
        if (parsed.scheme, parsed.netloc, parsed.path) != (configured.scheme, configured.netloc, self._upload_path):
            raise GCSProviderError("Resumable session escaped its configured bucket endpoint")
        try:
            query = parse_qs(parsed.query, strict_parsing=True)
        except ValueError:
            raise GCSProviderError("Invalid resumable session identifier") from None
        if not query.get("upload_id") or len(query["upload_id"]) != 1:
            raise GCSProviderError("Invalid resumable session identifier")
        return location

    @staticmethod
    def _offset(response, total):
        value = next((v for k, v in response.headers.items() if k.lower() == "range"), None)
        if value is None:
            return 0
        match = re.fullmatch(r"bytes=0-([0-9]+)", value)
        if not match or not 0 < int(match[1]) + 1 <= total:
            raise GCSProviderError("Invalid resumable acknowledgement")
        return int(match[1]) + 1

    def _upload(self, org_id, key, source_fd, expected, deadline):
        url = self.endpoint + self._upload_path + "?" + urlencode({
            "uploadType": "resumable", "ifGenerationMatch": "0", "name": key,
        })
        payload = json.dumps({"name": key, "metadata": {"sha256": expected["sha256"]}},
                             sort_keys=True, separators=(",", ":")).encode()
        response = self._call("POST", url, headers={"Content-Type": "application/json; charset=UTF-8",
                              "X-Upload-Content-Type": "application/octet-stream",
                              "X-Upload-Content-Length": str(expected["size_bytes"])},
                              body=payload, deadline=deadline)
        if response.status == 412:
            return self._reconcile(org_id, key, expected)
        self._expect(response, (200, 201))
        session = self._session(response)
        offset, uncertain, stalls = 0, False, 0
        total = expected["size_bytes"]
        while True:
            if uncertain:
                try:
                    response = self._call("PUT", session,
                                          headers={"Content-Length": "0", "Content-Range": f"bytes */{total}"},
                                          deadline=deadline)
                except GCSReconciliationRequired:
                    found = self._reconcile(org_id, key, expected)
                    if found:
                        return found
                    raise GCSReconciliationRequired("Resumable status is uncertain; reconcile the named object") from None
            else:
                os.lseek(source_fd, offset, os.SEEK_SET)
                chunk = os.read(source_fd, min(self.chunk_size, total - offset))
                if not chunk and offset < total:
                    raise ObjectConflict("Upload source changed during publication")
                content_range = f"bytes {offset}-{offset + len(chunk) - 1}/{total}" if chunk else f"bytes */{total}"
                try:
                    response = self._call("PUT", session,
                                          headers={"Content-Length": str(len(chunk)), "Content-Range": content_range},
                                          body=chunk, retry=False, deadline=deadline)
                except GCSReconciliationRequired:
                    uncertain = True
                    stalls += 1
                    if stalls >= self.max_attempts:
                        found = self._reconcile(org_id, key, expected)
                        if found:
                            return found
                        raise GCSReconciliationRequired("Resumable upload requires named-object reconciliation") from None
                    continue
            if response.status in {200, 201, 412, 404, 410}:
                found = self._reconcile(org_id, key, expected)
                if found:
                    return found
                raise GCSReconciliationRequired("Upload session ended without a verified named object")
            if response.status in _RETRY:
                uncertain = True
                stalls += 1
            elif response.status == 308:
                acknowledged = self._offset(response, total)
                if acknowledged < offset:
                    raise GCSProviderError("Resumable acknowledgement moved backwards")
                stalls = stalls + 1 if acknowledged == offset else 0
                offset, uncertain = acknowledged, False
            else:
                self._expect(response, (308,))
            if stalls >= self.max_attempts:
                found = self._reconcile(org_id, key, expected)
                if found:
                    return found
                raise GCSReconciliationRequired("Resumable upload made no bounded progress")
            if uncertain:
                self.sleeper(min(0.05 * (2 ** stalls), 1.0))

    def put_file(self, org_id, key, source):
        self._key(org_id, key)
        source = Path(source).absolute()
        with _external_directory(source.parent) as parent:
            try:
                source_fd = os.open(source.name, _FILE_FLAGS, dir_fd=parent)
            except OSError:
                raise InvalidObjectKey("Upload requires a regular, non-symlink source") from None
            try:
                info = os.fstat(source_fd)
                if not stat_module.S_ISREG(info.st_mode):
                    raise InvalidObjectKey("Upload source must be a regular file")
                if info.st_size > self.max_object_bytes:
                    raise ObjectLimitExceeded("Object exceeds upload byte limit")
                digest, total = hashlib.sha256(), 0
                while chunk := os.read(source_fd, min(1024 * 1024, self.max_object_bytes - total + 1)):
                    total += len(chunk)
                    if total > self.max_object_bytes:
                        raise ObjectLimitExceeded("Object exceeds upload byte limit")
                    digest.update(chunk)
                expected = {"key": key, "sha256": digest.hexdigest(), "size_bytes": total}
                found = self._reconcile(org_id, key, expected)
                if found:
                    return found
                deadline = self.monotonic() + self.upload_deadline
                result = self._upload(org_id, key, source_fd, expected, deadline)
                if result is None:
                    raise GCSReconciliationRequired("Concurrent object changed; retry named-object reconciliation")
                return result
            finally:
                os.close(source_fd)

    def get_file(self, org_id, key, destination, *, max_bytes):
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        metadata = self._metadata(org_id, key)
        data = self._read(metadata, min(max_bytes, self.max_object_bytes))
        destination = Path(destination).absolute()
        with _external_directory(destination.parent) as parent:
            temporary = ".part-" + uuid4().hex
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            try:
                view = memoryview(data)
                while view:
                    count = os.write(fd, view)
                    if count <= 0:
                        raise ObjectStoreError("Download write made no progress")
                    view = view[count:]
                os.fsync(fd)
                try:
                    os.link(temporary, destination.name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
                except FileExistsError:
                    raise ObjectConflict("Download destination already exists") from None
                os.fsync(parent)
            finally:
                os.close(fd)
                os.unlink(temporary, dir_fd=parent)
        return metadata

    def list_keys(self, org_id, prefix=""):
        organization = _organization(org_id)
        if not isinstance(prefix, str):
            raise InvalidObjectKey("Invalid inventory prefix")
        if prefix:
            normalized = prefix[:-1] if prefix.endswith("/") else prefix
            if normalized != organization:
                self._key(org_id, normalized)
        scoped_prefix = prefix if prefix.startswith(organization + "/") else organization + "/"
        found, token, seen_tokens = [], None, set()
        for _ in range(self.max_list_pages):
            query = {"prefix": scoped_prefix, "maxResults": str(min(1000, self.max_list_items)),
                     "fields": "items(name),nextPageToken"}
            if token:
                query["pageToken"] = token
            response = self._call("GET", self._objects + "?" + urlencode(query))
            self._expect(response)
            raw = self._json(response)
            items = raw.get("items", [])
            if not isinstance(items, list):
                raise GCSProviderError("Invalid orphan inventory")
            for item in items:
                if not isinstance(item, dict):
                    raise GCSProviderError("Invalid orphan inventory entry")
                key = self._key(org_id, item.get("name"))
                if not key.startswith(scoped_prefix) or key in found:
                    raise GCSProviderError("Unscoped or duplicate orphan inventory entry")
                found.append(key)
                if len(found) > self.max_list_items:
                    raise ObjectLimitExceeded("Orphan inventory exceeds item bound")
            token = raw.get("nextPageToken")
            if token is None:
                return sorted(found)
            if not isinstance(token, str) or not token or len(token) > 4096 or token in seen_tokens:
                raise GCSProviderError("Invalid or repeated orphan inventory page token")
            seen_tokens.add(token)
        raise ObjectLimitExceeded("Orphan inventory exceeds page bound; do not reconcile partial inventory")

    def inventory(self, org_id, prefix=""):
        """Verified snapshot, or fail without returning partial orphan candidates."""
        return [self.stat(org_id, key) for key in self.list_keys(org_id, prefix)]

    def delete_generation(self, org_id, key, *, expected_generation, expected_sha256):
        self._key(org_id, key)
        if not isinstance(expected_generation, str) or not _GENERATION.fullmatch(expected_generation):
            raise ValueError("Deletion requires an observed generation")
        if not isinstance(expected_sha256, str) or not _SHA.fullmatch(expected_sha256):
            raise ValueError("Deletion requires an observed SHA-256")
        observed = self.stat(org_id, key)
        if observed["generation"] != expected_generation or observed["sha256"] != expected_sha256:
            raise ObjectConflict("Deletion snapshot no longer matches the object")
        url = self._objects + "/" + quote(key, safe="") + "?" + urlencode({"ifGenerationMatch": expected_generation})
        response = self._call("DELETE", url)
        # Retrying a preconditioned DELETE after a lost success is idempotent.
        self._expect(response, (200, 204, 404))

    def delete(self, org_id, key, expected_sha256=None):
        observed = self.stat(org_id, key)
        if expected_sha256 is not None and observed["sha256"] != expected_sha256:
            raise ObjectConflict("Deletion checksum precondition failed")
        self.delete_generation(org_id, key, expected_generation=observed["generation"],
                               expected_sha256=observed["sha256"])
