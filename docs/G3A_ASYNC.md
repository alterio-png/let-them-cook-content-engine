# G3A asynchronous cloud contracts — Issue #6

These are prepared REST contracts, not a deployed cloud pipeline. Local evidence
uses real PostgreSQL 16.15, real FFmpeg and real RSA verification with local keys;
provider responses come from a deterministic in-process stub. No Google resource,
credential, API request, execution or bill was created by these tests. Cloud Tasks,
Cloud Run, Google-issued OIDC, metadata ADC, provider IAM and physical cloud limits
remain **NOT_VALIDATED**. Unknown execution acceptance is explicitly **BLOCKED**
until provider evidence or a reviewed operator investigation can establish safety.

## Authority and delivery

Cloud Tasks carries a short versioned JSON HTTP wake signal containing only the
organization, job and durable outbox event UUIDs. Task names use the event UUID.
The REST adapter configures an exact HTTPS callback, dedicated service-account
OIDC identity, exact audience and 30-second dispatch deadline. It handles 409
ALREADY_EXISTS and surfaces 429/transport uncertainty to bounded PostgreSQL outbox
backoff. A stale dispatcher cannot acknowledge or release a replaced outbox lease.

`CloudDispatcher` is separate from the unchanged G1 Dispatcher. It has no provider
lookup assumption. Google's task-name retention is finite (currently up to 24 hours,
or 9 days for queue.yaml/queue.xml queues). Deleted or expired provider history can
permit another HTTP signal with the same name. That signal cannot insert a job:
PostgreSQL validates its original outbox/job/organization binding, reserves one
launch intent per job/fence, and grants one fenced worker claim. The independent
G2 PostgreSQL sweep can recover logical work without any task history. The local
fallback tests execute G2 locally; a cloud scheduler must instead discover due
jobs and enter the launch ledger, never invoke an unrestricted MediaWorker or
jobs.run path that bypasses the physical reservation cap. Neither
Cloud Tasks nor these adapters promise permanent deduplication or exactly-once
physical rendering/billing.

## Non-idempotent jobs.run and cost control

`CloudLaunchControl.prepare_launch` commits an immutable `cloud_job_launches` row
**before** making `jobs.run`. A global PostgreSQL advisory lock serializes slot
reservations across organizations; organization locking preserves the G1 active
quota. The default global reservation cap is two, with a configured upper bound of
32. The ledger has composite foreign keys for organization/job/event identity,
RLS, no end-user grants, immutable identity and operation name, and irreversible
closure. Only the trusted internal worker principal can use it.

Only the creator of a new intent calls `jobs.run`, once. A timeout, 429, ambiguous
error, response loss or controller crash leaves its intent unresolved. Repeated
signals and controller restarts return that intent and never blindly retry the
non-idempotent API. Unknown acceptance with no operation name reserves the slot
indefinitely. This trades availability for bounded admission and requires an
operator/provider investigation; elapsed local time is not proof of absence.

An accepted operation name is validated against the configured project/region.
Reconciliation reads that exact operation and its execution. `operations.done`
means execution creation completed, not that a worker stopped. Operation error,
404, missing history, expired lease, and even a completed/failed PostgreSQL job do
not release the physical admission slot. Release requires an authenticated
execution response with completionTime, zero running tasks, and terminal task
counts equal to taskCount. A stopped unclaimed execution consumes one bounded G1
failure attempt before another launch may use a new fence. If quota/backoff
prevents that transition, reconciliation keeps its slot and retries observation.
A stopped claimed execution permits a new launch only when G1 also allows takeover.

These are locally tested admission contracts. Actual provider concurrency, task
retries, billing, execution visibility and IAM semantics are **NOT_VALIDATED**.
Every jobs.run permission must belong exclusively to the controlled path before
G3B can measure or claim physical bounds. The review template specifies one task,
parallelism one, API retry zero, timeout 300 seconds, CPU/memory caps and a pinned
image. Independent provider-internal duplication cannot be excluded locally.

## Queue-independent cloud recovery

`LaunchController.sweep_once(limit=20)` is the cloud-safe recovery entrypoint.
The integer limit is restricted to 1–100. Each pass first observes at most that
many unresolved launch records, then discovers at most that many due G2 jobs by
joining PostgreSQL media specs and their original outbox event. Discovery ignores
outbox dispatched state and provider task history. It never calls an unrestricted
G2 claim/worker and never resends an uncertain jobs.run request. Every new launch
uses the same globally locked intent reservation as a task callback.

Observations rotate touched_at under a short SKIP LOCKED transaction so an old
unknown operation cannot permanently starve later terminal evidence when limit=1.
Candidates with any unclosed launch are excluded. Saturated organizations are
filtered before the candidate limit, while attempt-exhausted jobs remain eligible
for safe terminalization. The preparation transaction rechecks all conditions.
An unresolved older physical execution blocks every new fence for its job, even
when its database lease expired and another global slot is free.

The bounded report distinguishes provider-observed states, blocked observations,
new network launch attempts, deferred existing/capacity work, terminal failures
and errors. Its `launched` entries count only new controller network attempts;
state=uncertain is not proof a container started. A concurrent callback returning
an existing intent never increments that count. SQL/provider failures are recorded
or raised, never silently converted to successful completion.

