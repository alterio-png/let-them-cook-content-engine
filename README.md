# LetThemCook Content Engine

**G0 Technical Spike, 2026-11-07. Not production ready.**

A separate, intentionally small foundation for a multitenant video-production SaaS. No packages have been cloned from third-party candidate repositories. This bundle includes a working local FFmpeg render test, database-contract draft, security gates and documented architecture.

## What works right now

- Generate synthetic source video and sample SRT subtitles, with no customer footage.
- Render the same video into **9:16**, **1:1**, and **4:5** MP4 variants, with original audio preserved.
- Burn a provided SRT subtitle file. **No AI transcription, smart reframing or social posting yet.**
- Inspect duration, streams, dimensions and SHA-256 of all outputs.
- Tenant-local path validation: cannot render media from another tenant's local input folder.
- Stable idempotency key with a SQLite `UNIQUE(tenant, request_key)` constraint.
- No duplicate renders for concurrent identical operations in the same SQLite workspace.
- Conflicting reuse of a key is rejected. Missing/corrupt output is not silently regenerated.

These assertions are backed by G0 local tests. **SQLite is only a local proof:** production requires PostgreSQL, a distributed claim/lease mechanism and an authoritative delivery state.

## Quick start (local only)

Requirements: Python 3.11+; `ffmpeg` with `libx264`, AAC and `libass` subtitles support; `ffprobe`.

```bash
python3 -m engine.cli demo --workspace workspace --tenant demo
python3 -m engine.cli render \
  --workspace workspace --tenant demo --key first-render-001 \
  --input workspace/inputs/demo/synthetic_demo.mp4 \
  --caption-srt workspace/inputs/demo/synthetic_demo.srt \
  --quality smoke --duration-seconds 1.2
python3 -m engine.cli status --workspace workspace --tenant demo --key first-render-001
python3 -m unittest discover -s tests -v
```

Output files are placed in `workspace/outputs/demo/<job-id>/{vertical,square,feed}.mp4`, together with a hash manifest. Use `--quality production` for 1080-based dimensions, but G0 tests exercise only the small smoke presets. Do not use production quality on large untrusted files without sandbox, quotas and time limits.

## Boundaries and intended target architecture

```text
Web app (future Next.js)
  -> authorized backend API (future)
  -> PostgreSQL: organizations, assets, immutable operations (future)
  -> outbox + Cloud Tasks / orchestrator (future)
  -> isolated Cloud Run CPU/GPU worker (future)
  -> object storage (GCS/R2 decision pending)
  -> manual creative review (future)
  -> official social API adapters (future)
```

Only the local FFmpeg worker and its tests exist in this G0 package. The architecture above is a plan, not implemented cloud infrastructure. No credentials, customer data, third-party downloads, deployments or paid resources.

## G0 checklist

- [x] Candidate GitHub repositories verified at current upstream HEAD; recorded in `docs/REPO_AUDIT.md`.
- [x] Render three formats with supplied captions and audio in a controlled local test.
- [x] Tenant-local access and idempotency contracts exercised in the test suite.
- [x] Cloud architecture documented with failure model.
- [ ] Create **private** GitHub repository `alterio-png/let-them-cook-content-engine` and push this G0 baseline.
- [ ] Audit production FFmpeg distribution / codecs / legal notices.
- [ ] Benchmark CPU cost with representative licensed videos.
- [ ] Choose GCS vs R2 via real transfer-cost test.
- [ ] Provision and test Supabase/PostgreSQL RLS, transactional outbox and queue reconciliation.
- [ ] Connect Whisper after isolated model license, data and cost review.
- [ ] Deploy only behind access controls after production-specific authorization.

Details: `docs/G0_DECISION.md`, `docs/ARCHITECTURE.md`, `docs/REPO_AUDIT.md`, `docs/SECURITY.md`, `docs/SCALE_GATES.md`.
