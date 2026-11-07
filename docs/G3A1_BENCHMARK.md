# G3A.1 shared upload benchmark

`scripts/benchmark_g3a1.py` measures real HTTP requests to two independent OS
processes. Each process has its own PostgreSQL upload authority, connection
semaphore and private working directory. Both use the same disposable local
PostgreSQL database and compatible persistent private LocalObjectStore. No
SQLite upload ledger is created. This is local shared-authority evidence;
cloud throughput, availability, storage compatibility, autoscaling and billing
remain **NOT_VALIDATED**.

Run after the G3A.1 isolated runner has applied all migrations, with the pinned
Python dependencies and PostgreSQL image documented by that runner. Run the
benchmark after other suites have finished, without competing test traffic:

```sh
G1_TEST_DSN='postgresql://postgres:LOCAL_DISPOSABLE_PASSWORD@127.0.0.1:55440/g1_test' \
  .venv/bin/python scripts/benchmark_g3a1.py --output evidence/g3a1/benchmark
```

The script rejects a missing DSN, a non-loopback host, a database outside
`g1_*`, service overrides and host-address overrides. The administrative
laboratory connection creates only random fixture organizations/users, quota
rows and a disposable restricted API login. Request handling uses that
nonowner, non-bypass login. The fixture deletes its own rows, login and private
files, and stops/joins its children. Reports omit DSNs, passwords, JWTs and
upload capabilities. `summary.json` contains assertions and measurements;
`events.jsonl` contains timestamped resource samples and physical HTTP attempt
statuses/latencies. A failed assertion exits nonzero and records `FAILED` and
the exception class, without recording database exception details.

## Workload and evidence boundaries

- One hundred threads cross a barrier and make authenticated **HTTP POSTs**
  with the same immutable request key, size and SHA-256, split between the two
  servers. All logical operations must obtain the identical grant, expiry and
  signed upload path. Actual PostgreSQL rows must contain one charged
  reservation, exactly 128 reserved bytes and no receipt yet. There are no
  one-hundred-connection PostgreSQL calls hidden behind this claim: each API
  replica bounds database use to four connections.
- Twenty distinct keys cross a second barrier against a different organization's
  shared limit of ten grants and 1,280 bytes. Exactly ten HTTP operations must
  return 201 and ten must return quota status 429. Actual counters must equal
  the count/sum of retained reservations and remain within both limits. The
  two cohorts use separate organizations, so the duplicate cohort cannot
  consume the quota-race budget.
- A 503 is recorded as explicit local backpressure. The operation retries with
  the same immutable request, at most sixteen HTTP attempts, with bounded
  25–250 ms exponential backoff and a deterministic 0–18 ms stagger. Every
  physical attempt contributes to status counts and latency percentiles;
  initial/final statuses, retry counts and logical operation latency are
  reported separately. Non-503 failures are not silently retried. The default
  cohort therefore makes at least one hundred real duplicate HTTP requests;
  it may make more physical attempts when capacity is exhausted. This is
  bounded admission evidence, not a claim that all requests complete without
  backpressure.
- The accepted bytes are uploaded through replica A. Replica B must return
  their receipt; the persistent private object must have the actual expected
  hash and size. A is then terminated forcibly **after a valid receipt has
  committed**. A fresh process C, with another private working directory,
  must recover the same grant, expiry and receipt. Replaying the reservation
  must retain one quota charge and one receipt. A different organization's
  authenticated identity must be denied access to that receipt.

This benchmark does not kill a writer between object publication and receipt
commit. The separate real PostgreSQL/disk/SIGKILL fault tests establish
mid-publication recovery, conservative uncertain quota retention, stale
fencing rejection and reconciliation. Official Supabase Auth is also a separate
gate: benchmark JWTs are synthetic cryptographic **LOCAL_CRYPTO_CONTRACT**
fixtures, not evidence of an external issuer or session service.

## Resource and latency measurements

All durations use a monotonic clock. HTTP p50/p95/p99 use linear interpolation
over every observed physical request. Logical latency includes backoff. Cohort
wall time includes the barrier/executor phase; it is not cloud service latency
or sustained production throughput. HTTP client timing includes local socket
queueing and JSON handling. Reports include the exact source SHA, Python,
platform, logical CPU count and PostgreSQL version.

CPU is the `getrusage` delta for the benchmark process plus all reaped child
processes. HTTP servers are stopped and joined before the final sample, so
their user/system CPU is included. A Linux `/proc` sampler every 50 ms sums
resident bytes across the client and all live descendants, including both
servers and Python process helpers. Shared pages can be counted repeatedly;
peaks shorter than the sampling interval can be missed. The separate
PostgreSQL container, kernel/cgroup overhead and unrelated host processes are
excluded. These figures do not establish production memory limits, isolated
per-request resource use or cloud capacity.

The report explicitly sets monetary cost to **UNKNOWN**. Quota bytes/counts
are application admission counters. No price, billing unit, cloud request,
egress or infrastructure cost is measured. Real GCS/Cloud Tasks/Cloud Run
integration remains **NOT_VALIDATED**; this workload neither starts G3B nor
creates cloud resources.

The small measurement test runs the same real path with ten duplicate HTTP
operations and four distinct keys competing for two slots. The full default
100/20 run is a separate required evidence step. This document describes the
method; only a completed report and matching logs support a gate decision.