After authenticated terminal provider execution evidence and G1 lease expiry,
a due crashed job can obtain a new intent/fence through a later sweep. When the
attempt cap is exhausted, the sweep marks the job failed, expires unfinished
attempt records and adds one audit event atomically under organization/job locks,
only after every physical reservation for that job is closed. It creates neither
a logical job nor a render claim for this transition. A live lease or unknown
physical execution prevents terminalization. Persistent scheduler invocation and
its interval/ownership must be provisioned and validated in G3B; no scheduler
resource is deployed here.

## Separate service authentication

`GoogleServiceIdentityVerifier` accepts only configured public RSA signing keys,
RS256, a Google issuer, exact callback audience, configured service-account email
and numeric subject, email_verified=true, and bounded valid timestamps. It rejects
end-user Supabase JWTs, wrong service identities, changed signatures and
client-selected key URLs. Queue headers grant no authority. The task handler has
no permissive verifier default or debug bypass. Public-key distribution/rotation
and actual Google-issued tokens must be validated in G3B; local cryptographic
contract evidence is not Google OIDC integration evidence.

The callable handler returns quickly after durable launch recording, and never
runs FFmpeg inside a task HTTP request. Cloud ingress/HTTP service hosting remains
a G3B integration task; no cloud HTTP server was deployed here.

## Immutable worker and blocking I/O

The executable entrypoint is `python -m engine.cloud_worker` with these fixed
arguments supplied by the trusted orchestrator:

```text
--launch-id UUID --organization-id UUID --job-id UUID
--event-id UUID --expected-fence INTEGER
```

No caller command, source URL, executable or FFmpeg argument enters this contract.
The dedicated internal DSN and storage configuration are environment-only. Local
mode uses G3_OBJECT_STORE=local, G3_LOCAL_STORE_ROOT, G3_SCRATCH_ROOT and
G3_WORKER_DSN. Prepared GCS mode uses G3_OBJECT_STORE=gcs and G3_GCS_BUCKET; its
fixed Google metadata ADC provider runs only when explicitly selected. That
runtime identity integration is NOT_VALIDATED.

`LaunchBoundMediaControl` checks all immutable fields and expected next fence while
holding the same organization/job locks as G1. It delegates the original G1 claim
inside the same PostgreSQL transaction and consumes the launch atomically. A
stale, consumed or cross-organization launch cannot acquire another generation.
`run_bound_worker` then invokes the unchanged G2 real media executor.

G3 adds an independent bounded lease keeper after the bound claim commits. Its
separate connection renews the same lease/fence while synchronous storage I/O is
blocked. Renewal failure or the whole-job deadline stops renewal, prevents later
control-plane transitions and lets G1 expiry/takeover recover. The keeper stops
future renewal loops and bounds its shutdown join when the invocation exits.
An already blocked SQL call can outlive that join under a network blackhole;
process termination and the deployment's database network timeout profile remain
G3B validation requirements. G2's native heartbeat still kills FFmpeg
processes when renewal fails. Python cannot forcibly interrupt an in-progress
stdlib HTTP read: storage request/upload deadlines remain mandatory, and late
object effects remain private immutable old-fence artifacts for reconciliation.
No failed keeper can accept a final manifest or overwrite a valid generation.

## Reproduction and evidence boundary

Use the hash-locked G3A environment and fresh disposable database prepared by
scripts/prepare_g3a_database.py; never apply test bootstrap SQL to a real Supabase
project. The complete runner executes all preserved suites and the new tests:

```sh
G3A_PYTHON=/tmp/ltc-g3a-venv/bin/python bash scripts/run_g3a_local.sh
# Focused suite against that fresh disposable PostgreSQL:
PYTHONPATH=integration:media_tests python -m unittest cloud_tests.g3a.test_tasks_run -v
```

The async suite exercises provider name-history loss, duplicate HTTP wake signals,
20 concurrent signal handlers and 20 competing worker claims, lost task responses,
429, uncertain jobs.run acceptance, pre-network controller crash, operation/execution
reconciliation, two-organization global slots, cross-tenant rejection, internal RLS,
immutable ledger fields, stale workers, real FFmpeg outputs with audio/dimensions/
duration/SHA-256 verification, and delayed real loopback HTTP I/O longer than a
one-second lease. A whole-job deadline test proves the keeper stops, no manifest
commits, and a newer G2 fence can recover. The preserved G2 suite independently
verifies supplied SRT changes decoded pixels and real SIGKILL/orphan recovery.

## References checked 2026-11-07

- [Cloud Tasks tasks.create and finite task-name retention](https://docs.cloud.google.com/tasks/docs/reference/rest/v2/projects.locations.queues.tasks/create)
- [Cloud Tasks duplicate delivery guidance](https://docs.cloud.google.com/tasks/docs/common-pitfalls)
- [Cloud Run jobs.run and fixed container overrides](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.jobs/run)
- [Cloud Run Execution completionTime and task counters](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.jobs.executions)

No G0/G1/G2 file was edited, no Creative OS checkout was touched, and no merge or
deploy is authorized by this contract or its local tests.
