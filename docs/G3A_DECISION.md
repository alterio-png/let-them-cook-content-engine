# Issue #6 / G3A — decision and executed evidence

**CONDITIONAL GO for G3A's tested local Auth, HTTP, cloud contracts and reviewable
infrastructure. NO GO for cloud deployment, production or implicit G3B execution.**
All actual cloud services remain unvalidated. The conditions below are explicit
staging gates, not successful integration claims. The deliverable remains DRAFT.

## Provenance and preservation

- Main was current at `366f0f01b9c9d349ea9270d4051779deecd105c9`, with PR #5 merged
  and G0/G1/G2 checks successful before branch creation.
- Branch: `feat/g3a-cloud-contracts-01`. All **61 baseline tracked files** remain
  byte-for-byte unchanged; the G0 and G2 manifests plus the new G3A manifest are
  checked by the runner. No historical migration, module, test or workflow changed.
- Creative OS was only queried; its main remains
  `1a30934928a767fb851cd1d846318d6c60660394`. No checkout or writes there.
- Exact final PR SHA, checks and artifact links are recorded in the PR description;
  this committed record cannot contain its own future commit hash.

## Complete clean-clone execution

Source `4cc282befdd6e6b44c52e5b66f6e58ff3173b19a` was cloned without local changes.
Only this decision/evidence addition follows that tested code. The final PR HEAD
is independently tested by CI and another clean checkout; results are linked
from the PR. Commands and raw evidence:

```bash
G3A_PYTHON=/workspace/g3a-venv/bin/python G3A_PORT=55451 \
  G3A_EVIDENCE_DIR=/workspace/g3a-evidence/clean \
  bash scripts/run_g3a_local.sh
sh infra/g3a/validate_toolchain.sh
```

Both exited **0**. [Complete log](evidence/G3A_CLEAN_RUN.txt):
**216 tests passed: 12 G0 + 36 G1 + 49 G2 + 109 G3A contracts + 10 official Auth**.
The 109 comprise 9 crypto, 4 benchmark, 24 GCS, 19 HTTP, 33 IaC policy and 20
async/launch tests. No integration was silently skipped in the complete runner.
Local PostgreSQL and FFmpeg are real, with the original pinned dependencies.
Python 3.12.14, PostgreSQL 16.15 and FFmpeg 7.1.5-0+deb13u1 were observed.

Official Supabase Auth/GoTrue **v2.197.0** ran 75 real Auth migrations in its own
isolated PostgreSQL database. Tests called real signup, password sign-in and
logout. Signed JWT verification plus a narrow live-session query rejected logout,
deleted users, expired retained sessions, forged signatures and service-role use.
Actual authenticated HTTP/RLS calls reached idempotent submission, real FFmpeg
worker, current status and a private download with the authoritative hash.
The separate nine locally signed JWT tests do not substitute for this evidence.

[IaC log](evidence/G3A_IAC_RUN.txt): official Terraform **1.17.1**, hash verified;
Google provider **8.10.0**, locked and signature checked; local `init -backend=false
-lockfile=readonly`, formatting and `validate`: **valid=true, 0 errors, 0 warnings**.
No plan/apply, Google credential or Google API request was used. Effective deployed
IAM, network and billing behavior are not proven by schema validation.

## Measured local load

[Compact report](evidence/G3A_BENCHMARK_SUMMARY.json); full per-object hashes and
timestamped `events.jsonl` are retained in the CI artifact. Workload: synthetic
300 ms source clips, three small smoke renditions, two organizations, two worker
threads globally and at most one active job per organization. One hundred
simultaneous SQL submissions created exactly one logical job/outbox/spec and
one reservation, followed by a verified authoritative completion.

| Queued jobs | Completed/errors | Execution wall s | Jobs/s | E2E p95 s | CPU s, process + reaped children | Sampled aggregate RSS peak |
|---:|---|---:|---:|---:|---:|---:|
| 20 | 20 / 0 | 14.316 | 1.397 | 14.600 | 23.548 | 155422720 bytes |
| 50 | 50 / 0 | 35.421 | 1.412 | 34.867 | 57.125 | 167993344 bytes |

