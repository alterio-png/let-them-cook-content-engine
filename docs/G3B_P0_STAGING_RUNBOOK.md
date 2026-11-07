# G3B-P0 — future staging runbook, not executed

Prepared for Issue #10 on 2026-11-07. **Execution status: BLOCKED.** This is a
reviewable procedure for a later, separately authorized G3B-P1. P0 performs
documentation and static verification only; it creates no account, credential,
hosted Auth change, project, bucket, queue, Job, DNS or paid service. No deploy,
Terraform plan/apply or staging experiment is run by this document.

The baseline is main `7a271382487ac4d16e278e5ccb8c345279b3cc16`. Proposed shared
upload authority is in **OPEN/DRAFT PR #9**, HEAD
`e40357d08fa3c0b258b63c0d3a811cbab2b23206`; it is not integrated into main.
[Preflight](G3B_P0_PREFLIGHT.md), [architecture](G3B_P0_ARCHITECTURE.md),
[cost model](G3B_P0_COST_MODEL.md) and [security gates](G3B_P0_SECURITY_GATES.md)
are prerequisites, not records of deployed infrastructure.

## 1. Authorization record required before any resource action

No person, account, region, credit, plan or budget is inferred. Fill and sign
this record outside source control without publishing credentials. Approval
must name the exact implementation/configuration/image digests to be tested.

| Required field | Current status | Required accountable role/evidence |
| --- | --- | --- |
| Independent PR #9 review of upload authorization, RLS/grants, locks/deadlines, retry/backpressure, recovery and rollback | BLOCKED | Named independent reviewer and resolved findings; neither authorship nor local PASS is approval |
| Controlled merge of PR #9 and all **five** workflows on its new main SHA | BLOCKED | Authorized human merge decision, immutable merge SHA and G0/G1/G2/G3A/G3A.1 success links |
| Future runtime/wiring implementation and its separate review | BLOCKED | Reviewed cloud HTTP adapter, ingest bridge, scheduler/sweep ownership, secrets/configuration and container entrypoints |
| Nonproduction provider account, billing account, dedicated project and region | UNKNOWN | Authorized owner identifies exact existing/new resource scope, residency and billing ownership; no production account |
| Auth/PostgreSQL plan, region, TLS/pooler, permissions, backup and restore retention | UNKNOWN | Database/security owner approves and validates all dependent services and their fixed/variable charges |
| Budget, thresholds, price sheet, billing lag allowance and residual retention funding | UNKNOWN | Budget owner explicitly approves amounts and gross-cost accounting; no assumed free allowance or credit |
| Operator, backup operator and cancellation authority | UNKNOWN | Named people available for the complete window, with narrowly reviewed stop permissions and verified notification route |
| Licensing/security/image gate | BLOCKED | Exact image digests, SBOM/signatures, native-dependency/GPL/codecs review and accepted synthetic-only execution scope |
| Start/end time, fixture manifest, operation count and teardown/retention owner | UNKNOWN | Signed session manifest and acceptance of charges that can arrive after the window |

Missing approval, reviewer, accountable owner or verified billing information
means **do not start**. Do not create a project first and investigate eligibility
or prices afterward. A new fixed-price Auth/database plan that cannot fit the
approved envelope blocks this session; shared capacity/credits are not presumed.

## 2. Resolve implementation and access gaps before scheduling P1

At PR #9's reviewed source, the HTTP listener is loopback-only; the task handler
is a callable contract, not a hosted listener. A future Cloud Run adapter must
bind the required runtime port, enforce body/receive/handler limits, select
`PostgresUploadAuthority` fail-closed and consume reviewed secret references.
The dispatcher/sweep runtime is not provided by the historical IaC proposal.
Receipt state `uploaded_pending_validation` also has **no automatic trusted
upload-to-G2 ingestion bridge**. Implement/review these in separate work before
claiming a cloud pipeline, rather than bypassing them with a privileged HTTP DSN
or presenting manual fixture ingestion as an end-to-end upload proof.

