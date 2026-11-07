# G0 executed evidence, 2026-11-07

Source is a locally generated synthetic `testsrc2` video plus a `sine` audio track, with sample SRT. No external code, assets or API credentials were used. `ffmpeg` and `ffprobe` are installed in the host environment.

## Test command

`python3 -m unittest discover -s tests -v`

Result: 12 tests, ALL PASS (on the current local host). Tests cover: local tenant isolation, symlink escape, invalid identifiers, SHA, SQLite unique constraint, real 3-format renders with audio and captions, idempotency cache, conflicting requests, corrupted output reconciliation, and a 6-request concurrent-duplicate test verifying exactly one FFmpeg pass per preset.

## Runtime

- Python: 3.13.5
- Node (present but not used): 22.16.0
- FFmpeg: Debian 7.1.5-0+deb13u1, GPL-enabled (`--enable-gpl --enable-libx264 --enable-libass`)
- Local ffmpeg binary SHA-256: `e8a8d46f5225f3062cec7c07fb145d58ae73c603cb740dcd5bad34bfb54e455a`
- No Docker installed; no external PostgreSQL, storage, queues or cloud runtime involved.

## Caveats

- All smoke output dimensions are intentionally small to avoid misleading cost/performance estimates.
- CI config exists but cannot yet be run in GitHub because the proposed new repository was not found and repository creation is not available via the connected GitHub tool.
- Not a validated service-level isolated or production-ready multitenant system.
