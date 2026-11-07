"""G2 real-media executor on the unchanged G1 lease/outbox contracts.

Only synthetic, trusted-fixture media is authorized by this local proof. A
generation's immutable objects are published before the fenced PostgreSQL commit.
This deliberately permits orphaned objects after a crash; the database manifest
is the sole authority, and explicit reconciliation removes obsolete generations.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shutil
import stat
import tempfile
import time
from uuid import UUID

from .control_plane import StaleLease, _audit
from .core import QUALITY, ReconciliationRequired
from .media_control import MediaControlPlane
from .media_runtime import Limits, MediaError, inspect_media, render_profiles, validate_srt
from .object_store import ObjectLimitExceeded


def _uuid(value):
    text = str(value)
    if str(UUID(text)) != text:
        raise ValueError("expected canonical UUID")
    return text


def _private_directory(path):
    path = Path(path).absolute()
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in path.parts[1:]:
            if component in {"", ".", ".."}:
                raise ValueError("scratch paths must not traverse directories")
            try:
                os.mkdir(component, 0o700, dir_fd=fd)
            except FileExistsError:
                pass
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        info = os.fstat(fd)
    finally:
        os.close(fd)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077):
        raise ValueError("scratch directories must be owned and private")
    return path


class MediaWorker:
    def __init__(self, dsn, store, scratch_root, *, limits=None, lease_seconds=30,
                 fault_hook=None, cancel_event=None):
        self.control = MediaControlPlane(dsn)
        self.store = store
        self.scratch_root = _private_directory(scratch_root)
        self.limits = limits or Limits()
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 300:
            raise ValueError("lease_seconds must be an integer in [1,300]")
        self.lease_seconds = lease_seconds
        self.fault_hook = fault_hook
        self.cancel_event = cancel_event

    def _hook(self, stage, claim):
        if self.fault_hook is not None:
            self.fault_hook(stage, claim)

    def _job_scratch(self, claim):
        organization = _uuid(claim["organization_id"])
        job_id = _uuid(claim["id"])
        return _private_directory(_private_directory(self.scratch_root / organization) / job_id)

    def _metrics(self, claim, stage, started, read_bytes, written_bytes, error_code=None):
        self.control.record_metrics(
            claim, stage, max(0, round((time.monotonic() - started) * 1000)),
            read_bytes, written_bytes, error_code=error_code,
        )

    @staticmethod
    def _same_bytes(metadata, sha256, size):
        if metadata["sha256"] != sha256 or metadata["size_bytes"] != size:
            raise MediaError("checksum", "object checksum or size differs from immutable metadata")

    @staticmethod
    def _check_source(info, spec, duration_seconds):
        if (info["input_width"] != spec["width"] or info["input_height"] != spec["height"]
                or abs(info["duration_seconds"] * 1000 - spec["duration_ms"]) > 150
                or duration_seconds > info["duration_seconds"] + 0.05):
            raise MediaError("invalid_input", "probed media differs from trusted ingestion metadata")

    def _download(self, org, key, destination, expected_hash, expected_size, max_bytes):
        metadata = self.store.get_file(org, key, destination, max_bytes=max_bytes)
        self._same_bytes(metadata, expected_hash, expected_size)
        return metadata["size_bytes"]

    def _verify_manifest(self, claim, spec, directory, *, heartbeat=None):
        """Read back immutable published bytes, then inspect the actual videos."""
        manifest = claim["output_manifest"]
        try:
            self.control._validate_manifest(claim, manifest)
            if (manifest.get("media_digest") != spec["media_digest"]
                    or manifest.get("source_hash") != spec["sha256"]
                    or manifest.get("caption_hash") != spec.get("caption_sha256")):
                raise ValueError("manifest input identity differs")
            manifest_path = directory / "stored-manifest.json"
            downloaded = self.store.get_file(
                claim["organization_id"], manifest["generation"] + "/manifest.json",
                manifest_path, max_bytes=65536,
            )["size_bytes"]
            if json.loads(manifest_path.read_text(encoding="utf-8")) != manifest:
                raise ValueError("object manifest differs from authoritative database")
            expected = QUALITY[claim["request_payload"]["quality"]]
            if {item.get("preset") for item in manifest["outputs"]} != set(expected):
                raise ValueError("manifest does not contain all three required renditions")
            duration = claim["request_payload"]["duration_ms"] / 1000
            for index, output in enumerate(manifest["outputs"]):
                if heartbeat is not None:
                    heartbeat()
                target = directory / f"verified-{index}.mp4"
                downloaded += self._download(
                    claim["organization_id"], output["key"], target,
                    output["sha256"], output["size_bytes"], self.limits.max_output_bytes,
                )
                info = inspect_media(target, self.limits, cancel_event=self.cancel_event,
                                     heartbeat=heartbeat)
                width, height = expected[output["preset"]]
                if (info["input_width"] != width or info["input_height"] != height
                        or output.get("width") != width or output.get("height") != height
                        or abs(info["duration_seconds"] - duration) > 0.15
                        or abs(info["duration_seconds"] - output["duration_seconds"]) > 0.05
                        or info["has_audio"] != output["has_audio"]
                        or (manifest["source_has_audio"] and not info["has_audio"])):
                    raise ValueError("published output failed media verification")
            return downloaded
        except StaleLease:
            raise
        except Exception as exc:
            raise ReconciliationRequired(
                "Published media is missing, corrupt or inconsistent; never rerender a completed operation"
            ) from exc

    def _verify_completed(self, claim, spec):
        with tempfile.TemporaryDirectory(prefix=f'{claim["fence"]}-',
                                         dir=self._job_scratch(claim)) as scratch:
            self._verify_manifest(claim, spec, Path(scratch))
        return claim

    def run(self, job_id=None):
        """Run one named task or one due G2 job from the durable database sweep.

        Return a job row (completed, queued retry, or permanent failed), or None
        when no claim is due. A stale owner raises StaleLease. Corrupt completed
        results raise ReconciliationRequired and are never silently regenerated.
        BaseException is intentionally not caught: hard worker termination must
        leave evidence for the next fence and orphan reconciler.
        """
        spec = self.control.get_spec(job_id) if job_id is not None else None
        if job_id is not None and spec is None:
            raise ValueError("job is not a G2 media operation")
        claim = (self.control.claim_job(job_id, self.lease_seconds) if job_id is not None
                 else self.control.claim_next_media_job(self.lease_seconds))
        if claim is None:
            return None
        spec = spec or self.control.get_spec(claim["id"])
        if claim["state"] == "completed":
            return self._verify_completed(claim, spec)
        started = time.monotonic()
        next_renew = started + max(0.2, self.lease_seconds / 3)
        read_bytes = written_bytes = 0

        def heartbeat(force=False):
            nonlocal next_renew
            now = time.monotonic()
            if self.cancel_event is not None and self.cancel_event.is_set():
                raise MediaError("cancelled", "job cancellation requested")
            if now - started > self.limits.timeout_seconds:
                raise MediaError("timeout", "whole media job exceeded wall-clock deadline")
            if force or now >= next_renew:
                self.control.renew_lease(claim["id"], claim["lease_token"], claim["fence"],
                                         self.lease_seconds)
                next_renew = time.monotonic() + max(0.2, self.lease_seconds / 3)

        try:
            self._metrics(claim, "started", started, read_bytes, written_bytes)
            with tempfile.TemporaryDirectory(prefix=f'{claim["fence"]}-',
                                             dir=self._job_scratch(claim)) as scratch:
                scratch = Path(scratch)
                marker = scratch / "lease.json"
                marker.write_text(json.dumps({"job_id": str(claim["id"]), "fence": claim["fence"]}))
                marker.chmod(0o600)
                source = scratch / "source.mp4"
                self._hook("before_download", claim)
                heartbeat(force=True)
                read_bytes += self._download(
                    claim["organization_id"], spec["storage_key"], source,
                    spec["sha256"], spec["bytes"], self.limits.max_bytes,
                )
                caption = None
                if spec.get("caption_key"):
                    caption = scratch / "caption.srt"
                    read_bytes += self._download(
                        claim["organization_id"], spec["caption_key"], caption,
                        spec["caption_sha256"], spec["caption_bytes"], 65536,
                    )
                    validate_srt(caption)
                self._hook("download", claim)
                heartbeat()
                info = inspect_media(source, self.limits, cancel_event=self.cancel_event,
                                     heartbeat=heartbeat)
                duration = claim["request_payload"]["duration_ms"] / 1000
                self._check_source(info, spec, duration)
                self._metrics(claim, "downloaded", started, read_bytes, written_bytes)
                output_dir = scratch / "outputs"
                output_dir.mkdir(mode=0o700)
                self._hook("before_render", claim)
                rendered = render_profiles(
                    source, caption, output_dir, claim["request_payload"]["quality"], duration,
                    self.limits, cancel_event=self.cancel_event, heartbeat=heartbeat,
                )
                self._hook("after_render", claim)
                self._metrics(claim, "rendered", started, read_bytes, written_bytes)
                generation = f'{claim["organization_id"]}/{claim["id"]}/{claim["fence"]}'
                outputs = []
                for output in rendered:
                    heartbeat(force=True)
                    name = output["preset"]
                    key = f"{generation}/{name}.mp4"
                    metadata = self.store.put_file(claim["organization_id"], key,
                                                   output_dir / f"{name}.mp4")
                    self._same_bytes(metadata, output["sha256"], output["size_bytes"])
                    written_bytes += metadata["size_bytes"]
                    outputs.append({"key": key, "sha256": metadata["sha256"],
                                    "size_bytes": metadata["size_bytes"], "preset": name,
                                    "width": output["width"], "height": output["height"],
                                    "duration_seconds": output["duration_seconds"],
                                    "has_audio": output["has_audio"]})
                    self._metrics(claim, "publishing", started, read_bytes, written_bytes)
                    self._hook("after_object", claim)
                manifest = {
                    "schema_version": 2, "generation": generation, "outputs": outputs,
                    "media_digest": spec["media_digest"], "source_hash": spec["sha256"],
                    "caption_hash": spec.get("caption_sha256"),
                    "source_has_audio": info["has_audio"],
                    "caption_mode": "supplied-srt-burned" if caption else "none",
                    "stats": {"elapsed_ms": round((time.monotonic() - started) * 1000),
                              "bytes_read": read_bytes, "bytes_written": written_bytes,
                              "attempt": claim["attempt_count"],
                              "estimated_output_pixel_frames": math.ceil(duration * 30) * sum(
                                  width * height for width, height in
                                  QUALITY[claim["request_payload"]["quality"]].values()),
                              "cpu_seconds_limit_per_process": self.limits.cpu_seconds,
                              "estimate_model": "output_pixels_times_requested_frames_not_billing"},
                }
                manifest_file = scratch / "manifest.json"
                manifest_file.write_text(json.dumps(manifest, sort_keys=True, allow_nan=False),
                                         encoding="utf-8")
                manifest_file.chmod(0o600)
                heartbeat(force=True)
                written_bytes += self.store.put_file(
                    claim["organization_id"], generation + "/manifest.json", manifest_file,
                )["size_bytes"]
                self._hook("after_manifest", claim)
                verification = scratch / "verification"
                verification.mkdir(mode=0o700)
                read_bytes += self._verify_manifest(
                    {**claim, "output_manifest": manifest}, spec, verification, heartbeat=heartbeat,
                )
                self._metrics(claim, "published", started, read_bytes, written_bytes)
                self._hook("before_commit", claim)
                heartbeat(force=True)
                result = self.control.finish_job(claim["id"], claim["lease_token"],
                                                 claim["fence"], manifest)
                self._hook("after_commit", claim)
                self._metrics(claim, "completed", started, read_bytes, written_bytes)
                return result
        except StaleLease:
            # Never write failure state or metrics over the replacement owner.
            raise
        except Exception as exc:
            # A successful commit may have lost its client response. Reconcile
            # authoritative DB state before performing ANY retry transition.
            current = self.control.get_job(claim["id"])
            if current is not None and current["state"] == "completed":
                return self._verify_completed(current, spec)
            code = getattr(exc, "error_code", None) or ("limits" if isinstance(exc, ObjectLimitExceeded)
                                                       else "io_error" if isinstance(exc, OSError)
                                                       else "execution_error")
            if code not in {"invalid_input", "limits", "caption", "invalid_srt", "checksum",
                            "cancelled", "timeout", "ffmpeg", "io_error", "execution_error"}:
                code = "execution_error"
            permanent = code in {"invalid_input", "limits", "caption", "invalid_srt", "checksum", "cancelled"}
            self._metrics(claim, "rejected" if permanent else "retry", started,
                          read_bytes, written_bytes, error_code=code)
            if permanent:
                return self.control.reject_job(claim, code)
            return self.control.fail_job(claim["id"], claim["lease_token"], claim["fence"],
                                         "timeout" if code == "timeout" else "crash")

    def reconcile_orphans(self, organization_id, job_id, *, delete=False):
        """Internal operator action; report/remove only obsolete fenced output.

        The job row lock serializes the authority check with claims/completion.
        The current generation is protected unless the job is terminal failed.
        It is safe to call this repeatedly; a still-running stale process might
        later publish more obsolete files, which the next sweep will find.
        """
        org, job = _uuid(organization_id), _uuid(job_id)
        report = {"objects": [], "staging": [], "scratch": [], "deleted": bool(delete)}
        with self.control.transaction() as db:
            row = db.execute("SELECT * FROM public.render_jobs WHERE id=%s AND organization_id=%s FOR UPDATE",
                             (job, org)).fetchone()
            if row is None:
                raise ValueError("job is not available in this organization")

            def obsolete(fence):
                return fence < row["fence"] or (fence == row["fence"] and row["state"] == "failed")

            prefix = f"{org}/{job}/"
            for key in self.store.list_keys(org, prefix):
                parts = key[len(prefix):].split("/")
                if len(parts) != 2 or not parts[0].isdigit() or not obsolete(int(parts[0])):
                    continue
                report["objects"].append(key)
                if delete:
                    self.store.delete(org, key)
            if hasattr(self.store, "list_staging"):
                for item in self.store.list_staging(org):
                    key = item["key"]
                    if not key.startswith(prefix):
                        continue
                    parts = key[len(prefix):].split("/")
                    if len(parts) != 2 or not parts[0].isdigit() or not obsolete(int(parts[0])):
                        continue
                    report["staging"].append(key)
                    if delete:
                        self.store.delete_staging(
                            org, key, expected_size_bytes=item["size_bytes"],
                            expected_modified_at=item["modified_at"],
                        )
            parent = self.scratch_root / org / job
            if parent.exists():
                _private_directory(_private_directory(self.scratch_root / org) / job)
                for candidate in parent.iterdir():
                    fence = candidate.name.split("-", 1)[0]
                    if (not fence.isdigit() or not obsolete(int(fence))
                            or candidate.is_symlink() or not candidate.is_dir()):
                        continue
                    report["scratch"].append(str(candidate.relative_to(self.scratch_root)))
                    if delete:
                        shutil.rmtree(candidate)
            _audit(db, row, "media_orphans_deleted" if delete else "media_orphans_detected",
                   {"objects": report["objects"], "staging": report["staging"],
                    "scratch": report["scratch"]})
        return report
