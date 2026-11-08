# G3B-R0 decision

**CONDITIONAL GO for a bounded local synthetic demonstration.** This decision
requires the final delivered PR checks to be green and the maintainer to run
the separate human smoke before treating the UI as human-accepted. It is not
production, cloud, merge or deployment approval. The PR remains DRAFT.

`LOCAL_BROWSER_E2E`: actual browser login → PostgreSQL uploads → trusted
ingestion → real outbox/worker FFmpeg → three private verified downloads is
demonstrated. `REMOTE_STAGING`: **NOT_VALIDATED**. G3B-P1 remains **NO GO**.

## Evidence and review

The clean GitHub clone at `e418760bd998e97272f12fdeefc1487108c9a8c3` passed all
276 historical tests, 31 new tests and the real Chromium E2E. All 155 baseline
files remain byte-identical. The checked-in evidence directory includes actual
MP4s, manifest, FFprobe data, caption frames, screenshot, test logs, bounded load,
failure/recovery summaries and hashes. See
`evidence/g3br0/2026-11-08/README.md` and `docs/G3BR0_RUNBOOK.md`.

Independent review found and prompted fixes for the UI ingestion-duration
contract, bounded/nonblocking telemetry, Auth-backend 503 classification,
privileged environment leakage, local Docker/proxy guards and real PostgreSQL
monitor visibility. Actual principal misconfiguration tests reject direct and
column DML. No confirmed tenant/auth/fencing bypass remained in the review.

The first CI browser startup failed after all 31 R0 tests passed. Its exported
failure was retained and is not a browser PASS. The driver must provide actual
startup diagnostics and the final runner/browser gate must pass before this
decision is active; the final PR report identifies the exact delivered SHA and
check URLs. The older local E2E does not substitute for that gate.

## Remaining operational findings

| Finding | Local work completed | Remaining gate |
| --- | --- | --- |
| BP-02 sustained capacity | Controlled two-replica/eight-caller load, real rendering and sampled resources | Long-lived workload/fairness/SLO and target hardware remain unvalidated |
| BP-03 aggregate connections | Role-enforced 4+6+3+2+1 budget, max_connections=30, real monitor visibility and samples | Fleet-wide pools, hosted Auth/background consumers and remote capacity |
| BP-04 503 diagnosis | Distinct HTTP capacity, pool, Auth provider/session and DB event codes; browser bounded retries | Hosted error taxonomy/alerting and all dependency failure paths |
| BP-05 failure summaries | Structured per-case/workload outcomes, physical/logical/retry/exhaustion counts, retained failed attempts; historical/local phase export | Failures before evidence initialization/dependency bootstrap can lack exported artifacts; runner logs remain necessary |

These findings remain **partially addressed operational backlog**, not closed
production findings. Sampled RSS/live CPU exclude container PG/Auth and have
documented sampling/shared-memory limits. Local rootless cgroup enforcement is
NOT_VALIDATED; no claim that Docker flags alone prove enforcement is made.
Native G2 bounds remain unchanged and covered by historical real tests.

The separate agent-operated exploratory browser walkthrough observed the
interface, actual playback and downloads. **Human-operated smoke/listening is
NOT_EXECUTED**; audio preservation instead has actual decoded-audio/tone and
stream evidence. No UX/customer acceptance, long-video throughput or production
FFmpeg codec/legal clearance is implied.

## Boundaries preserved

Only generated approved synthetic media; 1 MiB upload, 120 s job and 32 MiB
delivery caps unchanged. No browser authority to approve provenance/promote
receipts. No SQLite fallback, quota refund or destructive R0 reconciliation.
No client videos, paid cloud, hosted Supabase, Terraform plan/apply, deploy,
merge, external project modification or G3B-P1 execution. Creative OS and the
155 previous repository files are unchanged.

Actual cloud monetary cost remains **UNKNOWN**. The historical P0 prices and
architecture documents are preserved unchanged. A future remote gate still
needs explicit authorization, budget/region/owners, hosted runtime/Auth/IAM,
storage/queue validation and separate security/capacity evidence.
