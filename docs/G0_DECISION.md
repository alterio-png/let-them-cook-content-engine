# G0 decision record | 2026-11-07

## Verdict: PASS for local core feasibility, CONDITIONAL for cloud foundation, NO GO for SaaS launch

The user's goal is a reliable, scalable and legally commercializable video/content platform, using LetThemCook Creative OS as a curated technology library. A new repo is the correct isolation boundary.

### Verified evidence
- 12 local Python unittest cases passed with real `ffmpeg` renders on synthetic, locally generated source footage, with video+audio and supplied SRT captions.
- Rendered three formats: 9:16 (270x480), 1:1 (320x320) and 4:5 (320x400), with hashes and ffprobe output checks.
- Same operation key was reused without duplicate render; conflicting payload rejected; damaged output requires explicit reconciliation.
- Basic path isolation between tenant-owned local input directories tested.
- FFmpeg runtime: Debian `7.1.5-0+deb13u1`, with `--enable-gpl`, `--enable-libx264`, `--enable-libass`. This is an **existing environment binary**, not a commercially cleared distribution.
- GitHub candidate upstream HEADs retrieved on 2026-11-07. No candidate code was cloned or installed.
- Existing private Creative OS remains unchanged. Last verified main from connected GitHub: `1a30934928a767fb851cd1d846318d6c60660394`; Catalog QA CI success, `2026-11-06`.
- Requested new GitHub repository `alterio-png/let-them-cook-content-engine` was not found via connected GitHub (`404`) at G0 inspection. The connector does not expose repository-creation action. This ZIP is a prepared baseline, **not pushed to GitHub**.

### Scope of this ZIP
A Python stdlib CLI and isolated FFmpeg subprocess proof, SQLite demo-state, proposed PostgreSQL schema with org RLS, architectural contracts, license triage, security and load gates, CI config (untested on GitHub).

### NOT demonstrated
No frontend, OAuth, upload service, cloud deployment, Supabase migration run, PostgreSQL transaction proof, Cloud Tasks or Cloud Run load test, GPU, AI transcription, smart reframing, rights certification, social publication, billing, data retention or production incident runbook exercise.

### Authorized next step
Create private repository with the exact new-project name, upload this baseline, run CI on the published HEAD and only then authorize isolated G1 candidate licensing + infrastructure proof. Do not alter Creative OS main or promote candidates to approved automatically.

### Target engineering defaults, subject to benchmarks
- Web: Next.js/TypeScript behind auth (not in G0)
- Identity and data: Supabase Auth/Postgres with tenant RLS, transactional outbox (draft only)
- Async: Cloud Tasks for control-plane dispatch; Cloud Run service orchestrator -> Cloud Run Jobs for long renders (not yet deployed)
- Video: isolated FFmpeg CPU worker; GPU only if benchmark demonstrates value
- Subtitle/AI: faster-whisper candidate with explicit model/data licensing and provider abstraction
- Storage: choose GCS versus Cloudflare R2 via measured total cost (including GCP egress to R2)
- Publishing: official APIs, explicit user consent and approval, no third-party scraping dependency

### Decision order
1. Legal/codec build and vetted synthetic sample + real licensed reference clips.
2. Production data model in isolated PostgreSQL: org access, RLS tests, unique request key, outbox, epoch/lease, idempotent artifact commit.
3. Load on one real cloud worker, checkpoint and retry tests; measure cost per media minute.
4. Authenticated upload + review UI; multi-tenant E2E validation.
5. Approved social connector and billing after measured economics.

### G0 sign-off
Only the local feasibility gate is supported by evidence. No operator or legal acceptance is implied. No SaaS production authorization.
