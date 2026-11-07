"""CLI entrypoint: python -m engine.cli demo|render|status."""
import argparse
import json
import subprocess
import sys
from pathlib import Path
from .core import ContractError, require_tool, run_job, job_status


def demo(workspace: Path, tenant: str):
    from .core import SAFE_TENANT
    if not SAFE_TENANT.fullmatch(tenant):
        raise ContractError("Invalid tenant")
    dest = workspace / "inputs" / tenant
    dest.mkdir(parents=True, exist_ok=True)
    video = dest / "synthetic_demo.mp4"
    subtitle = dest / "synthetic_demo.srt"
    if not video.exists():
        # Generated synthetic footage: no client content, no copyrighted media.
        cmd = [require_tool("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
               "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24:duration=2.3",
               "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=2.3",
               "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video)]
        result = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=90)
        if result.returncode:
            raise ContractError("Could not generate demo: " + result.stderr[-300:])
    subtitle.write_text("1\n00:00:00,200 --> 00:00:01,800\nLetThemCook G0 - teste local\n", encoding="utf-8")
    return {"video": str(video), "caption": str(subtitle)}


def main():
    parser = argparse.ArgumentParser(description="LetThemCook G0 local deterministic rendering spike")
    sub = parser.add_subparsers(dest="command", required=True)
    demo_p = sub.add_parser("demo", help="Generate synthetic test media")
    demo_p.add_argument("--workspace", type=Path, default=Path("workspace"))
    demo_p.add_argument("--tenant", default="demo")
    render_p = sub.add_parser("render", help="Render 3 formats from a tenant-owned input")
    render_p.add_argument("--workspace", type=Path, default=Path("workspace"))
    render_p.add_argument("--tenant", required=True)
    render_p.add_argument("--key", required=True, help="Caller-provided stable operation identifier")
    render_p.add_argument("--input", type=Path, required=True)
    render_p.add_argument("--caption-srt", type=Path, default=None)
    render_p.add_argument("--quality", choices=["smoke", "production"], default="smoke")
    render_p.add_argument("--duration-seconds", type=float, default=None)
    status_p = sub.add_parser("status", help="Lookup one existing local operation")
    status_p.add_argument("--workspace", type=Path, default=Path("workspace"))
    status_p.add_argument("--tenant", required=True)
    status_p.add_argument("--key", required=True)
    args = parser.parse_args()
    try:
        if args.command == "demo":
            data = demo(args.workspace, args.tenant)
        elif args.command == "render":
            data = run_job(args.workspace, args.tenant, args.key, args.input,
                           caption_arg=args.caption_srt, quality=args.quality,
                           duration_seconds=args.duration_seconds)
        else:
            data = job_status(args.workspace, args.tenant, args.key)
        print(json.dumps(data, indent=2, sort_keys=True))
    except (ContractError, OSError, subprocess.TimeoutExpired) as e:
        print(json.dumps({"error": type(e).__name__, "detail": str(e)}), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
