"""G0 FFmpeg render spike with local SQLite idempotency and tenant boundaries.

Safety properties demonstrated locally:
- immutable input hash in request fingerprint
- no arbitrary shell command, URL downloads, or cross-tenant local inputs
- identical operation cannot start twice in a shared SQLite workspace
- lost output requires reconciliation; no implicit second render
- output objects plus manifest written to a staging directory before publish

Not a production security or distributed-queue implementation.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
import uuid
from pathlib import Path
from datetime import datetime, timezone

MAX_BYTES = 500 * 1024 * 1024  # G0 pilot limit, not a production limit
MAX_DURATION_SECONDS = 900
QUALITY = {
    "smoke": {"vertical": (270, 480), "square": (320, 320), "feed": (320, 400)},
    "production": {"vertical": (1080, 1920), "square": (1080, 1080), "feed": (1080, 1350)},
}
SAFE_TENANT = re.compile(r"^[a-z][a-z0-9_-]{0,48}$")
SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
SAFE_CAPTION = re.compile(r"^[A-Za-z0-9_.-]+\.srt$", re.I)


class ContractError(ValueError):
    pass


class ConflictError(ContractError):
    pass


class ReconciliationRequired(ContractError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def sha256_file(file: Path) -> str:
    h = hashlib.sha256()
    with file.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def require_tool(name: str):
    found = shutil.which(name)
    if not found:
        raise ContractError(f"Required local binary unavailable: {name}")
    return found


def validate_identifiers(tenant: str, key: str):
    if not SAFE_TENANT.fullmatch(tenant):
        raise ContractError("Invalid tenant identifier")
    if not SAFE_KEY.fullmatch(key):
        raise ContractError("Invalid idempotency key")


def input_in_tenant(workspace: Path, tenant: str, candidate: Path) -> Path:
    root = (workspace / "inputs" / tenant).resolve()
    if not root.is_dir():
        raise ContractError("Tenant input directory does not exist")
    try:
        source = candidate.resolve(strict=True)
    except (FileNotFoundError, OSError) as e:
        raise ContractError("Input does not exist") from e
    if not source.is_relative_to(root) or not source.is_file():
        raise ContractError("Cross-tenant or external inputs forbidden")
    if source.stat().st_size <= 0 or source.stat().st_size > MAX_BYTES:
        raise ContractError("Input is empty or exceeds G0 file limit")
    return source


def probe_video(source: Path) -> dict:
    cmd = [require_tool("ffprobe"), "-v", "error", "-show_format", "-show_streams", "-of", "json", str(source)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    if result.returncode != 0:
        raise ContractError("Invalid/unreadable media file")
    try:
        info = json.loads(result.stdout)
        duration = float(info.get("format", {}).get("duration", 0))
    except (ValueError, KeyError, TypeError) as e:
        raise ContractError("Unreadable media metadata") from e
    if not (0 < duration <= MAX_DURATION_SECONDS):
        raise ContractError("G0 duration must be >0 and <=900s")
    videos = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    if not videos:
        raise ContractError("No video stream")
    return {"duration_seconds": duration, "input_width": videos[0].get("width"), "input_height": videos[0].get("height"),
            "has_audio": any(s.get("codec_type") == "audio" for s in info.get("streams", []))}


def connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS render_jobs (
      job_id TEXT PRIMARY KEY,
      tenant TEXT NOT NULL,
      request_key TEXT NOT NULL,
      fingerprint TEXT NOT NULL,
      state TEXT NOT NULL CHECK(state IN ('running','completed','failed')),
      manifest_path TEXT,
      error_code TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      UNIQUE(tenant, request_key)
    )""")
    conn.commit()
    return conn


def job_status(workspace: Path, tenant: str, key: str) -> dict:
    validate_identifiers(tenant, key)
    conn = connection(workspace / ".local" / "jobs.sqlite3")
    row = conn.execute("SELECT * FROM render_jobs WHERE tenant=? AND request_key=?", (tenant, key)).fetchone()
    conn.close()
    if row is None:
        return {"state": "not_found", "tenant": tenant, "request_key": key}
    return {k: row[k] for k in ("job_id", "tenant", "request_key", "state", "error_code", "manifest_path")}


def _caption_filter(caption: Path | None) -> str:
    if caption is None:
        return ""
    if not SAFE_CAPTION.fullmatch(caption.name):
        raise ContractError("G0 captions require a safe .srt filename")
    return f",subtitles=filename={caption.name}"


def render_one(source: Path, out: Path, width: int, height: int, caption: Path | None, duration: float) -> None:
    """No shell, network input, filter injection or upscaling-dependent AI here."""
    # Pad rather than crop: the first technical gate prioritizes preserving source content.
    vf = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
          f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
          + _caption_filter(caption))
    cmd = [require_tool("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
           "-i", str(source), "-t", str(duration), "-map", "0:v:0", "-map", "0:a:0?",
           "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "27", "-pix_fmt", "yuv420p",
           "-r", "30", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)]
    result = subprocess.run(cmd, cwd=str(caption.parent if caption else source.parent),
                            capture_output=True, text=True, timeout=300, check=False)
    if result.returncode != 0:
        raise ContractError("FFmpeg render failed: " + result.stderr[-350:])
    if not out.exists() or out.stat().st_size <= 0:
        raise ContractError("Renderer produced no usable output")


