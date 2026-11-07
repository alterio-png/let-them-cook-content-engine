"""Trusted local ingestion fixture: verified bytes become an immutable bundle.

No HTTP endpoint/JWT verifier. Connection is owned by a trusted importer, never
an authenticated end-user DSN. Upload-before-commit failure can leave private
unreferenced inputs; inventory_inputs reports them for explicit reconciliation.
"""
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from .media_runtime import Limits, MediaError, inspect_media, validate_srt


def _snapshot(path, destination, max_bytes):
    """Bound the actual opened regular file, including caller-side mutations."""
    import os
    import stat
    path = Path(path)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise MediaError("invalid_input", "source is not available as a regular local file") from exc
    with os.fdopen(descriptor, "rb") as inp:
        info = os.fstat(inp.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= max_bytes:
            raise MediaError("limits", "input must be a bounded regular file")
        with destination.open("xb") as out:
            total = 0
            while chunk := inp.read(1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise MediaError("limits", "input exceeds byte cap")
                out.write(chunk)
        destination.chmod(0o600)


def ingest_media(connection, store, org_id, actor_id, video, caption=None,
                 rights_confirmed=False, limits=None):
    limits = limits or Limits()
    member = connection.execute(
        """SELECT role FROM public.memberships WHERE organization_id=%s AND user_id=%s
           AND role IN ('owner','admin','editor') FOR SHARE""", (org_id, actor_id)
    ).fetchone()
    if not member or rights_confirmed is not True:
        raise PermissionError("authorized membership and confirmed media rights required")
    # Snapshot into private scratch so ingestion never trusts caller-side hashes.
    import tempfile
    import os
    with tempfile.TemporaryDirectory(prefix="ltc-ingest-") as name:
        scratch = Path(name)
        os.chmod(scratch, 0o700)
        source = scratch / "source.mp4"
        _snapshot(video, source, limits.max_bytes)
        info = inspect_media(source, limits)
        cap = None
        if caption is not None:
            cap = scratch / "caption.srt"
            _snapshot(caption, cap, 65536)
            validate_srt(cap)
        asset_id = uuid4()
        key = f"{org_id}/assets/{asset_id}/source.mp4"
        stored = store.put_file(org_id, key, source)
        caption_info = store.put_file(org_id, f"{org_id}/assets/{asset_id}/caption.srt", cap) if cap else None
        binding = {"version": "g2-v1", "source_sha256": stored["sha256"],
                   "source_bytes": stored["size_bytes"], "caption": caption_info}
        digest = hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        row = connection.execute(
            """INSERT INTO public.assets(id,organization_id,uploader_id,storage_key,sha256,bytes,
               duration_ms,width,height,rights_confirmed) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,true)
               RETURNING *""",
            (asset_id, org_id, actor_id, key, stored["sha256"], stored["size_bytes"],
             round(info["duration_seconds"] * 1000), info["input_width"], info["input_height"])
        ).fetchone()
        connection.execute(
            """INSERT INTO public.media_bindings(asset_id,organization_id,caption_key,caption_sha256,
               caption_bytes,binding_digest) VALUES(%s,%s,%s,%s,%s,%s)""",
            (asset_id, org_id, caption_info["key"] if caption_info else None,
             caption_info["sha256"] if caption_info else None,
             caption_info["size_bytes"] if caption_info else None, digest)
        )
        return row


def inventory_inputs(connection, store, org_id):
    """Report private unreferenced objects; never auto-delete concurrent ingestion."""
    referenced = set()
    for row in connection.execute("SELECT storage_key FROM public.assets WHERE organization_id=%s", (org_id,)):
        referenced.add(row["storage_key"])
    for row in connection.execute("SELECT caption_key FROM public.media_bindings WHERE organization_id=%s", (org_id,)):
        if row["caption_key"]:
            referenced.add(row["caption_key"])
    return [key for key in store.list_keys(org_id, f"{org_id}/assets/") if key not in referenced]