Specify the test client's approved network/identity path. The historical API is
internal ingress with no API invoker binding. An end-user Supabase JWT alone
does not establish Cloud Run IAM permission. Keep user/session verification
separate from provider invocation identity; do not make the service public or
use an administrator token merely to get the experiment running. Validate the
same-project Tasks-to-controller route and exact OIDC audience without treating
internal ingress as outbound isolation or private database connectivity.

The proposed initial connection envelope is **13 application/operations slots
plus hosted Auth/platform connections that remain UNKNOWN**: API ledger 2
(including heartbeat), API render 2, live session check 1, controller 1,
dispatcher 1, sweep 1, worker 2 (main/heartbeat), upload reconciler 1 and operator/
recovery reserve 2. Dispatcher/sweep/reconciler are serialized in this first
session, but the conservative allocation does not subtract them. Before P1,
prove role/pooler limits and measured aggregate capacity including Auth and
provider reserves; the old ten-slot planning example is insufficient. A proposed
environment variable is not evidence that code consumes or enforces a pool cap.

## 3. Proposed finite session envelope — requires explicit approval

These are planned ceilings, **NOT_VALIDATED** and not implemented by this
documentation. The operator must verify their effective runtime and database
enforcement before admitting a request. Reducing a provider setting alone does
not enforce the cumulative session limit or prevent a direct launch bypass.

| Control | Initial proposal | Admission/stop rule |
| --- | --- | --- |
| Session window | At most two hours, including setup/measurement and compute shutdown | Fix an absolute UTC deadline; stop new admissions by T+90 min, drain/cancel by T+110 min, verify compute stopped by T+120 min |
| API and controller | One instance each, one concurrent request each; no warm minimum | No public access or scale increase to work around a failed gate |
| Worker | One physical execution globally, one task, parallelism one, platform retries zero | PostgreSQL launch admission cap one; unknown/uncertain launches retain the slot until terminal provider evidence |
| Worker allocation | 1 vCPU / 2 GiB, provider timeout 300 s | Inherited application whole-job timeout is 120 s; do not silently raise it for a five-minute input |
| Global workload | Three logical jobs, at most **four physical executions total** | Normal 1-minute, normal 5-minute, and distinct 1-minute fault job with at most one later fenced recovery execution |
| Queue signals | At most four named wake-up events, one signal/s and one concurrent delivery; target budget five deliveries/event | 20 callbacks is a conditional planning budget requiring an independently verified stop mechanism and overrun headroom, not a bound established by the historical retry settings; deliveries never authorize extra physical renders |
| Scheduling | One operator-owned bounded dispatcher/sweep loop; no unattended scheduler | Serialize dispatcher, sweep and upload reconciliation; every launch must traverse the same admission ledger |
| Uploads | Two synthetic organizations; exact owner/request/hash/byte caps provisioned from the approved fixture manifest | Match DB quota, authority, HTTP body and trusted-ingest limits; no automatic quota increase |
| Outputs/logs | Fixed three formats, exact approved quality/profile; bounded output, operation and log-volume inventory | Record all attempts/objects, including failed ones, retained versions and soft-deleted bytes; stop before approved bounds are exceeded |

Current defaults include HTTP/upload body **1 MiB**, application input duration
900 s and native output limit **128 MiB per output**. Those are different
boundaries. HTTP delivery separately caps each download at **32 MiB**; an output
accepted under the native 128 MiB limit is not automatically deliverable through
that unchanged endpoint. The fixture/profile must fit both, or stop for a
separately reviewed change. For four executions and three video formats, a conservative native
output allocation alone is 4 × 3 × 128 MiB = **1.5 GiB**, before source/caption,
manifests, retained versions, soft-deleted bytes and ancillary storage. Do not
replace that worst-case term with expected tiny smoke outputs. The effective
application/provider/storage bounds and cost calculations must agree.

A 300-second synthetic source is not a claim that rendering takes 300 seconds.
Both one-minute and five-minute fixtures need pre-approved actual byte counts,
SHA-256, SRT, duration/audio/probe expectations and a profile shown to fit all
unchanged caps. **Five-minute readiness is BLOCKED** until this is demonstrated
within the 120-second application deadline and approved memory/CPU limits, or a
separately reviewed bounded implementation/profile change is authorized. A
timeout is evidence to stop/reassess, not permission for a larger timeout,
instance, upload limit or new spend envelope.

