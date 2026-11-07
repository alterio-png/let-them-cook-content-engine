"""Native FFmpeg/probe tests on locally generated video; no mocked encoding."""
import os
from pathlib import Path
import signal
import struct
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from engine.cli import demo
from engine.core import QUALITY, sha256_file
from engine.media_runtime import (
    Limits, MediaCancelled, MediaError, MediaTimeout, inspect_media,
    render_profiles, validate_srt,
)


class MediaRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        generated = demo(cls.root, "native")
        cls.source = Path(generated["video"])
        cls.caption = Path(generated["caption"])

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.out = Path(self.scratch.name) / "out"

    def render(self, caption=True, limits=Limits(), **kwargs):
        return render_profiles(self.source, self.caption if caption else None,
                               self.out, "smoke", 1.2, limits, **kwargs)

    def test_actual_three_profiles_match_bytes_dimensions_duration_and_audio(self):
        heartbeats = []
        outputs = self.render(heartbeat=lambda: heartbeats.append(time.monotonic()))
        self.assertTrue(heartbeats)
        self.assertEqual(set(QUALITY["smoke"]), {output["name"] for output in outputs})
        for output in outputs:
            path = self.out / output["file"]
            media = inspect_media(path)
            self.assertEqual(QUALITY["smoke"][output["name"]],
                             (media["input_width"], media["input_height"]))
            self.assertEqual(output["sha256"], sha256_file(path))
            self.assertEqual(output["size_bytes"], path.stat().st_size)
            self.assertAlmostEqual(1.2, media["duration_seconds"], delta=0.15)
            self.assertTrue(media["has_audio"])
            self.assertEqual(0o600, path.stat().st_mode & 0o777)
            pcm = subprocess.run(
                ["ffmpeg", "-v", "error", "-threads", "1", "-i", str(path),
                 "-t", "0.8", "-vn", "-ac", "1", "-ar", "44100", "-f", "s16le", "pipe:1"],
                check=True, capture_output=True, timeout=10,
            ).stdout
            samples = struct.unpack(f"<{len(pcm) // 2}h", pcm)
            self.assertGreater(sum(sample * sample for sample in samples) / len(samples), 100000)
            crossings = sum(left <= 0 < right for left, right in zip(samples, samples[1:]))
            self.assertAlmostEqual(440, crossings / (len(samples) / 44100), delta=5)
        self.assertEqual(0o700, self.out.stat().st_mode & 0o777)
        self.assertFalse((self.out / "captions.srt").exists())

    def test_caption_pixels_differ_from_real_uncaptioned_decode(self):
        self.render()
        plain = Path(self.scratch.name) / "plain"
        render_profiles(self.source, None, plain, "smoke", 1.2)

        def frame(path):
            return subprocess.run(["ffmpeg", "-v", "error", "-threads", "1", "-ss", "0.6",
                                   "-i", str(path), "-frames:v", "1", "-f", "rawvideo",
                                   "-pix_fmt", "gray", "-threads", "1", "pipe:1"],
                                  check=True, capture_output=True, timeout=10).stdout

        burned = frame(self.out / "square.mp4")
        uncaptioned = frame(plain / "square.mp4")
        self.assertEqual(320 * 320, len(burned))
        # More than a checksum assertion: decoded luminance near the caption
        # region changes, proving supplied text affected actual output pixels.
        bottom_start = 320 * 250
        difference = sum(abs(a - b) for a, b in zip(burned[bottom_start:], uncaptioned[bottom_start:]))
        self.assertGreater(difference, 1000)

    def test_repeated_native_render_produces_equal_hashes(self):
        first = self.render()
        again = Path(self.scratch.name) / "again"
        second = render_profiles(self.source, self.caption, again, "smoke", 1.2)
        self.assertEqual([row["sha256"] for row in first], [row["sha256"] for row in second])

    def test_invalid_mp4_and_real_media_caps_are_rejected(self):
        invalid = Path(self.scratch.name) / "invalid.mp4"
        invalid.write_bytes(b"not an MP4")
        with self.assertRaises(MediaError) as caught:
            inspect_media(invalid)
        self.assertEqual("invalid_input", caught.exception.error_code)
        for limits in (Limits(max_bytes=10), Limits(max_duration_seconds=0.5),
                       Limits(max_pixels=100), Limits(max_frames=2)):
            with self.subTest(limits=limits), self.assertRaises(MediaError) as caught:
                inspect_media(self.source, limits)
            self.assertEqual("limits", caught.exception.error_code)

    def test_plain_srt_validation_rejects_markup_malformed_and_symlinks(self):
        self.assertEqual(1, validate_srt(self.caption)["cues"])
        invalid = Path(self.scratch.name) / "caption.srt"
        for text in ("1\n00:00:00,000 --> 00:00:01,000\n{\\an8}override\n",
                     "1\n00:00:00,000 --> 00:00:01,000\n<b>markup</b>\n",
                     "1\n00:00:02,000 --> 00:00:01,000\nbackwards\n", "garbage"):
            invalid.write_text(text)
            with self.subTest(text=text), self.assertRaises(MediaError):
                validate_srt(invalid)
        invalid.unlink()
        invalid.symlink_to(self.caption)
        with self.assertRaises(MediaError):
            validate_srt(invalid)

    def test_output_caps_and_failing_native_encoder_leave_no_partial_files(self):
        with self.assertRaises(MediaError):
            self.render(limits=Limits(max_output_bytes=8192))
        self.assertFalse(self.out.exists() and any(self.out.iterdir()))
        with self.assertRaises(MediaError) as caught:
            self.render(limits=Limits(max_pixels=100))
        self.assertEqual("limits", caught.exception.error_code)

    def test_actual_timeout_kills_and_reaps_native_process(self):
        processes = []
        real_popen = subprocess.Popen

        def launch(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            # Suspend a real native launcher so the wall-time path must kill
            # a live process rather than relying on machine speed.
            os.kill(process.pid, signal.SIGSTOP)
            return process

        with patch("engine.media_runtime.subprocess.Popen", side_effect=launch):
            with self.assertRaises(MediaTimeout):
                self.render(limits=Limits(timeout_seconds=0.1))
        self.assertTrue(processes)
        for process in processes:
            self.assertIsNotNone(process.returncode)
            with self.assertRaises(ProcessLookupError):
                os.kill(process.pid, 0)
        self.assertFalse(self.out.exists() and any(self.out.iterdir()))

    def test_cancel_and_lost_heartbeat_kill_actual_subprocess(self):
        real_popen = subprocess.Popen
        processes = []
        cancel = threading.Event()

        def launch(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            cancel.set()
            return process

        with patch("engine.media_runtime.subprocess.Popen", side_effect=launch):
            with self.assertRaises(MediaCancelled):
                self.render(cancel_event=cancel)
        self.assertTrue(processes)
        self.assertTrue(all(process.returncode is not None for process in processes))
        cancel.clear()
        calls = []

        def lost_lease():
            calls.append(True)
            if len(calls) >= 2:
                raise RuntimeError("synthetic lost lease")

        processes.clear()

        def observe(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process

        with patch("engine.media_runtime.subprocess.Popen", side_effect=observe):
            with self.assertRaisesRegex(RuntimeError, "synthetic lost lease"):
                self.render(heartbeat=lost_lease)
        self.assertTrue(processes)
        self.assertTrue(all(process.returncode is not None for process in processes))

    def test_symlink_video_and_nonempty_output_are_rejected(self):
        linked = Path(self.scratch.name) / "linked.mp4"
        linked.symlink_to(self.source)
        with self.assertRaises(MediaError):
            inspect_media(linked)
        self.out.mkdir()
        existing = self.out / "do-not-touch"
        existing.write_bytes(b"preserved")
        with self.assertRaises(MediaError):
            self.render()
        self.assertEqual(b"preserved", existing.read_bytes())


if __name__ == "__main__":
    unittest.main()
