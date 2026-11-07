# G2 / Issue #4 — executed decision

**GO for the isolated, local synthetic-media G2 scope. NO GO for production/SaaS.**
This is a DRAFT PR deliverable, not merge or deploy authorization. Final review
requires the unchanged G0/G1 workflows and new G2 workflow to succeed on the
exact final PR HEAD. The PR description supplies that final SHA and CI links;
this committed evidence document cannot contain its own future commit hash.

## Baseline and provenance

Main was confirmed as `5ce643e49a927d509dd240e0308b7960d17450db`, with G1 PR #3
merged before the G2 branch was created. Exact-main CI succeeded:

- [G0 main](https://github.com/alterio-png/let-them-cook-content-engine/actions/runs/42368751618)
- [G1 main](https://github.com/alterio-png/let-them-cook-content-engine/actions/runs/42368751620)

`AGENTS.md`, Issue #4 and the complete original G0/G1 docs, code, migrations,
tests and workflows were read. All 37 historical tracked files remain unchanged,
including all G0 hashes and the G1 modules/migrations/workflows. The new baseline
hash manifest and runner prove preservation. Creative OS main remains
`1a30934928a767fb851cd1d846318d6c60660394`; it was only queried, never cloned,
modified, reset, rebased or merged.

No new Python package was installed. Execution reuses G1's hash-pinned psycopg
3.2.13/typing-extensions 4.16.0 and pinned official PostgreSQL image. Runtime:
Python 3.12.14, PostgreSQL 16.15, FFmpeg 7.1.5-0+deb13u1, Linux x86_64.
FFmpeg SHA-256:
`e8a8d46f5225f3062cec7c07fb145d58ae73c603cb740dcd5bad34bfb54e455a`.
Native codec/distribution legal clearance remains a separate gate.

## Recorded complete execution

The [raw local log](evidence/G2_LOCAL_RUN.txt) records source
`36d027ea2f4fedd579611258316f107939aaa720` executed with a newly initialized
PostgreSQL and actual FFmpeg. Only this decision and evidence addition follows
that tested code. The final HEAD is independently rerun from a clean clone and
CI; those exact-SHA results belong to the DRAFT PR and its Actions artifact.

```sh
G2_PORT=55442 G2_PYTHON=/workspace/g1-venv/bin/python \
  G2_EVIDENCE_DIR=/workspace/g2-final-demo \
  sh scripts/run_g2_local.sh
```

Exit code **0**: **12 G0 + 36 G1 + 49 G2 = 97 tests passed**, then the complete
synthetic demo finished with PostgreSQL state `completed`. The G2 suite includes
18 actual filesystem ObjectStore tests, 10 real native-media tests, 3 persistent
queue tests, 3 PostgreSQL security tests and 15 full pipeline/failure tests.

| Acceptance | Actual evidence |
|---|---|
| Full path | Trusted rights/metadata ingestion → authenticated submission → PostgreSQL job/outbox → G1 dispatcher → persistent named local queue → worker → actual FFmpeg → persistent private ObjectStore → byte readback/ffprobe → fenced final DB manifest. |
| Formats | Three MP4s, 270×480, 320×320 and 320×400; actual byte/hash, duration and audio checked. |
| Captions/audio | Supplied SRT changes decoded output pixels; decoded PCM remains nonzero with the expected synthetic 440 Hz tone. |
| Reproducibility | Identical immutable input rendered in separate logical jobs produces exactly equal MP4 hashes on the same pinned execution environment; no cross-version bit identity is claimed. |
| Idempotency | 100 distinct PostgreSQL connections submit one media operation: 1 job, 1 outbox, 1 media spec and one 6-cent fictitious reservation. Completed replay verifies objects without another attempt/generation. |
| Tenants | Two organizations execute concurrently; DB reads are scoped, forged writes/submission denied, composite FKs enforced, store reads/writes/deletes and unsafe symlink/traversal paths rejected. |
| Real crash | Actual worker SIGKILL after first durable output leaves no authoritative manifest; lease expiry/takeover produces a newer valid generation. Old objects/scratch/staging are detected and safely removed; current-generation fragments remain protected. |
| Hard kill during encode | Killing the supervisor while actual FFmpeg decodes terminates the native process through parent-death protection. |
| Fencing | Stale/expired worker cannot replace completed successor; unchanged G1 tests prove lease renewal race, active quota and sweep recovery contracts. |
| Failure/retry | Download/publication checkpoint faults, native encoder file-size failure, timeout/cancel, DB-trigger failure before commit and lost completion response are tested. State, classified metrics/audit, bounded attempts and object recovery are asserted. |
| Queue reliability | Concurrent named creation persists one task; reopening preserves it; 200 reads with garbage collection disabled do not grow file descriptors. |
| Regression | Both original suites run unchanged, plus all baseline checksums. |

## Actual persistent sample

[G2_SAMPLE_OUTPUTS.json](evidence/G2_SAMPLE_OUTPUTS.json) contains the complete
sample manifest, generation, object keys and paths produced by the recorded run.
CI exports actual synthetic MP4/SRT inputs and final MP4/manifest objects as a
private review artifact, rather than committing binary media to source control.

| Output | Dimensions | Duration | Audio | Bytes | SHA-256 |
|---|---|---|---|---:|---|
| vertical | 270×480 | 1.2 s | yes | 34094 | `779bf746fa8ced463a9e3318a968f7cc40de9e0dc56ba5fd772f4818c1d2d4d6` |
| square | 320×320 | 1.2 s | yes | 36684 | `30e361c7a0e02db0fd6882b31d3981069d484e8e78405205c6265dc001dc1fd9` |
| feed | 320×400 | 1.2 s | yes | 36659 | `bed83208cd907baf571ddf252c038cdb50f5235d97de2731b30601e9ae699876` |

## Review findings and validation limits

Independent review found and corrected missing parent-death termination of native
FFmpeg, SQLite connections relying on garbage collection for closure, and staging
fragments omitted from orphan reconciliation. New regression tests prove those
corrections, including conditional staging deletion against observed metadata.

An early shared development-database run had test interference from concurrent
global dispatcher/fault injections. Another run overlapped an incompatible native
launcher edit. Neither is used as acceptance evidence. The recorded complete run
uses an exclusive fresh database, settled code and job-scoped fault injection.

Identity remains a trusted fixture, storage/fila are local, inputs are synthetic,
and isolation is Linux process/rlimit plus private scoped filesystem—not a
production hostile-media jail. There is no measured cloud scale, commercial cost,
remote S3/R2 implementation, real Supabase Auth, exactly-once physical processing,
AI, publication, customer media, paid service, deploy or automatic merge.

See [G2_PIPELINE.md](G2_PIPELINE.md) for reproduction, contracts, known limits and
rollback. Actual native binaries still require distribution/security/legal review;
production readiness remains **NO GO** regardless of this local gate's outcome.
