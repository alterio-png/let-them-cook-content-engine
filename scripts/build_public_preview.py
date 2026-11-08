#!/usr/bin/env python3
"""Build a static, synthetic-only preview from the exact PR16 evidence allowlist."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "5d6e3da15b88edde79b67d71c089656cfa222cda"
UI = ROOT / "public_preview"
EVIDENCE = "evidence/g3br0/2026-11-08/browser/"


def archived(name):
    return subprocess.check_output(["git", "show", f"{SOURCE}:{EVIDENCE}{name}"], cwd=ROOT)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    manifest_bytes = archived("manifest.json")
    manifest = json.loads(manifest_bytes)
    assert {o["preset"] for o in manifest["outputs"]} == {"vertical", "square", "feed"}
    expected_dimensions = {"vertical": (270, 480), "square": (320, 320), "feed": (320, 400)}
    media = {}
    for output in manifest["outputs"]:
        preset = output["preset"]
        assert (output["width"], output["height"]) == expected_dimensions[preset]
        assert output["duration_seconds"] == 2.3 and output["has_audio"] is True
        data = archived(preset + ".mp4")
        assert len(data) == output["size_bytes"] < 1048576
        assert digest(data) == output["sha256"], f"Archived media integrity failed: {preset}"
        media[preset] = data

    # Generated output is never committed. Never copy the repo, env files or runtime.
    dist = UI / "dist"
    if dist.exists():
        shutil.rmtree(dist)
    (dist / "media").mkdir(parents=True)
    (dist / "media" / "manifest.json").write_bytes(manifest_bytes)
    for preset, data in media.items():
        destination = dist / "media" / (preset + ".mp4")
        destination.write_bytes(data)
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(destination),
                        "-frames:v", "1", "-q:v", "3", str(dist / "media" / (preset + ".jpg"))],
                       check=True, timeout=15)
    html = (UI / "index.html").read_text()
    for name in ("app.js", "styles.css"):
        data = (UI / name).read_bytes()
        stem, extension = name.rsplit(".", 1)
        filename = f"{stem}.{digest(data)[:12]}.{extension}"
        (dist / filename).write_bytes(data)
        html = html.replace(f"./{name}", f"./{filename}")
    (dist / "index.html").write_text(html)
    for name in ("_headers", "robots.txt", "icon.svg", "star.svg"):
        shutil.copyfile(UI / name, dist / name)
    files = {str(p.relative_to(dist)): digest(p.read_bytes()) for p in sorted(dist.rglob("*")) if p.is_file()}
    release = {
        "product": "LetThemCook Content Engine", "mode": "public-synthetic-artifact-demo",
        "pipeline_source_commit": SOURCE,
        "frontend_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "frontend_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)),
        "backend_connected": False, "new_uploads": False, "new_renders": False,
        "media": "Archived real outputs from PR #16, 2.3 s synthetic fixture, smoke resolution",
        "files_sha256": files,
    }
    (dist / "release.json").write_text(json.dumps(release, indent=2) + "\n")
    print(f"Static preview built: {len(files) + 1} files, synthetic media hashes PASS")
    print(dist)


if __name__ == "__main__":
    main()
