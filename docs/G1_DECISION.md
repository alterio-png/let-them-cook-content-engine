# Issue #2 — decision and executed evidence

## Decision

**GO for the isolated G1 PostgreSQL proof. NO GO for production/SaaS/deploy.**
This is a DRAFT review deliverable; merge and deployment remain unauthorized.
Final PR approval is conditional on both GitHub workflows succeeding on the exact
final PR HEAD. The PR description records that final SHA and CI links, avoiding
any claim that this historical document can contain its own commit hash.

## Baseline and preservation

- Source `main`: `08bf0d42f83c68e82be5391d1315a924baa77b46`.
- All 20 `G0_SHA256SUMS.txt` entries verified before and after implementation;
  the manifest itself and all G0 tracked files remain unmodified.
- Baseline exact-SHA CI succeeded:
  https://github.com/alterio-png/let-them-cook-content-engine/actions/runs/42365121042
- Creative OS main was read only and remains
  `1a30934928a767fb851cd1d846318d6c60660394`; no clone, writes, reset, rebase or PR
  mutation was performed in that repository.
- Branch: `feat/g1-postgres-outbox-01`. Only additive files in this repository.
- README, AGENTS and all nine original docs were read in full, along with the SQL,
  code, tests, workflow, example Dockerfile and Issue #2. The Issue's G1 naming is
  used here; the older G0 scale-gate names are preserved as historical evidence.

## Actual execution

The committed local log [G1_LOCAL_RUN.txt](evidence/G1_LOCAL_RUN.txt) was produced
with complete source at `9acf75b` in a newly initialized, isolated PostgreSQL.
Only this decision/log addition follows that tested source. Final HEAD clean-clone
execution and exact-SHA CI are recorded in the DRAFT PR and its Actions artifact.

Environment: Python 3.12.14, PostgreSQL 16.15, psycopg/psycopg-binary 3.2.13,
libpq 17.6, typing-extensions 4.16.0, FFmpeg 7.1.5-0+deb13u1. PostgreSQL image
and Python artifact SHA pins are in the runner and dependency lock.
FFmpeg binary SHA-256:
`e8a8d46f5225f3062cec7c07fb145d58ae73c603cb740dcd5bad34bfb54e455a`.
ffprobe binary SHA-256:
`cefc5f81c0b18429cdbf8fa5a89e1e9c759235e10f93f9b329ff1b2fdff26f46`.

Result: **12 G0 tests + 36 G1 tests passed**. G0 uses real synthetic FFmpeg
video/audio/SRT and output probing/checksums. G1 migrations and application
transitions use actual PostgreSQL, not a mocked database. Tests assert:

| Requirement | Executed evidence |
|---|---|
| 100 identical operations concurrently | 100 separate open PostgreSQL connections synchronized before submission; exactly 1 logical job, 1 outbox, 1 submit audit, 6 fake cents reserved. |
| Tenant isolation | Distinct users/orgs; reads scoped both directions; direct writes denied; cross-org submit denied; cross-org asset/job child FKs rejected even for trusted writers. |
| Atomicity and quotas | Real failing DB triggers at job/outbox/audit boundaries leave zero partial writes/reservation; 20 competing distinct keys against 6 cents admit exactly two 3-cent jobs. |
| Immutable requests | Changed payload with same key rejected; direct digest/payload/cost/source hash changes rejected. |
| Media/cost caps | Rights, source bytes/duration/pixels and output pixels rejected before reservation; cumulative cost, max jobs and active-job quota tested. |
| Worker duplicates | 20 concurrent claims admit one live owner/attempt. Two organizations can progress independently under per-org limits. |
| Fences and timeouts | Expired owner rejected before/after takeover; real expiry during validation rejected at UPDATE; newer manifest remains authoritative; attempts bounded to three. |
| Crash/retry recovery | Real transaction failure after job/attempt updates rolls both back; retry and crash recover through DB sweep after original outbox dispatch, without a new event. |
| Renewal race | Pause renewal before commit across the old visible expiry; competing claim blocks, then sees renewed lease and cannot exceed max_active_jobs=1. |
| Sweep backlog | Tenant at capacity with more due work than scan limit cannot block another tenant's runnable work. |
| Queue handoff | 16 concurrent dispatcher claims admit one owner; timeout, 429, acceptance with lost response, crash before/after enqueue use stable event identity and exactly one named stub task. |
| Delivery exhaustion | Stale dispatcher cannot ack/release replacement lease; fifth failed send retains an audited reconcile state. |

The independent review found renewal/capacity and sweep/backlog races and a
container startup readiness race. All were fixed before the recorded complete
run, with regression coverage for both concurrency races and TCP readiness in
the disposable runner. Early test-harness FK ordering was corrected to assert
the intended composite FK failure rather than an earlier UNIQUE violation.

## Scope limits

The queue is a fault-injecting local idempotent stub, identity is an auth.uid()
test fixture, and manifests validate metadata. No claim of real Supabase Auth,
Cloud Tasks/Run, object storage byte verification, exactly-once physical rendering,
GPU/AI, production media sandboxing, unit economics or commercial license approval
is supported. None was used, provisioned, deployed or paid for. See
[G1_CONTROL_PLANE.md](G1_CONTROL_PLANE.md) for contracts, reproduction and rollback,
and [G1_DEPENDENCIES.md](G1_DEPENDENCIES.md) for isolated dependency review.
