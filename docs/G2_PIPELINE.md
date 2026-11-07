# G2 real media pipeline — Issue #4

G2 follows the operational Issue numbering. Historical G0 gate names and G0/G1
files are preserved. This is a real **local synthetic-media** pipeline, with no
cloud credentials, paid service, AI model, social publication or deploy.

## Reproduce from a clean checkout

Use Linux x86_64, Python 3.12, Docker and FFmpeg/ffprobe with libx264, AAC and
libass. Python dependencies and PostgreSQL image are the reviewed G1 pins; no new
package dependency was added. All G0/G1 code, SQL, tests, workflow and docs remain
byte-for-byte preserved. `G2_BASELINE_SHA256SUMS.txt` covers all 37 tracked files
from main `5ce643e49a927d509dd240e0308b7960d17450db`.

```sh
python3.12 -m venv /tmp/ltc-g2-venv
/tmp/ltc-g2-venv/bin/pip install --require-hashes --only-binary=:all: -r requirements-g1.lock
G2_PYTHON=/tmp/ltc-g2-venv/bin/python \
  G2_EVIDENCE_DIR=/tmp/ltc-g2-demo \
  sh scripts/run_g2_local.sh
```

Choose a new evidence directory each time. `G2_PORT` overrides the loopback
PostgreSQL port (default 55438). The runner checks both preservation manifests,
starts a disposable PostgreSQL 16.15 database, applies 001/002/003, executes G0,
G1 and G2 suites, and runs the real end-to-end synthetic demo. Its local database
is removed on exit. The private evidence directory persists: `summary.json`,
original synthetic input/SRT, SQLite transport and immutable MP4 objects remain.
No test silently skips an unavailable database or encoder.

CI executes the same command and uploads the test log, summary, real input/SRT
and exported MP4/manifest objects as its artifact. The log and summary contain
UUIDs and checksums for synthetic media only. CI artifacts are deliberate review
exports, not a public object storage service.

## Transaction and identity boundary

`ingest_media` is a trusted fixture/importer helper with an administrator-owned
database connection. It checks and locks actor membership, requires an explicit
rights flag, snapshots bounded bytes to private scratch, probes actual H.264 MP4
with optional AAC, validates supplied plain UTF-8 SRT and stores immutable input
objects. It is not an upload API or JWT verifier. Only synthetic test footage is
authorized here. Do not expose this connection or arbitrary SQL to end users.

Migration 003 adds immutable `media_bindings` and `media_job_specs` plus internal
`media_attempt_metrics`. Authenticated callers invoke `submit_media_render` with
the trusted auth.uid fixture; authorization and tenant reads use PostgreSQL RLS.
Its transaction calls the unchanged G1 `submit_render`, atomically retaining
one job, outbox, audit and reservation under duplicate requests. An immutable
asset ID binds source bytes and optional captions; changing a caption requires
a new asset. G2's explicit `media_digest` hashes the G1 request digest and the
source/caption binding digest. No caller-controlled caption path enters FFmpeg.

## Execute and verify

The unchanged G1 `Dispatcher` hands a stable outbox event to `LocalQueue`, a
private durable SQLite named-task adapter with atomic deduplication and explicit
connection closure. Restarting it preserves tasks. It is not Cloud Tasks and
does not model cloud retention. A queue message is an acceleration: G2's bounded
PostgreSQL sweep finds only G2 specs and recovers due retries/expired leases.

`MediaWorker` uses the G1 claim, renewal, active quotas, lease/fence and atomic
completion. It downloads scoped input bytes, checks real sizes/hashes and source
metadata, then renders the G0 scale/pad/audio/SRT recipe in a new bounded native
runtime. G0 SQLite execution is not invoked for G2 state.

Each attempt publishes immutable keys:

```text
<org>/assets/<asset>/source.mp4
<org>/assets/<asset>/caption.srt
<org>/<job>/<fence>/vertical.mp4
<org>/<job>/<fence>/square.mp4
<org>/<job>/<fence>/feed.mp4
<org>/<job>/<fence>/manifest.json
```

