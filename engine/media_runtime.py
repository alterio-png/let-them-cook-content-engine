"""Bounded G2 execution of the unchanged G0 FFmpeg scale/pad/SRT recipe.

Only local MP4/H.264 with optional AAC audio and plain supplied SRT are accepted.
Native processes have wall/CPU/memory/file limits and are killed and reaped on
timeout, cancellation or lost lease. This is a local synthetic-media sandbox,
not production seccomp/container isolation or commercial codec clearance.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time

from .core import QUALITY, require_tool, sha256_file


@dataclass(frozen=True)
class Limits:
    max_bytes: int = 524288000
    max_duration_seconds: float = 900
    max_pixels: int = 8294400
    max_frames: int = 27000
    timeout_seconds: float = 120
    max_output_bytes: int = 134217728
    memory_bytes: int = 1073741824
    cpu_seconds: int = 120

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"invalid media limit: {name}")


class MediaError(RuntimeError):
    def __init__(self, error_code, message="media processing failed"):
        self.error_code = error_code
        super().__init__(message)


class MediaTimeout(MediaError):
    def __init__(self, message="media wall-time limit exceeded"):
        super().__init__("timeout", message)


class MediaCancelled(MediaError):
    def __init__(self, message="media execution cancelled"):
        super().__init__("cancelled", message)


def _check(cancel_event, heartbeat):
    if cancel_event is not None and cancel_event.is_set():
        raise MediaCancelled()
    if heartbeat is not None:
        heartbeat()


def _kill(process):
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait()


def _run(binary, arguments, limits, *, cwd=None, cancel_event=None, heartbeat=None,
         timeout=None, error_code="ffmpeg"):
    """Only fixed internal FFmpeg/probe invocations reach this function."""
    _check(cancel_event, heartbeat)
    seconds = limits.timeout_seconds if timeout is None else min(timeout, limits.timeout_seconds)
    if seconds <= 0:
        raise MediaTimeout()
    command = [sys.executable, str(Path(__file__).with_name("media_sandbox.py")),
               str(os.getpid()), str(math.ceil(limits.cpu_seconds)), str(int(limits.memory_bytes)),
               str(int(limits.max_output_bytes)), require_tool(binary), *arguments]
    started = time.monotonic()
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout,
                                   stderr=stderr, cwd=cwd, shell=False, start_new_session=True)
        try:
            while process.poll() is None:
                _check(cancel_event, heartbeat)
                remaining = seconds - (time.monotonic() - started)
                if remaining <= 0:
                    raise MediaTimeout()
                try:
                    process.wait(timeout=min(0.1, remaining))
                except subprocess.TimeoutExpired:
                    pass
            _check(cancel_event, heartbeat)
            if process.returncode != 0:
                # Do not expose native stderr paths or untrusted input content.
                raise MediaError(error_code, f"{binary} rejected media (exit {process.returncode})")
            stdout.seek(0)
            result = stdout.read(1048577)
            if len(result) > 1048576:
                raise MediaError("limits", "native metadata exceeds 1MiB")
            return result
        finally:
            _kill(process)


def _regular(path, max_bytes):
    path = Path(path)
    try:
        info = path.lstat()
    except OSError as exc:
        raise MediaError("invalid_input", "media file is unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise MediaError("invalid_input", "media must be a regular private file")
    if not 0 < info.st_size <= max_bytes:
        raise MediaError("limits", "media byte limit exceeded or empty file")
    return path.absolute(), info.st_size


def inspect_media(path, limits=Limits(), *, cancel_event=None, heartbeat=None):
    source, size = _regular(path, limits.max_bytes)
    with source.open("rb") as handle:
        header = handle.read(16)
    if len(header) < 16 or header[4:8] != b"ftyp":
        raise MediaError("invalid_input", "only local ISO MP4 media is accepted")
    result = _run("ffprobe", ["-v", "error", "-protocol_whitelist", "file,pipe",
                  "-f", "mov", "-enable_drefs", "0", "-use_absolute_path", "0",
                  "-threads", "1", "-show_format", "-show_streams", "-of", "json",
                  str(source)], limits, cancel_event=cancel_event, heartbeat=heartbeat,
                  timeout=30, error_code="invalid_input")
    try:
        info = json.loads(result)
        streams = info["streams"]
        videos = [stream for stream in streams if stream["codec_type"] == "video"]
        audios = [stream for stream in streams if stream["codec_type"] == "audio"]
        if len(videos) != 1 or len(audios) > 1 or len(streams) != len(videos) + len(audios):
            raise ValueError("unsupported tracks")
        video = videos[0]
        duration = float(info["format"]["duration"])
        width, height = int(video["width"]), int(video["height"])
        numerator, denominator = (int(value) for value in video["avg_frame_rate"].split("/"))
        fps = numerator / denominator
        frames = max(int(video.get("nb_frames", 0)), math.ceil(duration * fps))
        if not all(math.isfinite(value) for value in (duration, fps)):
            raise ValueError("nonfinite metadata")
        if (duration <= 0 or duration > limits.max_duration_seconds or width <= 0 or height <= 0
                or width * height > limits.max_pixels or not 0 < fps <= 60 or frames > limits.max_frames):
            raise MediaError("limits", "media duration, resolution or frame count limit exceeded")
        if video.get("codec_name") != "h264" or (audios and audios[0].get("codec_name") != "aac"):
            raise ValueError("unsupported codec")
        if audios and (int(audios[0].get("channels", 0)) not in (1, 2)
                       or not 0 < int(audios[0].get("sample_rate", 0)) <= 48000):
            raise ValueError("unsupported audio")
        if "mp4" not in info["format"]["format_name"].split(","):
            raise ValueError("unsupported container")
    except (KeyError, ValueError, TypeError, ZeroDivisionError, OverflowError) as exc:
        raise MediaError("invalid_input", "invalid or unsupported MP4 metadata") from exc
    return {"duration_seconds": duration, "input_width": width, "input_height": height,
            "has_audio": bool(audios), "video_codec": video["codec_name"],
            "audio_codec": audios[0]["codec_name"] if audios else None,
            "frames": frames, "fps": fps, "size_bytes": size}


_TIMESTAMP = re.compile(r"^(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})$")


def validate_srt(path, max_bytes=65536):
    source, size = _regular(path, max_bytes)
    try:
        raw = source.read_bytes()
        text = raw.decode("utf-8-sig").replace("\r\n", "\n")
        blocks = text.strip().split("\n\n")
        if not 1 <= len(blocks) <= 500:
            raise ValueError("cue limit")
        for block in blocks:
            lines = block.split("\n")
            if not 3 <= len(lines) <= 12 or not lines[0].isdigit():
                raise ValueError("cue shape")
            match = _TIMESTAMP.fullmatch(lines[1])
            if not match:
                raise ValueError("cue time")
            values = [int(value) for value in match.groups()]
            if any(values[index] >= 60 for index in (1, 2, 5, 6)):
                raise ValueError("time range")
            start = values[0] * 3600 + values[1] * 60 + values[2] + values[3] / 1000
            end = values[4] * 3600 + values[5] * 60 + values[6] + values[7] / 1000
            if not 0 <= start < end <= 900:
                raise ValueError("cue duration")
            for line in lines[2:]:
                if (not line.strip() or len(line) > 200 or any(char in line for char in "<>{}\\")
                        or any(ord(char) < 32 and char != "\t" for char in line)):
                    raise ValueError("only bounded plain captions")
    except (UnicodeError, ValueError) as exc:
        raise MediaError("invalid_input", "invalid plain UTF-8 SRT captions") from exc
    return {"sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": size, "cues": len(blocks)}


def render_profiles(source, caption, output_dir, quality, duration_seconds, limits=Limits(),
                    cancel_event=None, heartbeat=None):
    started = time.monotonic()

    def task_heartbeat():
        if time.monotonic() - started >= limits.timeout_seconds:
            raise MediaTimeout()
        if heartbeat is not None:
            heartbeat()

    source = Path(source).absolute()
    if quality not in QUALITY:
        raise MediaError("invalid_input", "unknown quality profile")
    media = inspect_media(source, limits, cancel_event=cancel_event, heartbeat=task_heartbeat)
    if (isinstance(duration_seconds, bool) or not isinstance(duration_seconds, (float, int))
            or not math.isfinite(duration_seconds) or not 0 < duration_seconds <= media["duration_seconds"]):
        raise MediaError("invalid_input", "invalid requested duration")
    if any(width * height > limits.max_pixels for width, height in QUALITY[quality].values()):
        raise MediaError("limits", "output resolution exceeds limit")
    if math.ceil(duration_seconds * 30) > limits.max_frames:
        raise MediaError("limits", "output frame count exceeds limit")
    output_dir = Path(output_dir).absolute()
    if output_dir.is_symlink():
        raise MediaError("invalid_input", "output scratch cannot be a symlink")
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise MediaError("invalid_input", "output scratch must be empty")
    os.chmod(output_dir, 0o700)
    created = []
    try:
        if caption is not None:
            validate_srt(caption)
            local_caption = output_dir / "captions.srt"
            with local_caption.open("xb") as handle:
                handle.write(Path(caption).read_bytes())
            os.chmod(local_caption, 0o600)
            created.append(local_caption)
        results = []
        total_bytes = 0
        for name, (width, height) in QUALITY[quality].items():
            _check(cancel_event, task_heartbeat)
            out = output_dir / f"{name}.mp4"
            created.append(out)
            vf = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                  f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
                  + (",subtitles=filename=captions.srt" if caption is not None else ""))
            command = ["-nostdin", "-hide_banner", "-loglevel", "error", "-n",
                       "-protocol_whitelist", "file,pipe", "-f", "mov", "-enable_drefs", "0",
                       "-use_absolute_path", "0", "-threads", "1", "-i", str(source),
                       "-t", str(duration_seconds), "-map", "0:v:0", "-map", "0:a:0?",
                       "-vf", vf, "-filter_threads", "1", "-c:v", "libx264", "-threads", "1",
                       "-preset", "veryfast", "-crf", "27", "-pix_fmt", "yuv420p", "-r", "30",
                       "-c:a", "aac", "-b:a", "128k", "-map_metadata", "-1",
                       "-movflags", "+faststart", str(out)]
            remaining = limits.timeout_seconds - (time.monotonic() - started)
            _run("ffmpeg", command, limits, cwd=output_dir, cancel_event=cancel_event,
                 heartbeat=task_heartbeat, timeout=remaining)
            os.chmod(out, 0o600)
            verified = inspect_media(out, limits, cancel_event=cancel_event, heartbeat=task_heartbeat)
            size = out.stat().st_size
            total_bytes += size
            if (verified["input_width"] != width or verified["input_height"] != height
                    or abs(verified["duration_seconds"] - duration_seconds) > 0.15
                    or verified["has_audio"] != media["has_audio"] or total_bytes > limits.max_output_bytes):
                raise MediaError("ffmpeg", "rendered media failed output verification")
            results.append({"name": name, "preset": name, "file": out.name,
                            "sha256": sha256_file(out), "size_bytes": size, "width": width,
                            "height": height, "duration_seconds": verified["duration_seconds"],
                            "has_audio": verified["has_audio"]})
        if caption is not None:
            (output_dir / "captions.srt").unlink()
        _check(cancel_event, task_heartbeat)
        return results
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        raise