def verify_cached_manifest(manifest_path: Path) -> dict:
    if not manifest_path.is_file():
        raise ReconciliationRequired("Manifest missing; do not rerender automatically")
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise ReconciliationRequired("Manifest is corrupt; manual reconciliation needed") from e
    for item in manifest.get("outputs", []):
        path = manifest_path.parent / item["file"]
        if not path.is_file() or sha256_file(path) != item["sha256"]:
            raise ReconciliationRequired("Artifact missing/mismatched; do not rerender automatically")
    return manifest


def run_job(workspace: Path, tenant: str, key: str, source_arg: Path,
            *, quality: str = "smoke", caption_arg: Path | None = None,
            duration_seconds: float | None = None) -> dict:
    validate_identifiers(tenant, key)
    if quality not in QUALITY:
        raise ContractError("Unknown quality preset")
    workspace = workspace.resolve()
    source = input_in_tenant(workspace, tenant, source_arg)
    caption = input_in_tenant(workspace, tenant, caption_arg) if caption_arg else None
    if caption and not SAFE_CAPTION.fullmatch(caption.name):
        raise ContractError("Caption file must be a safe .srt file")
    media = probe_video(source)
    duration = min(media["duration_seconds"], duration_seconds) if duration_seconds is not None else media["duration_seconds"]
    if not (0 < duration <= media["duration_seconds"]):
        raise ContractError("Invalid requested duration")
    fingerprint = hashlib.sha256(json.dumps({
        "source_sha256": sha256_file(source), "caption_sha256": sha256_file(caption) if caption else None,
        "quality": quality, "duration": duration, "formats": QUALITY[quality], "renderer_contract": "g0-v1"
    }, sort_keys=True).encode()).hexdigest()
    job_id = hashlib.sha256((tenant + "\0" + key).encode()).hexdigest()
    db = workspace / ".local" / "jobs.sqlite3"
    conn = connection(db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM render_jobs WHERE tenant=? AND request_key=?", (tenant, key)).fetchone()
        if row:
            if row["fingerprint"] != fingerprint:
                raise ConflictError("Same operation key, different request: rejected")
            conn.commit()
            if row["state"] == "completed":
                manifest = verify_cached_manifest(Path(row["manifest_path"]))
                return {"state": "completed", "cached": True, "manifest": manifest}
            if row["state"] == "running":
                return {"state": "running", "cached": False, "job_id": row["job_id"]}
            raise ReconciliationRequired("Failed operation; explicit human-controlled retry with a new key required")
        conn.execute("INSERT INTO render_jobs (job_id,tenant,request_key,fingerprint,state,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                     (job_id, tenant, key, fingerprint, "running", utc_now(), utc_now()))
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise
    conn.close()

    tenant_outputs = workspace / "outputs" / tenant
    final_dir = tenant_outputs / job_id
    staging_dir = tenant_outputs / "_staging" / (job_id + "." + uuid.uuid4().hex)
    staging_dir.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    try:
        if final_dir.exists():
            raise ReconciliationRequired("Destination exists unexpectedly; manual reconciliation needed")
        outputs = []
        for name, (w, h) in QUALITY[quality].items():
            filename = f"{name}.mp4"
            path = staging_dir / filename
            render_one(source, path, w, h, caption, duration)
            info = probe_video(path)
            if info["input_width"] != w or info["input_height"] != h:
                raise ContractError("Output has invalid dimensions")
            if media["has_audio"] and not info["has_audio"]:
                raise ContractError("Unexpected audio loss")
            outputs.append({"preset": name, "file": filename, "width": w, "height": h,
                            "size_bytes": path.stat().st_size, "sha256": sha256_file(path)})
        manifest = {"schema_version": 1, "stage": "G0-local-only", "job_id": job_id,
                    "tenant": tenant, "request_key": key, "fingerprint": fingerprint,
                    "input_sha256": sha256_file(source), "quality": quality, "duration_seconds": duration,
                    "elapsed_render_seconds": round(time.monotonic() - started, 3),
                    "caption_mode": "supplied-srt-burned" if caption else "none", "outputs": outputs}
        (staging_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        # Same tenant-output filesystem: directory rename is atomic.
        staging_dir.rename(final_dir)
        conn = connection(db)
        conn.execute("UPDATE render_jobs SET state='completed',manifest_path=?,updated_at=? WHERE job_id=? AND state='running'",
                     (str(final_dir / "manifest.json"), utc_now(), job_id))
        conn.commit()
        conn.close()
        return {"state": "completed", "cached": False, "manifest": manifest}
    except Exception as e:
        # Preserve existing successful output for manual reconciliation if DB commit failed.
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        conn = connection(db)
        conn.execute("UPDATE render_jobs SET state='failed',error_code=?,updated_at=? WHERE job_id=? AND state='running'",
                     (type(e).__name__, utc_now(), job_id))
        conn.commit()
        conn.close()
        raise