Current official Cloud Tasks `RetryConfig` documentation says retry stopping
requires both the attempt and duration conditions. Historical
`max_attempts=5` together with `max_retry_duration=300s` therefore does **not**
prove a hard five-delivery ceiling. Before P1, review effective settings and
implement/validate a separate attempt-budget/stop path that preserves durable
outbox state; count late/in-flight deliveries and every provider operation.
Without a finite approved overrun estimate, callback cost remains UNKNOWN and
the session remains BLOCKED. Do not acknowledge a failed operation as completed
just to suppress billing. Queue pause stops future delivery, not task creation
or already in-flight work; stop producers too. Runtime identities must not gain
the debugging `RunTask` bypass, which the queue reference says can bypass rate
limits. No attempt to invoke or inspect such provider operations is made in P0.

### Conditional financial thresholds

**USD 10 is only a proposed ceiling**, not an authorized credit or charge. Only
if the budget owner approves these exact values: configure alerts at 50/90/100%
of the approved scope; stop new admission at **USD 5 measured** or **USD 8
forecast**, whichever occurs first, or sooner if headroom is insufficient.
Forecast must include unreported operations, allocation/minimum billing,
idle/fixed services, failed work, retained objects and teardown obligations.

The historical IaC proposes **alerts-only** budgets. These notifications are not
hard spending caps, and delayed usage/billing can exceed a threshold before the
operator sees it. Current official documentation separately describes optional
service-supported spend-cap budgets; eligibility and effectiveness for this
topology are **NOT_VALIDATED** and do not replace application limits or the
operator kill policy. Unknown gross cost, missing telemetry or inability to
estimate a pending charge means stop; a credit-adjusted display of zero does
not prove zero gross cost. See the dated [cost model](G3B_P0_COST_MODEL.md).

## 4. Future ordered experiment and evidence matrix

Every row below is **NOT_VALIDATED** until separately authorized execution
produces the required evidence. No executable cloud command is supplied or run
here; commands require the approved project/region/resource inventory and
reviewed runtime. Do not launch by copying a placeholder or toggle an IaC guard
to substitute for authorization.

| Order | Planned action | Required evidence before next row |
| --- | --- | --- |
| 0 | Reconfirm main, PR #9 merge/review, five post-merge workflows and the exact reviewed runtime/configuration/image digests | SHA/digest inventory, signed authorization record, no unresolved prerequisite |
| 1 | Capture preexisting inventory and gross billing baseline before any approved create/change | Timestamped resource IDs, owners, labels, regions, IAM, secrets' IDs/versions only, Auth/DB plan and existing fixed charges; no unrelated resources in deletion scope |
| 2 | Establish only the approved isolated identities, private services and store; verify effective bounds/kill path without rendering | Denied unauthorized invocation, TLS and role/pooler evidence, aggregate connections, working operator cancellation and no direct launch bypass |
| 3 | Verify hosted Auth session invalidation, membership revocation and cross-organization denial | Actual issuer/audience/time/key/session checks; logout/deleted-user/role negatives; user and service identities remain separate |
| 4 | Verify shared upload admission and private storage, including interrupted publication/reconciliation | Actual generation/hash/bytes, one immutable receipt and quota charge, revoked/cross-tenant access denied; absent uncertain object never refunded |
| 5 | Complete the reviewed trusted ingestion bridge, rights check and bounded probe/SRT validation | Uploaded bytes become a verified immutable G2 asset only after trusted checks; no client-supplied metadata or privileged user DSN |
| 6 | Run one 60-second synthetic source, producing 9:16, 1:1 and 4:5 with supplied SRT/audio | Real provider operation/execution IDs, allocated/billed resources, stream/dimensions/duration/audio/hash verification and one authoritative fenced manifest |
| 7 | Review cost, retries/503, DB pressure, outputs and remaining window; then run one 300-second source only if every gate still fits | Same verification; source duration and render/billing time recorded separately; no unreviewed cap change |
| 8 | Use the distinct 60-second fault job for one controlled worker interruption; withhold any recovery execution until the prior execution is proven terminal | Durable uncertainty/lease/fence and actual provider terminal evidence; at most one new fenced physical recovery, never a blind repeated Jobs `run` |
| 9 | Stop admission, drain/cancel and reconcile; export redacted evidence and before/after inventory | All physical executions and authoritative objects accounted for; no unknown launch/receipt state treated as safe deletion |
| 10 | Complete authorized teardown or an explicitly funded retention handoff; inspect delayed billing and record verdict | Retained/deleted resource list, versions/soft-delete/backups and residual costs, owner/time for follow-up; no production/cloud-scalability claim |

