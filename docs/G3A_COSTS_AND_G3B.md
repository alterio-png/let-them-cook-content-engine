# Cost model and G3B plan — NOT EXECUTED

G3A uses local disposable processes/containers and synthetic media. No paid
service, production credential, hosted project, bucket, queue, job or deployment
is authorized. G3B requires a separate explicit authorization and budget.

## Cost evidence and unknowns

| Component | Measured in G3A | Monetary cost |
|---|---|---|
| Local PostgreSQL + render workers | CPU time, aggregate sampled RSS, elapsed time, bytes and completed/failed attempts in benchmark report | Unknown; the execution host's billing rate is not available. |
| Supabase hosted Auth/database | Not exercised; official Auth component runs locally | Unknown; plan, region, MAU, compute, pooling and storage not selected. |
| GCS | REST contracts exercised locally, no remote requests | Unknown; region/class, GiB-days, object operations, versions, transfer and lifecycle behavior require real measurement. |
| Cloud Tasks | Named wake-up/retry contracts exercised locally | Unknown; count all operations including retries/lookup and confirm current regional quotas/prices. |
| Cloud Run Jobs | Launch/uncertainty contracts exercised locally | Unknown; actual allocated vCPU/GiB seconds, startup/minimum billing, retries, logs and network are not measured. |
| R2/S3 alternative | No remote execution | Unknown; include GCP-to-provider egress, request classes, minimums and storage, not only destination egress. |

G1's reserved cents are fake correctness fixtures, not prices. Pixel-frame work
estimates are not CPU billing. Do not multiply local throughput into a cloud SLA
or infer costs from local wall time alone. Capture a dated official price sheet
for the approved region during G3B planning, and keep list price, observed bill,
credits/free allowances, taxes and margin assumptions separate.

Relevant official sources (recheck immediately before authorization):

- https://cloud.google.com/run/pricing
- https://cloud.google.com/storage/pricing
- https://cloud.google.com/tasks/pricing
- https://supabase.com/pricing
- https://developers.cloudflare.com/r2/pricing/
- https://docs.cloud.google.com/tasks/docs/quotas

For each job record input/output seconds; allocated vCPU/GiB seconds; actual
bytes and object-days; provider request/retry counts; queue operations; log bytes;
inter-region/inter-cloud transfer; failed/duplicate physical executions; support
allowance. Compute cost per input minute, output minute and successful operation
only after assigning every measured term its approved price. Keep unknown terms
as `unknown`, not zero.

## Proposed G3B authorization envelope

**Status: BLOCKED_PENDING_SEPARATE_AUTHORIZATION. No part of this plan is run.**

Proposed ceiling: **USD 10 total**, one isolated non-production project, one
approved region and a maximum two-hour test window. This is a proposed limit,
not approval to spend. Before creation, estimate the bounded worst-case workload
using the current price sheet; if it cannot fit with headroom, reduce the workload
or obtain a different explicit budget. Do not create a project first and estimate
afterward. Use no customer footage or production secrets/accounts.

Budget alerts are delayed notifications, not an immediate hard cap. Pair alerts
at 50/80/100% with explicit bounded executions, global admission limits, a wall
deadline, an operator watching billing/usage and a tested stop procedure. Stop
new admission at USD 5 measured or USD 8 forecast (including delayed usage), or
earlier if the approved ceiling/window is at risk. A billing estimate with unknown
charges is not sufficient authority to continue. No automatic budget kill/deploy
is implemented or exercised in G3A.

## G3B sequence

1. Obtain named human owner, security/licensing review, explicit spend ceiling,
   approved region/data residency, short retention and teardown ownership.
   Review IaC diff/provider validation without putting secrets in state or logs.
2. Use a separate staging project and narrowly scoped identities/secret references.
   Before enabling an API image or multiple/ephemeral replicas, replace the local
   SQLite upload reservation/receipt ledger with shared PostgreSQL state and
   test concurrent grants, restarts, reconciliation and total tenant byte caps.
   Validate real Supabase Auth signature/key rotation, issuer/audience/expiry,
   logout/deleted sessions, role/membership changes and SQL RLS using restricted
   logins. Never reuse local fixture keys or service-role grants in a browser.
3. Validate real private GCS upload, resumable interruption/status query, generation
   preconditions, hash/size/probe, signed download TTL, conditional deletion,
   private IAM denial and obsolete-object cleanup. Record operation and transfer
   counters. Repeat cross-tenant denial using real service identities.
4. Validate real Cloud Tasks OIDC issuer/audience/service email, forged headers,
   duplicate/late delivery, 429/timeout/response loss and disappearance after the
   name/history window. Verify PostgreSQL sweep rather than provider history
   governs recovery. Do not hold HTTP open for rendering.
5. Validate real Cloud Run Jobs immutable arguments, dedicated worker identity,
   container provenance, non-root execution, time/CPU/memory limits, intent/fence
   binding, lost run response and operation reconciliation. Demonstrate that a
   second run is not blindly launched when the first is uncertain.
6. Generate synthetic 1080p video with tone audio/SRT: cohorts of **1 minute and
   5 minutes**, then three aspect-ratio outputs. Begin at one worker; advance
   only within the approved budget to two global executions and bounded per-org
   capacity. Prove one logical reservation/manifest under 100 duplicates.
7. Measure actual startup/run/queue/end-to-end percentiles, CPU/RSS/allocated
   resources, failure/retry rate, object and network operations, concurrent DB
   connections, authoritative results, physical duplicate execution and billed
   cost per rendered minute. Compare polling/Tasks, Jobs/VM and GCS/R2 with
   complete cost terms and real operational complexity.
8. Inject a worker termination and controlled storage/queue uncertainty within
   the same global budget. Reconcile DB, operations and object generations;
   verify stale workers cannot publish an authoritative result. Stop immediately
   on isolation/auth bypass, uncontrolled concurrency or uncertain cost growth.
9. Stop admissions; cancel or drain known executions; investigate uncertain
   launches; export redacted evidence; delete temporary objects/secrets/queue/job
   resources and the isolated project as authorized. Verify billing/resource
   inventory after teardown and delayed charges. Record leftovers explicitly.

## Required G3B outcome

One evidence pack must identify exact commits/images, region/config, trust roots,
identities, workload and timestamps, measured resource/cost counters, provider
operation IDs, failure cases and teardown inventory. Decide GO/CONDITIONAL GO/
NO GO for the tested staging envelope only. G3B completion still does not grant
permission for production, customer uploads, commercialization, public publishing
or unbounded scaling.
