# G3A.1 local validation and operational contract

## Reproduce from a clean clone

Use Linux, Docker, Python 3.12 and real FFmpeg/ffprobe with libx264, AAC and
libass. Install only the historical reviewed hash-locked requirements:

```sh
git clone https://github.com/alterio-png/let-them-cook-content-engine.git
cd let-them-cook-content-engine
git checkout feat/g3a1-shared-upload-ledger-01
python3.12 -m venv /tmp/ltc-g3a1-venv
/tmp/ltc-g3a1-venv/bin/python -m pip install --require-hashes --only-binary=:all: -r requirements-g3a.lock
G3A1_PYTHON=/tmp/ltc-g3a1-venv/bin/python \
  G3A1_EVIDENCE_DIR=/tmp/ltc-g3a1-evidence \
  bash scripts/run_g3a1_local.sh
sh infra/g3a/validate_toolchain.sh
```

The additive runner first executes the unchanged historical runner: all
G0/G1/G2/G3A suites, actual 20/50-job media benchmarks and official Auth tests.
It then creates another fresh loopback PostgreSQL database, applies historical
plus additive migrations, executes every shared-upload test and the full
100-duplicate/20-distinct two-process HTTP benchmark, and starts a separate
official GoTrue/PostgreSQL laboratory for the shared Auth tests. Auth containers
use temporary secrets, private networking and ephemeral volumes. Runners remove
their own containers/volumes on exit. No hosted database or paid API is used.
The historical Terraform validation downloads pinned reviewed tooling for
`fmt`, backend-disabled `init` and `validate`; it never runs plan/apply.

Ports 55450 and 55460 must be free; override `G3A_PORT` and `G3A1_PORT` if needed.
Auth ports are selected dynamically. Laboratory database preparation rejects
non-loopback targets and existing prepared shared schemas. New shared helpers
also reject libpq service/hostaddr overrides. No suite may be pointed at a
production database. Keep evidence outside the checkout so benchmark clean-tree
checks remain meaningful. A failed command is a failed gate, even if earlier
commands passed.

## Future rollout prerequisites (not executed here)

1. Stop legacy upload admission for the affected organizations; drain and
   reconcile every legacy writer/reservation before moving authority.
2. Preserve legacy ledgers and private objects. Review an explicit migration
   of outstanding counters, ownership, requests, expiry and receipts; this PR
   provides no automatic SQLite-data import. Never give an active organization
   independent empty SQLite and PostgreSQL quota budgets.
3. Apply the additive migration with a trusted migration principal. Provision
   organization limits from reviewed authoritative usage. The private schema
   must stay outside Supabase Data API exposed schemas.
4. Provision the restricted API/session-reader login and, separately if needed,
   a login for the NOLOGIN `g3a1_upload_reconciler` role. Never give the HTTP
   login that recovery role or database ownership. Keep credentials and signing
   keys server-side.
5. Explicitly construct the shared authority/application/server on all replicas
   using the same PostgreSQL, compatible immutable private store and signing
   key. No fallback to SQLite is permitted. Bound combined HTTP, upload-ledger,
   session and render connection budgets against actual database capacity.
6. Validate remote storage consistency, generation preconditions, authenticated
   ingress, supervisor termination, IAM and staging failure recovery under the
   separate G3B plan before any cloud use. Local evidence does not authorize it.

## Reconciliation and retention

For a verified owner, call the receipt/reconcile route after a lost response.
A live lease returns busy/conflict; retry only after bounded backoff. A new
fenced claim verifies actual bytes before recording the original receipt.
For a previously claimed upload with missing bytes, use authenticated `resume`
with exactly the original body. The expired original PUT capability cannot
admit a new operation. An expired never-claimed grant cannot be resumed.

Only expired reservations never claimed (`fence = 0`) release their byte/count
charge automatically, exactly once in the transaction that records expiry.
Use bounded expiration batches and inspect metrics for backlog. Uploaded and
uncertain operations retain quota, including after owner revocation/deletion.
The dedicated SQL recovery role can reconcile those historical owners without
granting raw table access; its permissions and recovery are integration-tested.

Do not delete a currently missing, uncertain, leased or valid-receipt object as
routine orphan cleanup. A paused writer may still publish. This gate implements
safe classification, recovery and unused-grant expiry, with **no destructive
object GC or automatic uploaded/uncertain refunds**. Reservations, receipts
and audit events are retained. A future release policy must prove all possible
writers drained/fenced, conditionally delete the exact generation and commit a
single explicit quota release. Missing bytes alone are insufficient evidence.

Monitor retained bytes/count against caps, publishing/uncertain backlog and
age, quota refusals, backpressure, lease losses and reconciliation events.
Investigate persistent uncertainty; do not repeatedly extend limits to hide a
leak. Application counters are not cloud billing. Log event codes/identifiers
only, without JWTs, authorization headers, capabilities, DSNs or object bytes.

## Rollback

Stop new shared admissions, drain requests and stop/fence known writers.
Preserve the PostgreSQL schema, quota state, receipts, events, shared signing
key and private store. Keep shared read/reconciliation available under the
same authorization rules. Do not switch admitted organizations back to an
empty SQLite ledger, drop the schema, refund uncertain work or delete valid
files. Restoring historical code is not a data rollback. A destructive down
migration or production rollout requires a separate reviewed plan.