The smoke profiles are 270×480, 320×320 and 320×400; the inherited production
profile is implemented but **not a production acceptance benchmark**. Before
the database accepts a final manifest, the worker reads the published objects
back, verifies actual SHA-256/byte count, parses the stored manifest and probes
actual duration, video dimensions, codecs and audio. A completed replay repeats
verification and returns its authoritative generation; missing/corrupt completed
outputs require reconciliation and never cause an automatic rerender.

## Private storage and sandbox

`ObjectStore` defines immutable publication, bounded retrieval, checksum metadata,
inventory and conditional deletion. `LocalObjectStore` implements durable file
and directory fsync, atomic create-if-absent, 0600 objects/0700 directories and
org-prefixed keys. Directory-descriptor walks with no-follow flags reject path
traversal, symlink ancestors, replaced roots and cross-org references. A future
R2/S3 implementation must preserve these semantics using conditional writes and
generation-scoped objects; no cloud SDK or adapter is included.

Native commands are fixed argv, shell disabled, local MP4 demuxer/protocols only,
one encoder/filter thread and bounded tracks/codec/frame count. Limits cover
bytes, source/output duration and pixels, total output bytes, wall timeout,
CPU/address-space/file-size/open-file rlimits. A launcher avoids threaded Python
`preexec_fn`; Linux parent-death SIGKILL and a parent-PID race check terminate the
native process if its supervisor is hard-killed. Normal timeout, cancellation or
lost heartbeat kills the process group and waits for it. Scratch is private and
removed on ordinary completion/errors. Hard-kill leftovers are inventoried.

This boundary is a local process/rlimit sandbox for synthetic media. It does not
provide production seccomp, separate tenant OS identities, network namespaces,
codec fuzz assurance or an audited container. Processes sharing the same OS UID
are trusted. Local disk capacity, tenant throughput fairness and cloud scale are
not established by the two-tenant concurrency test.

## Failure, recovery and observability

Output-before-DB ordering deliberately allows private orphan objects. It does
not promise exactly-once physical rendering. A killed/expired worker cannot
commit after another fence; a replacement publishes into a new fence namespace.
The job row lock protects the orphan authority check: current authoritative/live
generations are never deleted. `reconcile_orphans(org,job,delete=False)` reports
obsolete objects, private upload staging fragments and scratch; explicit
`delete=True` removes only obsolete generations and records audit evidence.
Old workers might still produce more obsolete files; periodic reconciliation
is required until such workers are gone.

Input upload precedes the caller-owned ingestion transaction. If that transaction
rolls back, `inventory_inputs` reports unreferenced private inputs. Reporting is
intentional: automatic deletion could race a still-uncommitted valid ingestion.
An operator must stop/drain ingestion before deleting those unreferenced keys.

Transient download/write/FFmpeg/timeout/commit failures use G1's bounded attempts
and backoff; invalid media, checksum, caps and cancellation are permanent rejects.
A lost completion response first consults authoritative PostgreSQL and verifies
objects. Metrics and audit record fence/attempt, elapsed milliseconds, bytes read
and written and classified error. Manifest stats describe the input read/output
video publication checkpoint; final database metrics include manifest and readback
I/O. `estimated_output_pixel_frames` is only a declared work estimate, and the
per-process CPU ceiling is configuration. Neither is measured billing or pricing;
the G1 cents reservation remains fictitious and cumulative, including failures.

## Rollback and remaining gates

No merge/deploy or production migration is authorized. Closing the DRAFT PR
rolls back the proposal; destroying the disposable database and private demo
directory removes local test resources. Keep authoritative objects until reviewed
retention/deletion policy exists. There is no destructive production downgrade.

Remaining work includes real identity provisioning, remote storage/queue contracts,
hostile-media isolation/fuzzing, legal/codec distribution review, storage quotas,
resource and transfer-cost benchmarks, backup/restore, lifecycle retention and
operational ownership. See the decision/evidence record for what was actually run.
