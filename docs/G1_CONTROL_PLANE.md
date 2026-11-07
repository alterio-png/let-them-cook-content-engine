# G1 control plane — Issue #2

This is the isolated PostgreSQL proof requested by Issue #2. The older
`SCALE_GATES.md` labels this foundation G2 and licensing G1; those G0 documents
remain historical and byte-for-byte intact. This change does not authorize any
production, cloud, AI, publication, customer media or codec distribution gate.

## Run from a clean checkout

Requirements: Docker, Python **3.12 on Linux x86_64**, FFmpeg/ffprobe with libx264,
AAC and libass. Other Python/platform wheels deliberately fail the locked install.
No production credentials or services are needed.

```sh
python3.12 -m venv /tmp/ltc-g1-venv
/tmp/ltc-g1-venv/bin/pip install --require-hashes --only-binary=:all: -r requirements-g1.lock
G1_PYTHON=/tmp/ltc-g1-venv/bin/python sh scripts/run_g1_local.sh
```

The script starts a pinned, disposable PostgreSQL 16 image on loopback port 55432
(`G1_PORT` overrides it), uses tmpfs for data, allows 160 connections, and removes
the container on exit. Its password is a public local fixture, never a secret.
It applies the local auth fixture, unchanged draft `001`, and additive `002`,
checks **all** G0 hashes, and runs both independent suites. G1 lives under
`integration/` so the unchanged G0 CI still runs without a PostgreSQL driver.
Missing PostgreSQL/DSN in the integration suite is an error, never a skipped PASS.
CI runs the same script and retains the test log for its exact commit SHA.

For an already running **fresh disposable** local database, set `G1_TEST_DSN`
to a loopback DSN whose database name starts with `g1_`, then run:

```sh
python scripts/prepare_g1_database.py
python -m unittest discover -s tests -v
python -m unittest discover -s integration -v
```

The initializer refuses an existing application schema. Do not run the local
`integration/g1/bootstrap.sql` fixture against Supabase. It models `auth.uid()`
from a trusted verified identity context; it does not validate JWTs, implement
Supabase Auth, or prove a hosted Supabase integration.

## Authorization and transactional submission

Apply `001` then `002` as an administrator. No login principals/passwords are
created by `002`. A future authenticator must install a **verified** identity in
an `authenticated` transaction and call
`public.submit_render(org, request_key, asset, duration_ms, quality)`; never expose
SQL access, claim setters, a privileged DSN, or the internal worker role to users.
`ControlPlane.submit` leaves transaction ownership with that trusted caller.

The SQL function checks current membership (owner/admin/editor) and locks that
membership through commit. Tenant reads use RLS. Authenticated users have no
direct mutation privileges; anonymous callers cannot invoke submission. Attempts
and outbox are internal. Every job/asset reference and each child job reference
includes organization ID in a composite FK, independent of RLS.

The function locks the organization's quota row, computes its digest from a
server-built versioned JSONB payload including immutable source metadata/hash,
requested duration and quality, and returns an existing equal operation before
reserving again. A changed payload under the same key is rejected. New work
atomically creates job, outbox, audit and cumulative reservation. Media rights,
bytes, source duration, source/output pixels, job count and budget are checked.
Trusted ingestion supplies asset metadata; no arbitrary file probe is introduced.

The fake cost model is `ceil(duration_ms/1000) * 3` cents per operation. It proves
reservation correctness, **not** unit economics. Reservations remain cumulative
on success/failure; release/refund, pricing and billing are future policies.
Request identity, digest, payload and reserved cost cannot be edited; completed
manifest/state/generation cannot be replaced. Legacy assets with unknown metadata
or rights are rejected until verified by trusted ingestion.

## Worker and outbox contracts

`ControlPlane(dsn)` and `Dispatcher(dsn)` are internal libraries. Use a dedicated
trusted login allowed to assume `engine_worker` in a real integration. The tests
use administrator setup and explicitly `SET LOCAL ROLE` to restricted,
non-owner, non-superuser, non-bypass roles for the application transitions.
The worker has internal cross-org access but cannot alter memberships, assets,
quotas, request identity or delete rows. It must never be a user-facing principal.

`claim_job` locks an organization advisory key then a job row. The organization
lock serializes active quota checks, and row locks serialize duplicate claims.
Claims use database time, random lease token and increasing fence; the lease is
1–300 seconds. Heartbeats use `renew_lease`. Finish/fail/renew predicates recheck
current state, token, fence and nonexpired database time at the UPDATE. Takeover
expires the former attempt; retries have exponential bounded backoff and at most
three attempts by default (schema permits 1–10). Completion, attempt and audit
commit together. A lost completion response returns the existing matching result.

Poll `claim_next_job` independently of queue delivery: its bounded candidate scan
recovers due queued jobs and crashed workers after lease expiry, including after
the original outbox was acknowledged. A queue adapter alone is insufficient.
Jobs beyond the attempt cap become failed and retain audit evidence. Operators
must investigate these records; no automatic new logical operation is created.

The dispatcher claims outbox rows with `FOR UPDATE SKIP LOCKED` and a separate
lease/fence. It reconciles `lookup(event_id)` before `ensure_task(event_id,job_id)`.
**Both authoritative lookup and atomic idempotent named creation are required of
the adapter.** A lost response, timeout, duplicate delivery or crash therefore
uses the same event ID. Retry/backoff is bounded to five sends; uncertainty beyond
that is an explicit `reconcile` state with audit, not silently discarded work.
`ack` and `release` are fenced. The tests' durable queue is an in-process stub,
not a claim about Cloud Tasks retention, lookup consistency or deduplication TTL.

Manifest acceptance validates size, generation namespace, filenames, checksum
syntax and positive byte counts. An external renderer/storage adapter must
verify actual bytes and publish immutable generation-scoped objects first.
Expired generations cannot replace the authoritative database manifest.
The G1 tests prove no duplicate **committed state/named queue task**; they do not
prove exactly-once physical rendering/billing or remote object effects. G0 still
exercises actual synthetic FFmpeg rendering, audio, supplied SRT and checksums.

## Limits and rollback

No API server, production identity provisioning, remote queue/storage, distributed
scheduler deployment, resource-isolated FFmpeg integration, commercial pricing,
license clearance, or SaaS readiness is delivered. A long-running production
scheduler needs fairness/monitoring and shutdown ownership; queue adapter semantics
must be separately validated. Lost results outside the database require storage
reconciliation before choosing any new operation.

All G0 tracked files and `G0_SHA256SUMS.txt` are preserved. The Creative OS is never
checked out or mutated. Rollback of this **unmerged** proof is closing the DRAFT
PR/removing its branch; local database rollback is destroying the disposable
container. No production downgrade or destructive migration is supplied or run.
