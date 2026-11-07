# G3A.1 decision — Issue #8

**GO for the tested local shared PostgreSQL upload authority.** This gate
removes the SQLite authority blocker for the explicitly selected shared HTTP
application, within the compatible private local ObjectStore contract.
Cloud/production operation and G3B remain **NO GO / NOT_VALIDATED**. Monetary
cost remains **UNKNOWN**. No merge, deployment, cloud resource or G3B execution
is authorized or performed by this decision.

## Reproducible source proof

Main was confirmed at `7a271382487ac4d16e278e5ccb8c345279b3cc16`, with PR #7
merged and all four post-merge historical workflows successful. Implementation
and tests were committed at `1eacb889fbef95650998f7136e1d481e97c9f047`, then
cloned using `git clone --no-local` into an independent clean checkout. The
benchmark confirms that exact source SHA and `source_worktree_clean: true`.
Later delivery commits add only documentation/evidence. The PR must identify
the exact final head and successful CI runs; final-head CI artifacts repeat
the tests and record their own source SHA.

Raw [runner log](../evidence/g3a1/clean-clone/clean-clone-full.txt),
[provenance](../evidence/g3a1/clean-clone/provenance.json),
[hash verification](../evidence/g3a1/clean-clone/baseline-hashes.txt),
[shared report](../evidence/g3a1/clean-clone/shared-benchmark/summary.json),
[HTTP/resource events](../evidence/g3a1/clean-clone/shared-benchmark/events.jsonl),
[historical benchmark](../evidence/g3a1/clean-clone/historical-benchmark/summary.json)
and [IaC log](../evidence/g3a1/clean-clone/clean-clone-iac.txt) are committed.
Their hashes are in `evidence/g3a1/clean-clone/SHA256SUMS.txt`.

| Gate | Actual clean-clone result |
| --- | --- |
| Historical G0 | 12 tests, real FFmpeg, PASS |
| Historical G1 | 36 real PostgreSQL tests, PASS |
| Historical G2 | 49 tests, actual media/objects/concurrency/SIGKILL, PASS |
| Historical G3A contracts | 118 tests, PASS; local contracts are not real cloud validation |
| Historical official local Auth | 10 tests with actual GoTrue/PostgreSQL, PASS |
| Shared upload regressions | 44 tests against real PostgreSQL/private disk/HTTP processes, PASS |
| Shared official local Auth | 7 tests, two independent HTTP processes, real sessions/logout and FFmpeg/download, PASS |
| Total | **276 tests, no skips; runner exit 0** |
| Historical preservation | All **114** historical file hashes identical; only additive repository files |
| IaC | Terraform 1.17.1/google provider 8.10.0: fmt/init with backend disabled/validate, zero errors or warnings; no plan/apply |

The initial local attempt stopped before tests because port 55450 could not
be bound. It was not classified as PASS. The successful full run used free
loopback ports 55472/55473; the aborted log is retained separately.

## Shared authority and concurrency proof

The clean-clone benchmark used HTTP processes **166900** and **166905**, then
new process **167619** with a distinct working directory. They shared real
PostgreSQL 16.15 and private immutable LocalObjectStore; **zero SQLite ledger
files** were created.

- 100 simultaneous logical duplicate reservations produced **one grant/key/
  expiry/capability**, one PostgreSQL reservation and one 128-byte quota charge.
  There were **144 physical HTTP attempts**: 100 successful 201 responses and
  44 explicit 503 backpressure responses followed by bounded retries.
- 20 simultaneous distinct requests against a ten-grant/1,280-byte organization
  limit produced **10 admitted and 10 denied with 429**. Actual committed
  counters equal ten retained rows and 1,280 bytes, without quota overrun.
- Publication on A, receipt read on B, SIGKILL of A after receipt commit and
  replay on fresh C preserved exactly one grant, receipt and charge. Actual
  object hash/size were read back and verified; cross-organization lookup was
  denied. Mid-publication SIGKILL and database failures are separately covered
  by the fault tests.

Duplicate physical-attempt latency p50/p95/p99 was **180/274/301 ms**; logical
latency including backoff was **281/477/486 ms**. Distinct-request physical
p50/p95/p99 was **76/100/100 ms**. The sampled aggregate client/descendant RSS
peak was **170,409,984 bytes**; aggregate measured user/system CPU was
**1.212314/0.269647 seconds** over approximately **1.465 seconds**. These figures
exclude the PostgreSQL container and kernel/cgroup overhead; shared pages may
be counted repeatedly and short peaks missed. They prove this local cohort,
not sustained throughput, cross-host storage behavior or cloud capacity.

## Recovery and security coverage

Real PostgreSQL triggers and actual process termination exercise failure
before/after claim/storage, absent receipt after durable storage, failed SQL
receipt/audit/quota transactions, lost commit response, quota refund rollback,
concurrent publication, restart, stale fencing and missing/corrupt/mismatched
object metadata. Only never-claimed expired grants refund once; uncertain
operations remain charged. Exact-body authenticated resume works after the
original grant TTL without a second charge. Revoked owners can be recovered
through the restricted service SQL role without giving it table DML.

The security suite exercises nonowner role permissions, denied raw mutations,
RLS, private helper ACL/search paths, composite receipt foreign keys, immutable
reservation/receipt identity, organization/owner boundaries and current
membership. Official local Auth additionally proves actual logout/session
revocation, metadata/header rejection, service-role rejection, deleted-user
revocation with historical receipt retention, and cross-replica/restart access.
Synthetic cryptographic fixtures used for load testing are explicitly labeled
and do not substitute for that official Auth gate.

Independent review found two lease/deadline defects during implementation;
both were corrected and rechecked on real PostgreSQL. Effective database lease
duration now governs renewal. SQL deadline checks after actual locks and writes,
plus remaining-budget statement timeout, reject overdue completion. The full
44-test run includes these regressions. No material review finding remained.

## Remaining boundaries

Uploaded and uncertain reservations retain quota and audit history; there is
no destructive object GC or automatic migration of existing SQLite data.
Switching an active organization to an empty ledger is unsafe. An arbitrary
blocking ObjectStore call needs process supervision for forcible termination;
SQL receipt admission is bounded, while COMMIT/fsync acknowledgement is not a
hard real-time guarantee. See [runbook](G3A1_RUNBOOK.md) for rollout, retention
and rollback requirements.

Official GoTrue is a real local component, not hosted Supabase or a full
Supabase deployment. Local CLI security advisors were unavailable without the
full stack on port 54322 and remain **NOT_VALIDATED**. Real hosted Auth, remote
Google OIDC, IAM, GCS/R2/S3, Cloud Tasks, Cloud Run, autoscaling and billing
remain **NOT_VALIDATED**. The existing G3B staging plan is preserved and was not
executed. Creative OS main was read-only verified at
`1a30934928a767fb851cd1d846318d6c60660394` and never modified.