This first session intentionally does not repeat the 100/20 cloud-load cohort.
PR #9's two-CPU CI needed **305 physical attempts**, including **205 HTTP 503
backpressure responses**, for 100 successful logical duplicate admissions.
Consistency under bounded retry is different from sustained capacity. Record
every 429/503, retry delay/count, connection wait, p50/p95/p99, backlog age and
physical launch, and compare to the approved serial envelope. If later load
testing is warranted, require a new bounded workload/cost authorization.

## 5. Immediate-stop matrix and operator kill policy

The named operator must be able to close admissions and stop dispatch/sweep
without destroying the ledger. If that independent control or cancellation
permission is unavailable, the session remains **BLOCKED**. On any condition
below, stop new requests/signals immediately, pause producers, preserve evidence,
and drain or cancel only the known executions in the approved inventory.

| Stop condition | Required response and preservation rule |
| --- | --- |
| Auth or tenant-isolation bypass; service-role/user identity confusion | Stop all admission and dispatch; deny the affected route/identity through the reviewed control, preserve audit data and investigate; no workaround using a broader principal |
| Unknown billing, missing price/usage term, USD 5 measured / USD 8 forecast reached if approved, or ceiling/window at risk | Freeze new work; reconcile pending allocations/operations and retained storage; no continuation on assumed credits or delayed dashboard data |
| Duplicate **physical** launch, more than one unresolved/global execution, or multiple Jobs without enforced fencing | Stop producers and admission; cancel/drain known executions, retain all launch reservations until authenticated terminal proof; do not infer safety from one completed PostgreSQL job |
| Object/receipt/manifest divergence, mismatched generation/hash/size, missing authoritative object | Quarantine access, retain quota/receipt/history and exact generation IDs; no overwrite, automatic refund or broad deletion |
| Unexpected 503/429 growth, retry ceiling reached, DB connection/lock pressure, lease loss or repeated timeout | End the wave, retain unacknowledged state and bounded backoff; do not raise concurrency or retry Jobs blindly |
| Loss of operator, stop control, telemetry, provider reachability or authorized window | Stop admission; if physical state is unobservable retain uncertainty and escalate to the named backup, never release a slot merely because a GET failed |

Cancellation is an asynchronous action to verify, not terminal proof by itself.
Record provider operation/execution IDs, requested cancellation time and actual
terminal task counters/completion time. Deleting a Job template, stopping the
dispatcher, or expiring a database lease is not a substitute for proving its
physical executions stopped. Retire a physical slot only via the reviewed
terminal-evidence path; an unresolved cancellation remains an uncertainty cost
and availability obligation. Operator escalation cannot grant a second launch.

## 6. Recovery, rollback, retention and teardown

Preserve the source-of-truth order. ObjectStore and PostgreSQL do not share an
atomic commit. Reconcile a private object by actual bytes/hash/size/generation
before accepting its receipt/manifest. Only expired, never-claimed upload
reservations (`fence = 0`) can refund automatically, once. A previously claimed
upload with absent bytes remains charged: a paused writer may still publish.
Authenticated exact-body resume or the separately restricted reconciliation
role must acquire a fresh fence; revoked/deleted owners do not justify deleting
historical receipts. All service privileges stay separate from HTTP credentials.

Stop new shared admission, drain/cancel known writers, retain leases/fences and
prove physical termination before destructive actions. Rollback keeps the shared
PostgreSQL ledger, quotas, receipts, events, signing-key reference/version and
authoritative objects available under the original authorization rules.
**Never move an active organization back to an empty SQLite ledger**, reset
usage counters, drop the shared schema, or delete a valid receipt/current object
as a shortcut. Restoring an older image is not a data rollback. The proposed
G3A.1 migration supplies no automatic SQLite-data import or destructive GC.

