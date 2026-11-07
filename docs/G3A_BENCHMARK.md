# G3A local benchmark methodology

`scripts/benchmark_g3a.py` exercises actual PostgreSQL transactions, durable local
queue handoff and G2 FFmpeg rendering. It is local evidence. Google Cloud
Storage, Cloud Tasks and Cloud Run throughput, availability, billing and
autoscaling remain **NOT_VALIDATED** by this benchmark.

Run in a clean clone after the isolated G3A runner has created its disposable
database and applied migrations. Do not run concurrently with any other test
traffic in that database. The script rejects a non-loopback connection, a
database outside `g1_*`, service/host-address overrides and pre-existing jobs.
Use the pinned dependencies and PostgreSQL image documented by the G3A runner.

```sh
G1_TEST_DSN='postgresql://postgres:LOCAL_DISPOSABLE_PASSWORD@127.0.0.1:55439/g1_test' \
  .venv/bin/python scripts/benchmark_g3a.py \
  --output evidence/g3a/benchmark --jobs 20,50 --workers 2 \
  --duration-ms 300 --per-org 1
```

The benchmark reuses G1/G2 laboratory fixture helpers, creates synthetic
testsrc/sine media and a supplied SRT, and cleans only its own random tenant
identities, rows, private object directories and scratch files. Reports contain
no database DSN or credentials. `summary.json` is machine readable;
`events.jsonl` preserves timestamped resource samples and job events. A failed
assertion exits nonzero and writes `FAILED`, including the error class without
printing potentially sensitive database exception text.

## Workload and assertions

- One hundred simultaneous authenticated PostgreSQL function submissions cross
  a barrier. They share one immutable media request key and must create one
  job, outbox event, media spec, admission reservation and final authoritative
  completion. This measures the SQL boundary; it is not one hundred HTTP calls.
- Twenty and fifty distinct jobs form separate cohorts, divided between two
  organizations. Every job is committed and dispatched to the local durable
  queue before execution starts. A two-thread executor bounds global execution
  in this single process; PostgreSQL enforces each organization's active-job
  quota. This demonstrates neither a distributed global worker cap nor cloud
  autoscaling. Each job renders all three real smoke profiles at 270×480,
  320×320 and 320×400 with preserved audio and burned supplied SRT.
- Completion verifies authoritative tenant/fence generations and actual object
  hashes/sizes. The unchanged G2 worker reads every publication back and probes
  real streams before committing. A completed replay repeats that verification
  and must preserve the manifest and attempt count. One completion audit and
  one outbox/spec per logical job are required. The sum of immutable admission
  reservations must equal the organization's reserved counter.
- Recovery injects a response lost after durable local-queue acceptance, one
  transient local object write error and one worker exception after a durable
  output object. Retries must retain a single named task, use real database
  backoff and produce the final authoritative generation. The script verifies
  backoff, then moves `available_at` into the past to accelerate laboratory
  scheduling; retry timing is therefore not a wall-clock backoff benchmark.
  A completion attempted with the obsolete lease/fence must be rejected.
  Reconciliation must remove orphaned output objects, be idempotent and preserve
  all authoritative hashes. An injected exception is not a hard process kill;
  the unchanged G2 SIGKILL/native-child recovery tests supply that separate proof.

## Measurements and limits

Queue latency is the first actual render-attempt start minus database job
creation. End-to-end latency is the completed job update minus creation. Both
use PostgreSQL timestamps and report all per-cohort observations through
linearly interpolated p50/p95/p99. Cohort throughput divides successful jobs by
the execution phase's measured wall time, after queueing and before the extra
completed replay verification. All jobs contribute to success/error and retry
counts; any incomplete job fails the entire run.

CPU measurements use `getrusage` deltas: Python process user/system CPU plus
all reaped native child user/system CPU. A Linux `/proc` sampler every 50 ms
walks the process-descendant tree and sums resident bytes, including live FFmpeg
and ffprobe processes. It reports sampled RSS/process/native-media highwater
and checks that a native-media subprocess was observed. Summed RSS can count
shared pages repeatedly and can miss peaks shorter than the sampling interval.
These metrics exclude the separate PostgreSQL container, kernel/cgroup overhead,
other tenants, infrastructure agents and external network/storage resources.
They are not cloud memory sizing or isolated production resource accounting.

A concurrent PostgreSQL sampler records active live leases by organization.
In-process hooks independently record claimed worker highwater. Any observed
organization/global cap violation fails the benchmark. Sampling can miss short
transitions; transactional quota and fencing tests remain required and the
benchmark does not replace them.

Reports include actual video bytes, CPU seconds per completed job and bytes per
job. Currency, price and monetary cost are **UNKNOWN** because no cloud billing
or real price input is measured. Existing G1 `cost_cents` values are synthetic
admission reservations and must not be presented as measured cloud costs.
Small smoke profiles and 300 ms synthetic clips cannot establish production
video capacity, sustained load, codec suitability or a cloud cost forecast.

`cloud_tests/g3a/test_benchmark.py` tests measurement math, the descendant-memory
boundary and DSN isolation, then runs ten duplicate SQL submissions, three real
queued jobs and the three real local recovery cases. The larger 100/20/50 run
is a distinct evidence step. Actual run logs, source revision, observations and
the G3A gate decision belong in the reviewed evidence report; this methodology
alone declares no successful execution.
