"""Private immutable object storage for the isolated local G2 pipeline.

The organization argument is a capability supplied by the authorized caller,
not an identity verifier. Future R2/S3 adapters must preserve immutable creation,
checksums, bounded reads and generation-scoped keys; no cloud SDK is used here.
Local processes sharing this OS user are trusted. This is not an OS tenant jail.
"""
from __future__ import annotations

from contextlib import contextmanager
import errno
import hashlib
import os
from pathlib import Path
import re
import stat as stat_module
from typing import Protocol
from uuid import UUID, uuid4


class ObjectStoreError(RuntimeError):
    pass


class ObjectConflict(ObjectStoreError):
    pass


class ObjectMissing(ObjectStoreError):
    pass


class InvalidObjectKey(ObjectStoreError):
    pass


class ObjectLimitExceeded(ObjectStoreError):
    pass


class ObjectStore(Protocol):
    def put_file(self, org_id, key: str, source: Path) -> dict: ...
    def get_file(self, org_id, key: str, destination: Path, *, max_bytes: int) -> dict: ...
    def stat(self, org_id, key: str) -> dict: ...
    def list_keys(self, org_id, prefix: str = "") -> list[str]: ...
    def delete(self, org_id, key: str, expected_sha256: str | None = None) -> None: ...


_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_CHUNK = 1024 * 1024


def _organization(org_id) -> str:
    try:
        value = str(org_id)
        if str(UUID(value)) != value:
            raise ValueError
        return value
    except (ValueError, TypeError, AttributeError) as error:
        raise InvalidObjectKey("Organization must be a canonical UUID") from error