Post-run inventory must enumerate live objects, **all noncurrent generations
and soft-deleted objects/buckets**, resumable/session uncertainty, retained
logs/images/secrets/versions, DB data/backups and every provider execution.
Ordinary object listing does not include every retained state. GCS soft delete
can retain data and associated storage costs after a delete; changing retention
does not remove already retained historical objects. Respect holds, generation
preconditions and existing `prevent_destroy`/`force_destroy=false` guards.

After a reviewed retention decision, conditionally delete only proven obsolete
generations whose writers are stopped and that are not authoritative in the
database. Record each deletion and resulting retained/soft-deleted bytes. Do
not delete the project or bucket blindly: that can destroy authoritative data
and remove the evidence needed to reconcile an uncertain write. If safe cleanup
cannot complete within the window, shut down compute/admission, retain the
minimal necessary private data, document ownership and obtain explicit funding
for the remaining retention obligation. A two-hour compute window does not
make later storage, backup, secret or fixed-plan charges disappear.

Compare pre/post inventories and separately mark each resource removed,
retained with reason/owner/deadline, or **UNKNOWN**. Verify delayed billing after
the provider's observed reporting delay and again until all pending items are
reconciled; record actual timestamps instead of inventing a guaranteed latency.
P1 cannot be declared cleanly torn down while unknown executions, unowned
retained data or unexplained charges remain. Export only redacted correlation
IDs, timestamps, hashes, counters and billed quantities; exclude JWTs, DSNs,
capability/session URLs, secret payloads and media contents from logs.

## 7. Future session report and decision boundary

The future evidence pack must contain exact main/runtime/image digests,
configuration and role limits, approved project/region/identities and redacted
pre/post inventory, fixture hashes and stream/probe expectations, provider
operation/execution/generation IDs, request/retry/503 and launch counts,
leases/fences, verified manifests, resource/latency measurements, list-price
calculations, gross actual costs, credits/taxes separately, unknowns and
teardown/retention ownership. Each planned test becomes PASS only with its own
actual evidence; otherwise report BLOCKED, NOT_VALIDATED or UNKNOWN.

Any eventual P1 verdict applies only to its bounded staging envelope. It does
not approve customer media, public ingress/publishing, production, commercial
codec distribution or autoscaling. P0's present readiness decision is in
[G3B_P0_DECISION.md](G3B_P0_DECISION.md); no step above has been executed here.

## Source boundary

Repository observations are from the exact main/PR heads stated above, chiefly
`docs/G3A_IAC.md`, `docs/G3A_ARCHITECTURE.md`, `docs/G3A1_DESIGN.md`,
`docs/G3A1_SCHEMA.md`, `docs/G3A1_RUNBOOK.md`, `engine/cloud_http.py`,
`engine/cloud_worker.py`, `engine/media_runtime.py`, `engine/media_ingest.py` and
`engine/cloud_run.py`. Immutable file/byte hashes and static-verification outputs
are indexed in [preflight](G3B_P0_PREFLIGHT.md). Historical G3A references to a
SQLite blocker are preserved; PR #9 changes the proposed local authority only
after its explicitly selected shared application, not main or deployed cloud.

Official operational sources read on **2026-11-07**, without authentication or
resource actions:

- [Google alerts-only budgets and the separate service-supported spend-cap option](https://docs.cloud.google.com/billing/docs/how-to/budgets): the historical alert proposal does not automatically stop spending.
- [Cloud Run Jobs execution model](https://docs.cloud.google.com/run/docs/execute/jobs): execution/task identity and provider observations must be recorded separately from PostgreSQL logical jobs.
- [GCS soft-delete behavior](https://docs.cloud.google.com/storage/docs/soft-delete): default retained states, noncurrent/deleted inventory and their continuing storage obligation must be accounted for.
- [Cloud Tasks retry configuration](https://docs.cloud.google.com/tasks/docs/reference/rest/v2/RetryConfig) and [queue rate limits/state](https://docs.cloud.google.com/tasks/docs/reference/rest/v2/projects.locations.queues): a configured retry count/duration is not the proposed hard callback budget; pause, in-flight work and permitted invocation paths require separate verification.