The observed highwater was **2 global, 1 per tenant**. The benchmark also completed
three recovery jobs: accepted-but-lost local queue response, storage write error,
and worker exception after durable publication. Backoff, a single named task,
old-fence rejection, verified output hashes and idempotent orphan cleanup passed.
An exception is not a hard kill; the unchanged G2 real SIGKILL tests separately
prove worker/native-child interruption recovery.

These figures are local observations, not cloud capacity or a production SLA.
CPU excludes the separate database container; sampled summed RSS can double-count
shared pages and miss short peaks. Auth/DB/kernel overhead and cloud allocation
are not included. Monetary cost is **UNKNOWN**, never zero or fictitious G1 cents.
See G3A_BENCHMARK.md for exact timing, retry-clock and measurement boundaries.

## Validation matrix and conditions

| Boundary | Status | Evidence or remaining gate |
|---|---|---|
| G0/G1/G2 regressions | PASS_LOCAL | 97 unchanged tests + all baseline hashes. |
| Official Auth component + restricted SQL/HTTP | PASS_LOCAL | 10 real integration tests, not a hosted/full Supabase stack. |
| GCS REST adapter + real G2 media | PASS_LOCAL_CONTRACT | Actual local HTTP faultserver, resumable/conditional requests, byte hashes and real FFmpeg; **GCS remote NOT_VALIDATED**. |
| Cloud Tasks/Run contracts + PostgreSQL | PASS_LOCAL_CONTRACT | Duplicate/history expiry, 429, lost acceptance, immutable launch ledger, strict physical reservations, OIDC crypto with local keys, stale fences and slow-I/O renewals. **Remote services/Google-issued OIDC NOT_VALIDATED**. |
| IaC tool/provider schema | PASS_LOCAL | Actual pinned Terraform/provider validation and 33 negative policy tests; **deployed IAM/network NOT_VALIDATED**. |
| Shared cloud HTTP upload quotas/receipts | **BLOCKED** | Current durable ledger is single-host SQLite. Shared PostgreSQL persistence and multi-replica/restart tests are required before any cloud API image is enabled. |
| Hosted Supabase/full-stack advisors/key rotation | **NOT_VALIDATED** | CLI local advisors could not connect to a full stack at 54322. Real component/RLS tests do not replace hosted advisors or key-rotation proof. |
| Cloud runtime images/HTTP adapters/TLS/VPC | **BLOCKED** | IaC has explicit false authorization/image approval guards. Container/native parser/license hardening and actual private routing need G3B review. |
| Remote run acceptance without operation identity | **BLOCKED_UNCERTAIN** | Reservation stays occupied; no blind repeat or automatic slot release from a completed DB row. Operator/provider proof is required. |
| Cloud costs, sustained 1080p, billing and teardown | **NOT_VALIDATED** | No paid resource or real cloud operation exists; G3B is planned only. |

## Review corrections

Independent review and actual tooling found and corrected claimed-worker crash
reconciliation, premature logical-completion slot release, lease renewal during
blocking storage I/O and the completion/renewer race. Tests cover each. A launch
operation marked done is insufficient: authenticated execution terminal counters
are required before releasing its physical reservation. Local tests do not prove
provider semantics in the real service. An unrestricted G2 sweep must never
bypass launch admission in cloud.

Terraform validation found two real IaC defects (literal JSON variable validation
expressions and an invalid empty budget updates block); both were corrected with
regression tests before the clean-clone result. Details and historical diagnostics
are in infra/g3a/TOOLCHAIN_VALIDATION.txt and the clean validation log.

## Deliverable and next boundary

Architecture/alternatives/trust model: G3A_ARCHITECTURE.md. Components: G3A_AUTH.md,
G3A_GCS.md, G3A_ASYNC.md and G3A_IAC.md. Reviewed tooling: G3A_TOOLING_REVIEW.md.
Reproduce using the hash-locked requirements and new runner; no production
credential is required. Local test containers/networks/temporary media are removed.

The [G3B plan](G3A_COSTS_AND_G3B.md) proposes a separate USD 10 ceiling, explicit
human authorization, isolated staging, real 1/5 minute synthetic 1080p tests,
measured costs, bounded concurrency and verified teardown. **It was not executed
and that budget is not approved.** No cloud resources, deploy, customer media,
production secrets, paid service, merge or production publication was used.