def _private(info, *, directory=False):
    kind = stat_module.S_ISDIR if directory else stat_module.S_ISREG
    if not kind(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise InvalidObjectKey("Object storage requires owned private directories/files")


@contextmanager
def _external_directory(path: Path, *, create=False):
    """Walk from / using no-follow descriptors, including every ancestor."""
    path = Path(path).absolute()
    fd = os.open("/", _DIR_FLAGS)
    try:
        for component in path.parts[1:]:
            if component in {"", ".", ".."}:
                raise InvalidObjectKey("Unsafe filesystem path")
            if create:
                try:
                    os.mkdir(component, 0o700, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    pass
            child = os.open(component, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise InvalidObjectKey("Symlink or non-directory in filesystem path") from error
        raise
    finally:
        os.close(fd)


class LocalObjectStore:
    def __init__(self, root: Path, *, max_object_bytes: int = 512 * 1024 * 1024):
        if type(max_object_bytes) is not int or max_object_bytes <= 0:
            raise ValueError("max_object_bytes must be positive")
        self.root = Path(root).absolute()
        self.max_object_bytes = max_object_bytes
        with _external_directory(self.root, create=True) as fd:
            info = os.fstat(fd)
            _private(info, directory=True)
            self._root_identity = (info.st_dev, info.st_ino)

    def _parts(self, org_id, key):
        organization = _organization(org_id)
        if not isinstance(key, str) or len(key) > 1024:
            raise InvalidObjectKey("Invalid object key")
        parts = key.split("/")
        if len(parts) < 2 or len(parts) > 16 or parts[0] != organization:
            raise InvalidObjectKey("Cross-organization or unscoped object key")
        if any(not _COMPONENT.fullmatch(part) or part in {".", ".."} for part in parts):
            raise InvalidObjectKey("Unsafe object key component")
        return parts

    @contextmanager
    def _directory(self, parts, *, create=False):
        with _external_directory(self.root) as root_fd:
            info = os.fstat(root_fd)
            _private(info, directory=True)
            if (info.st_dev, info.st_ino) != self._root_identity:
                raise InvalidObjectKey("Object store root was replaced")
            fd = os.dup(root_fd)
            try:
                for component in parts:
                    if create:
                        try:
                            os.mkdir(component, 0o700, dir_fd=fd)
                            os.fsync(fd)
                        except FileExistsError:
                            pass
                    child = os.open(component, _DIR_FLAGS, dir_fd=fd)
                    try:
                        _private(os.fstat(child), directory=True)
                    except BaseException:
                        os.close(child)
                        raise
                    os.close(fd)
                    fd = child
                yield fd
            except OSError as error:
                if error.errno == errno.ENOENT:
                    raise ObjectMissing("Object not found") from error
                if error.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise InvalidObjectKey("Symlink or non-directory in object path") from error
                raise
            finally:
                os.close(fd)

    @staticmethod
    def _open_file(parent_fd, name):
        try:
            fd = os.open(name, _FILE_FLAGS, dir_fd=parent_fd)
        except FileNotFoundError as error:
            raise ObjectMissing("Object not found") from error
        except OSError as error:
            if error.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise InvalidObjectKey("Symlink in object path") from error
            raise
        try:
            _private(os.fstat(fd))
            return fd
        except BaseException:
            os.close(fd)
            raise

    @staticmethod
    def _copy(source_fd, destination_fd, max_bytes):
        digest = hashlib.sha256()
        total = 0
        while chunk := os.read(source_fd, min(_CHUNK, max_bytes - total + 1)):
            total += len(chunk)
            if total > max_bytes:
                raise ObjectLimitExceeded("Object exceeds byte limit")
            digest.update(chunk)
            if destination_fd is not None:
                view = memoryview(chunk)
                while view:
                    written = os.write(destination_fd, view)
                    if written <= 0:
                        raise ObjectStoreError("Object write made no progress")
                    view = view[written:]
        return {"sha256": digest.hexdigest(), "size_bytes": total}

    def _metadata(self, parent_fd, name, key):
        fd = self._open_file(parent_fd, name)
        try:
            if os.fstat(fd).st_size > self.max_object_bytes:
                raise ObjectLimitExceeded("Object exceeds byte limit")
            return {"key": key, **self._copy(fd, None, self.max_object_bytes)}
        finally:
            os.close(fd)

    def stat(self, org_id, key):
        parts = self._parts(org_id, key)
        with self._directory(parts[:-1]) as parent_fd:
            return self._metadata(parent_fd, parts[-1], key)

    def put_file(self, org_id, key, source):
        parts = self._parts(org_id, key)
        source = Path(source).absolute()
        with _external_directory(source.parent) as source_parent:
            try:
                source_fd = os.open(source.name, _FILE_FLAGS, dir_fd=source_parent)
            except OSError as error:
                if error.errno == errno.ELOOP:
                    raise InvalidObjectKey("Symlink input is forbidden") from error
                raise
            try:
                info = os.fstat(source_fd)
                if not stat_module.S_ISREG(info.st_mode):
                    raise InvalidObjectKey("Input must be a regular file")
                if info.st_size > self.max_object_bytes:
                    raise ObjectLimitExceeded("Object exceeds byte limit")
                with self._directory(parts[:-1], create=True) as parent_fd:
                    temporary = ".part-" + uuid4().hex
                    target_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
                    try:
                        metadata = {"key": key, **self._copy(source_fd, target_fd, self.max_object_bytes)}
                        os.fsync(target_fd)
                        try:
                            os.link(temporary, parts[-1], src_dir_fd=parent_fd, dst_dir_fd=parent_fd, follow_symlinks=False)
                            os.fsync(parent_fd)
                        except FileExistsError:
                            existing = self._metadata(parent_fd, parts[-1], key)
                            if existing != metadata:
                                raise ObjectConflict("Immutable object already exists with different bytes")
                        return metadata
                    finally:
                        os.close(target_fd)
                        os.unlink(temporary, dir_fd=parent_fd)
                        os.fsync(parent_fd)
            finally:
                os.close(source_fd)

    def get_file(self, org_id, key, destination, *, max_bytes):
        parts = self._parts(org_id, key)
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        limit = min(max_bytes, self.max_object_bytes)
        destination = Path(destination).absolute()
        with self._directory(parts[:-1]) as parent_fd:
            source_fd = self._open_file(parent_fd, parts[-1])
            try:
                if os.fstat(source_fd).st_size > limit:
                    raise ObjectLimitExceeded("Object exceeds download byte limit")
                with _external_directory(destination.parent) as target_parent:
                    temporary = ".part-" + uuid4().hex
                    target_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=target_parent)
                    try:
                        metadata = {"key": key, **self._copy(source_fd, target_fd, limit)}
                        os.fsync(target_fd)
                        try:
                            os.link(temporary, destination.name, src_dir_fd=target_parent, dst_dir_fd=target_parent, follow_symlinks=False)
                        except FileExistsError as error:
                            raise ObjectConflict("Download destination already exists") from error
                        os.fsync(target_parent)
                        return metadata
                    finally:
                        os.close(target_fd)
                        os.unlink(temporary, dir_fd=target_parent)
                        os.fsync(target_parent)
            finally:
                os.close(source_fd)

    def list_keys(self, org_id, prefix=""):
        organization = _organization(org_id)
        if prefix:
            normalized = prefix[:-1] if prefix.endswith("/") else prefix
            if normalized != organization:
                self._parts(org_id, normalized)
        found = []

        def walk(fd, components):
            for name in sorted(os.listdir(fd)):
                if name.startswith(".part-"):
                    continue  # Uploads publish atomically; pending writes are private.
                if not _COMPONENT.fullmatch(name) or name in {".", ".."}:
                    raise InvalidObjectKey("Unexpected storage entry")
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat_module.S_ISDIR(info.st_mode):
                    child = os.open(name, _DIR_FLAGS, dir_fd=fd)
                    try:
                        _private(os.fstat(child), directory=True)
                        walk(child, components + [name])
                    finally:
                        os.close(child)
                else:
                    _private(info)
                    key = "/".join(components + [name])
                    if not prefix or key.startswith(prefix):
                        found.append(key)
        try:
            with self._directory([organization]) as directory:
                walk(directory, [organization])
        except ObjectMissing:
            return []
        return found

    def delete(self, org_id, key, expected_sha256=None):
        parts = self._parts(org_id, key)
        with self._directory(parts[:-1]) as parent_fd:
            metadata = self._metadata(parent_fd, parts[-1], key)
            if expected_sha256 is not None and metadata["sha256"] != expected_sha256:
                raise ObjectConflict("Deletion checksum precondition failed")
            os.unlink(parts[-1], dir_fd=parent_fd)
            os.fsync(parent_fd)

    def list_staging(self, org_id):
        """Report interrupted private upload fragments; never treat as objects."""
        organization = _organization(org_id)
        found = []
        def walk(fd, components):
            for name in sorted(os.listdir(fd)):
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat_module.S_ISDIR(info.st_mode):
                    child = os.open(name, _DIR_FLAGS, dir_fd=fd)
                    try:
                        _private(os.fstat(child), directory=True)
                        walk(child, components + [name])
                    finally:
                        os.close(child)
                elif name.startswith(".part-"):
                    _private(info)
                    found.append({"key": "/".join(components + [name]),
                                  "size_bytes": info.st_size, "modified_at": info.st_mtime})
                else:
                    _private(info)
        try:
            with self._directory([organization]) as fd:
                walk(fd, [organization])
        except ObjectMissing:
            return []
        return found

    def delete_staging(self, org_id, key, *, expected_size_bytes=None,
                       expected_modified_at=None):
        """Remove one observed fragment after caller proves its generation stale.

        This local maintenance API never accepts published names. The pipeline
        holds the job row lock and protects live/authoritative fences before
        invoking it. Optional metadata preconditions reject a changed fragment.
        """
        if not isinstance(key, str) or len(key) > 1024 or "/" not in key:
            raise InvalidObjectKey("Invalid staging fragment key")
        parent, name = key.rsplit("/", 1)
        if not re.fullmatch(r"\.part-[0-9a-f]{32}", name):
            raise InvalidObjectKey("Only generated staging fragment names may be removed")
        parts = self._parts(org_id, parent + "/fragment")[:-1]
        with self._directory(parts) as parent_fd:
            fd = self._open_file(parent_fd, name)
            try:
                info = os.fstat(fd)
                if (expected_size_bytes is not None and info.st_size != expected_size_bytes
                        or expected_modified_at is not None
                        and info.st_mtime != expected_modified_at):
                    raise ObjectConflict("Staging fragment changed since inventory")
                os.unlink(name, dir_fd=parent_fd)
                os.fsync(parent_fd)
            finally:
                os.close(fd)
