# G3B-P0 security gates — documentary preflight

Reviewed 2026-11-07 UTC for Issue #10. This document authorizes no provider
changes, credentials, resources, Terraform plan/apply, deployment or G3B-P1 run.
The security reviewer, release approver, cloud operator and data/legal owner are
**UNASSIGNED**; naming a required role below does not appoint a person.

The audited main is `7a271382487ac4d16e278e5ccb8c345279b3cc16`. Shared-upload
behavior is a proposal in **OPEN / DRAFT PR #9**, exact head
`e40357d08fa3c0b258b63c0d3a811cbab2b23206`; it is **not integrated into main**.
Its 276-test local/CI evidence is a prerequisite artifact, not proof of hosted
Auth, remote IAM, networking, GCS or cloud isolation. Statuses below mean:
`PASS` = the named static or historical local evidence only; `BLOCKED` = a
missing prerequisite prevents the next step; `NOT_VALIDATED` = no real service
proof; `UNKNOWN` = a value or accountable decision has not been established.

## Prerequisite and trust-boundary matrix

| ID | Gate and existing evidence | Current state | Required evidence before the applicable P1 step |
| --- | --- | --- | --- |
| S01 | Immutable main/PR #9 provenance; PR #9 SQL/auth/deadline tests and final-head CI | `PASS` for the referenced historical evidence; `BLOCKED` for integration | Independent review of the exact PR #9 head, controlled human merge, then all five workflows successful on the **new main**. If either SHA changes, repeat the review/integrity comparison. No P1 authorization can imply this merge occurred. |
| S02 | G3A verifier requires exact trust roots/issuer/audience, valid signature/timestamps and UUID subject/session; current session and membership checks are local | `PASS` locally; hosted `NOT_VALIDATED` | On the separately approved nonproduction provider, verify public signing keys, algorithm/key agreement, exact issuer/audience, expiry/issued-at, key rotation and unavailable/unknown-key failure. Reject malformed, anonymous and service-role end-user tokens. Never accept a client key URL or metadata as authorization. |
| S03 | Real local GoTrue logout/deletion and API denial; narrow session-reader columns/RLS | `PASS` locally; hosted privileges/session semantics `NOT_VALIDATED` | Prove actual logout leaves a still-valid JWT denied by the API, expired sessions/deleted users are denied, and current membership/role removal denies later requests. Verify the selected hosted schema permits the reviewed narrow session lookup without service-role credentials or exposing passwords/refresh tokens. Session policies are not assumed to delete expired rows immediately. |
| S04 | Private shared schema, explicit function ACLs, RLS, immutable identities/composite receipt FKs; raw user DML denied in PR #9 tests | `PASS` for local SQL; hosted advisors/Data API `NOT_VALIDATED` | Verify actual schema owners, login attributes, inherited roles, `PUBLIC`/`anon` execution revocation, definer search paths and API exposure configuration. Keep `g3a_private`/`g3a1_private` outside exposed schemas. Run approved hosted security advisors and direct denial tests; a successful advisor alone is insufficient. |
| S05 | Short membership → quota → reservation locks, effective lease heartbeat and deadline checks after locks/writes; stale fences cannot replace receipts | `PASS` locally; cloud timing `NOT_VALIDATED` | Reproduce revocation during I/O, lock contention, lease loss, clock skew and process termination using the selected network/runtime. Database time remains authoritative. SQL admission bounds do not promise a hard real-time COMMIT/fsync acknowledgement or cancel arbitrary blocked I/O. |
| S06 | Separate service verifier rejects end-user tokens and trusts only configured service identity; Tasks/Run are local contracts | `NOT_VALIDATED` | Prove real Google-issued OIDC issuer, exact audience, numeric subject, service-account email and key rotation. Verify task caller's invoker grant, enqueue `actAs` scope and service-agent permissions. Queue headers never establish identity. Wrong service identity and end-user JWT must fail. |
| S07 | Old IaC proposes internal-only Cloud Run and IAM-enabled ingress | `BLOCKED` pending actual route/adapter decision | Define how the approved harness reaches the internal API and how Google IAM and the application's end-user JWT coexist. Validate both identities; do not replace the user JWT with Google OIDC or expose an unauthenticated alternate path. Internal ingress is not proof of restricted egress or private database connectivity. |
| S08 | API role validation rejects owners, BYPASSRLS and excess roles; internal G1 role is trusted and wider | `BLOCKED` pending reviewed least-privilege mapping | Review separate API/session, dispatcher, launch-controller, worker, upload-reconciler and operator credentials. Separate service-account names/secret IDs alone do not narrow SQL grants. Prove the API cannot launch, reconcile as service, ingest with an owner DSN or read secrets outside its scope. |
| S09 | Local connection semaphores and short transactions; no hosted connection profile | TLS, pooling and aggregate capacity `NOT_VALIDATED`; provider limits `UNKNOWN` | Confirm database/pooler endpoint, CA/hostname verification, IP family/routing, firewall and network timeouts. Prove `SET LOCAL ROLE`, transaction-local claims, definer functions and lease operations survive the chosen pool mode without identity bleed. Bound **combined** client/server connections, not only the upload pool. |
| S10 | GCS adapter uses immutable keys, generation preconditions and actual byte verification against a loopback contract server | Real GCS/IAM `NOT_VALIDATED` | Observe actual `ifGenerationMatch=0` conflict behavior, generation-bound read/deletion and SHA/size readback. Validate no public ACL/access and minimum bucket permissions. A hash in metadata, successful PUT or receipt alone cannot prove object integrity. R2/S3 requires separate adapter/consistency evidence. |
| S11 | Claimed/uncertain uploads retain quota; only expired fence-zero unused grants refund once; PR #9 recovery/rollback contract | `PASS` locally; remote retention/restore `NOT_VALIDATED` | Retain private uncertain objects, reservations, signing material and receipts through failures. No missing-object-based refund, destructive GC or switch of an active organization to an empty SQLite ledger. Prove backed-up ledger and matching object generations can be reconciled before any cleanup approval. |
| S12 | Prepared launch intent precedes non-idempotent `jobs.run`; uncertainty consumes admission; provider response is a local stub | Real launch, cancellation and terminal evidence `NOT_VALIDATED` | Only the controlled launcher may run jobs. Observe one intended task/execution, capped retries and fence-bound claim. A lost launch response, operation 404 or `operations.done` alone must not free the slot. Require authenticated terminal execution/task evidence; logical DB completion is not physical shutdown. |
| S13 | Existing hash-locked dependencies and reviewed local image digests | `PASS` for those historical pins; deployable image/signature/SBOM `BLOCKED` | Produce reviewed immutable image digests, build provenance, SBOM/transitive vulnerability inventory, signer identity and signed attestations. Demonstrate the chosen enforcement rejects unapproved/unsigned images without breakglass. No image was built, signed or approved by this P0. |
| S14 | Historical FFmpeg binary enables GPL/libx264/libass; parser resource controls were locally exercised | Legal distribution/codec review and hostile-media runtime `NOT_VALIDATED` | Review exact binary build, linked libraries, LICENSE/NOTICE/source obligations and relevant codec/patent jurisdictions with the data/legal owner. Review sandbox, native-process kill behavior and temporary-file bounds. Synthetic-only staging does not clear commercial distribution or authorize customer media. |
| S15 | No selected provider account, project, region, residency decision, approver or spend authority | `UNKNOWN` / P1 `BLOCKED` | Identify isolated nonproduction tenancy, approved geography, IAM admin/operator, emergency actor, retention/backup scope and conditional cost/stop policy. Public rate research and USD 10 proposal do not grant spending authority. |
| S16 | Current Tasks retry semantics and manual `tasks.run` behavior are publicly documented; trial-wide callback admission/stop enforcement is not implemented | `BLOCKED` before any Tasks-enabled P1 | Independently review retry exhaustion semantics, implement/test the proposed application/operator callback budget, deny `cloudtasks.tasks.run` to routine runtime principals and verify the stop path. Queue retry parameters, rate limits and pause are not by themselves a hard total callback/cost cap. |

Supabase's current session guidance distinguishes JWT signature validity from
session existence; strict logout checking must consult the corresponding live
session. Neither user metadata nor a refreshed-looking token proves current
organization membership. These requirements preserve the project's stricter
existing boundary. [Official sessions](https://supabase.com/docs/guides/auth/sessions),
[RLS](https://supabase.com/docs/guides/database/postgres/row-level-security).

## Identity and privilege allocation to review

This is a proposed separation, not a deployed IAM policy or SQL migration.
The existing internal `engine_worker` role has trusted cross-organization
policies and render/outbox/audit writes; G3A also grants it launch-ledger writes.
It is not an end-user tenant role. A future split must be implemented/tested in
an authorized code change; this documentary PR must not silently claim that
split already exists.

| Principal | Minimum intended authority | Explicit denial / unresolved gap |
| --- | --- | --- |
| End user | Own verified session; current authorized membership; owner-scoped grant/receipt operations | No DSN, `service_role`, Google credential, metadata role, arbitrary organization prefix, raw ledger DML or worker/ingest authority. |
| HTTP API + session reader | Restricted NOINHERIT nonowner login, `authenticated` transaction context and only narrow `g3a_session_reader`; private create/read objects required by the API | No `engine_worker`, upload service-reconciler role, quota provisioning, `jobs.run`, password/refresh-token reads or raw upload-table writes. Existing bucket access spans service-owned organization prefixes; object IAM does not itself implement tenant membership. |
| Dispatcher / task caller | Dispatcher reads/leases due outbox; enqueues only the approved queue and may act as only the approved task caller; task caller invokes only the controller | No render execution, bucket delete, API-user impersonation, arbitrary service-account impersonation or `cloudtasks.tasks.run`. Separate queue/service-agent IAM and denial of debug dispatch need real tests. |
| Launch controller / bounded sweep | Reserve launch intent and observe exact operations/executions; exclusive permission to launch the fixed approved Job with reviewed immutable overrides | No arbitrary image/command/source URL; no bypass through a second scheduler, console launcher or generic retry path. `jobs.run` overrides make controller integrity particularly important. |
| Render worker | Consume its bound launch/job/org/fence, short lease transactions, fetch verified inputs, create/read immutable outputs and metrics | No enqueue/launch permission, Auth administration or credentials for other services. Broad trusted historical SQL and prepared storage list/delete need explicit review before use; do not infer per-job cloud isolation from a key prefix. |
| Upload reconciler | Dedicated service login granted only the NOLOGIN `g3a1_upload_reconciler` SQL role and approved private-object verification operations | Not an HTTP login; no raw table DML or automatic quota release/object deletion. Recovering a revoked/deleted historical owner does not grant a new end-user session. |
| Migration / emergency operator | Human-reviewed, time-bounded changes and audited inventory/drain/kill actions | Never use this credential in the API/worker or client. Emergency stop preserves ledger/receipts/uncertainty; breakglass and destruction require separate explicit review. |

The old `worker_media` custom IAM role includes object list/delete, while the
API custom role includes create/get. This static declaration is neither proof
of effective IAM nor authorization for destructive upload GC. Restrict cleanup
to reviewed obsolete generations; valid receipts, live writers and uncertain
uploads remain protected. The future operator must capture effective grants,
not only intended Terraform text.

## Network, secrets and connection gates

Cloud Run ingress and IAM are independent checks. A Supabase user token does
not satisfy Cloud Run IAM. One documented transport option sends Google ID
credentials via `X-Serverless-Authorization` while the application verifies the
user JWT in `Authorization`; the selected harness/edge must prove that path and
reject either missing/wrong identity. The current loopback handler is not a
validated cloud adapter. Same-project Cloud Tasks can be an internal ingress
source under Google's documented conditions; another Cloud Run caller can need
VPC routing. A caller on the operator's laptop is not automatically internal.
[Service authentication](https://docs.cloud.google.com/run/docs/authenticating/service-to-service),
[ingress](https://docs.cloud.google.com/run/docs/securing/ingress),
[private networking](https://docs.cloud.google.com/run/docs/securing/private-networking).

An internal API can still have internet egress. Select and verify routes to
Auth/JWKS, the PostgreSQL endpoint, GCS, Tasks/Run control APIs and telemetry.
Choose allowed destinations, DNS/TLS validation, timeout profile and any VPC/NAT
cost explicitly. Do not assume a Supabase public endpoint is privately routed
or that a Cloud Run network label enforces destination allowlisting.

Require certificate-chain **and hostname** verification for database transport
(`sslmode=verify-full` with the approved CA, or equivalent), rather than merely
requesting encryption. Pooling mode, client cap, prepared-statement behavior,
transaction scoping and IPv4/IPv6 reachability must be compatible with the
actual driver. Connect/session/statement/lock/idle timeouts and failed-heartbeat
termination must be measured under packet loss and unavailable DB conditions.
[Supabase connections](https://supabase.com/docs/guides/database/connecting-to-postgres),
[SSL enforcement](https://supabase.com/docs/guides/platform/ssl-enforcement).

Use the architecture's serial proposal and aggregate allocation as a starting
point: API 1 request, controller 1 request and 1 reserved physical execution;
the component connection allocation is **13 plus hosted Auth/platform usage
UNKNOWN**, subject to approval and real measurement. Reserve headroom for
migrations/emergency observation. Include API render handling, session lookup,
upload SQL and heartbeat, worker control/heartbeat, dispatcher, sweep and
provider-owned Auth connections. The PR #9 four-connection upload semaphore
is not a complete deployment database budget. Its two-CPU CI cohort needed
205 HTTP 503 responses/retries for 100 logical duplicates; integrity passed,
sustained capacity did not become validated. Never solve saturation by
unbounded retries, new replicas or duplicated database pools.

The proposed serial trial additionally caps **four physical executions total
across three logical jobs**, one task/parallelism one, 1 vCPU/2 GiB, application
deadline 120 seconds, provider timeout 300 seconds and zero platform retries,
inside a window of at most two hours. Every number is **UNAPPROVED**. A timeout
or manual retry does not replenish that trial-wide execution budget; a stopped
execution still counts. The runbook's operator must be able to stop admission,
then terminate/reconcile the exact in-flight execution without losing the ledger.

If Cloud Tasks is selected, its existing `maxAttempts=5` and
`maxRetryDuration=300s` proposal must **not** be presented as a five-callback
hard bound: the current REST reference requires both exhaustion conditions,
unless the task succeeds. The proposed **20 callback HTTP attempts total** is
an unapproved future application/operator budget, not a limit derived from
those provider fields and not implemented by P0. Count duplicate deliveries,
retries and rejected attempts; stop admissions and the trial through the
reviewed queue/runtime policy when that budget is reached, allowing for
in-flight attempts and delayed telemetry/cost. Prove that policy before a
Tasks-enabled trial, or leave Tasks disabled. [Current RetryConfig semantics](https://docs.cloud.google.com/tasks/docs/reference/rest/v2/RetryConfig).

The manual `tasks.run` API can dispatch despite a running task, rate limits
or a paused queue. Deny its `cloudtasks.tasks.run` permission to normal
application/dispatcher/operator paths; any emergency exception needs separate
explicit authorization and must consume the trial's callback/execution budgets.
Queue pause alone is not a security boundary against a principal with that
permission. Review inherited/effective grants and include a denial test, rather
than assume a role label enforces this. [Official manual-dispatch method](https://docs.cloud.google.com/tasks/docs/reference/rest/v2/projects.locations.queues.tasks/run).

Prefer attached short-lived workload identities; use secret-level access for
each necessary DSN/signing key and explicit reviewed secret versions. Keep
credentials out of source, Terraform variables/state payloads, image layers,
client responses and logs. Define rotation/revocation and recovery of upload
signing material before admitting grants; replicas must share the appropriate
active material and preserve recovery semantics for old grants. Unreviewed
rotation can strand existing capabilities. No live secret was accessed here.
[Secret Manager guidance](https://docs.cloud.google.com/secret-manager/docs/best-practices).

## Data, artifacts and denial evidence

Only locally generated synthetic media and nonproduction test identities may
enter the proposed trial. Record organization isolation, selected geography,
cross-provider routes, logs/backups/support access and deletion/restore policy.
Residency and retention approval are `UNKNOWN`; a region name alone is not a
legal assessment. Object versioning, soft deletion, backup copies and stranded
resumable sessions can outlive the two-hour trial and continue to incur charges.
Treat resumable-session URIs and capability URLs as bearer secrets; redact
authorization headers, JWTs, DSNs, passwords, object contents and signed URLs.
Correlation UUIDs can also be identifying when linked to users. Limit log
retention/access and capture only the minimum evidence needed for reconciliation.

The existing create-only GCS contract uses `ifGenerationMatch=0`; reads and
approved deletes bind the observed generation. Any mismatch, partial inventory
or lost success response remains uncertainty until actual bytes/hash/size and
generation are reconciled. Provider metadata alone is insufficient. A stale
worker may leave private old-fence objects but cannot replace the authoritative
manifest. [GCS preconditions](https://docs.cloud.google.com/storage/docs/request-preconditions).

Image digest pinning does not establish origin, signature, absence of known
vulnerabilities or license clearance. Require provenance and SBOM associated
with the exact digest, an approved signer and enforced verification policy;
test rejection of an unapproved artifact in the future isolated trial. Google's
Binary Authorization is a possible control to evaluate, not a configured
feature of this repository. [Official control](https://docs.cloud.google.com/run/docs/securing/binary-authorization).
Verify support and enforcement for the exact selected Job/service resource;
a service example does not establish identical Job behavior.
Review the actual FFmpeg build and codec obligations independently; no
permissive application license clears every linked library or codec patent.
[FFmpeg legal guidance](https://ffmpeg.org/legal.html).

| Future negative/failure test | Expected denial or safe retained state | Immediate stop condition |
| --- | --- | --- |
| Missing/wrong user JWT; metadata/header-selected organization; another tenant/owner's grant | Authentication/authorization denied; no new object/charge/receipt | Any auth or tenant-isolation bypass. |
| Logout, membership revocation, role downgrade or user deletion during/after admission | Later operations denied; in-flight boundary respected; private uncertain effect stays charged/recoverable | Unauthorized completion or receipt disclosure. |
| Google caller missing/wrong audience/subject/email, queue headers forged, user token sent to internal callback | Platform/application service checks deny; no launch intent or execution | Identity substitution or alternate unprotected invocation route. |
| Callback retry exhaustion or manual debug dispatch despite queue pause/rate limits | Independently enforced trial callback budget; routine `cloudtasks.tasks.run` denied; no renewed execution budget | Retry parameters treated as a hard five-attempt cap, unexplained callback growth or debug-dispatch permission bypass. |
| Concurrent duplicate wake signals and repeated operator invocation | Existing intent/fence reused; no second physical launch | Duplicate physical launch or multiple Jobs without the authoritative fence. |
| Lost `jobs.run` response; expired provider history; blocked operation lookup | Slot retained, admission stopped, investigation recorded | Blind launch retry or slot release without terminal evidence. |
| Storage success with lost response; changed generation; corrupt/missing bytes; stale writer | No invalid receipt/manifest; exact generation reconciled; uncertain quota held | Object/receipt divergence, unauthorized overwrite/delete or unproved quota refund. |
| Database lock/blackhole, exhausted pools, HTTP 503/provider 429, failed heartbeat, process crash | Bounded backoff; existing identity/key retained; renewal stops; fenced recovery only | Unbounded retries/connections, stale commit, uncontrolled executions or inability to stop work. |
| Unsigned/unapproved image or unexpected executable/source URL | Release/execution denied by reviewed artifact/control policy | Unapproved artifact executes or bypass is silently accepted. |
| Unexpected region, public object/access path, credential in evidence or customer media | Admissions paused; preserve sanitized incident evidence; no further data transfer | Any observed exposure, secret leakage or unauthorized data. |
| Billing unavailable/unreconciled, resource inventory mismatch or stop threshold reached | Stop new admission/launch immediately and invoke the approved drain/kill policy | Unknown billing, missed kill ownership, or continued spend without a bounded approved estimate. |

These are **planned** denial tests, not P0 cloud probes or passed cloud tests.
The future runbook must retain actual provider responses, resource IDs,
timestamps, role/route inventories, operation/execution terminal evidence and
object generations without bearer secrets. If a result cannot be established,
record `NOT_VALIDATED`/`UNKNOWN`, stop the affected path and do not infer safety
from a timeout or a missing resource lookup.

## Static audit evidence and current official references

Repository evidence is pinned to PR #9 head above. Relevant immutable files:

| Source file at `e40357d…` | SHA-256 of complete file |
| --- | --- |
| `engine/cloud_auth.py` | `aac80d5dfa5762d0e61d50c0e3748634385e702a042c2dc080b5e2cafe35a1cc` |
| `engine/shared_upload_authority.py` | `670c43a6e6f7dae35e012c8fe4dc67d2c883f82e6db6e9fe878523beaafbcc3f` |
| `engine/shared_upload_http.py` | `77708c3d7cf3cd9612e45f51b1820c3fecb9dbb5c1860ec081c28a9ba9515ae3` |
| `supabase/migrations/20261107150813_shared_upload_authority.sql` | `9d8521e9eed6a72c18942d6628b08dc2f53dd2ddc5d4bb3befbfb26705f08364` |
| `sql/002_g1_control_plane.sql` | `ee5bcff9183f0b5ef883bd7bbe4ab47033729ae80c177671122b60b01c541753` |
| `supabase/migrations/20261107123845_g3a_cloud_contracts.sql` | `800570363421d4a2984d9f3ef4c4111d7af12d1b434e07f858ef8004c0a17f77` |
| `infra/g3a/main.tf.json` | `ccf50b7588c1bfdf85cbd623cdc36b8cbba0713c5f8ccdad05a9c2cf88869734` |
| `docs/G3A1_DECISION.md` | `863d955f1c8d8670c03a379ecc30aa5e916169611ff3b418290accc375e3f395` |

Reproduce file hashes without executing application code or touching providers:

```sh
git show e40357d08fa3c0b258b63c0d3a811cbab2b23206:engine/cloud_auth.py | sha256sum
git show e40357d08fa3c0b258b63c0d3a811cbab2b23206:supabase/migrations/20261107150813_shared_upload_authority.sql | sha256sum
git show e40357d08fa3c0b258b63c0d3a811cbab2b23206:infra/g3a/main.tf.json | sha256sum
```

The official public documentation linked above was consulted on **2026-11-07**;
no cloud-provider console, project, authenticated Google/Supabase API, secret
or deployed endpoint was inspected. Supabase's [changelog](https://supabase.com/changelog.md) was
checked before the review: CLI v3 local-default/hosted-confirmation changes and
the announced November 10 TLS CBC change require rechecking the actual selected
client/hosted version before P1. PostgreSQL upgrade notices apply according to
the chosen hosted version, which remains `UNKNOWN`; they do not establish a
change to the locally tested PostgreSQL 16.15.

Public fetch observations (not hosted operational evidence): changelog markdown
103,941 bytes, SHA-256
`d3b1566a6a60d54095de09aeb61f50128dd0da17357fd62b646ccf560007162d`;
database connection guide markdown 17,685 bytes, SHA-256
`75a4b430d0c4001def8636653d117cec1b5bb5e75bccb31166ca9ef720d57387`;
FFmpeg legal HTML 10,746 bytes, SHA-256
`0c0f84b347baa5edb55ad594cb9b8dc3a6ae47b652131ff4533a16b3773e89df`.
These are dated content fingerprints, not promises that dynamic pages retain
the same bytes. The first browser fetch of FFmpeg legal timed out; the subsequent
unauthenticated official-page fetch succeeded. Public reference availability
does not close any remote-service gate in the matrix.

See [preflight](G3B_P0_PREFLIGHT.md), [architecture](G3B_P0_ARCHITECTURE.md),
[costs](G3B_P0_COST_MODEL.md), [future staging runbook](G3B_P0_STAGING_RUNBOOK.md)
and [decision](G3B_P0_DECISION.md). No cloud safety or production approval is
claimed by this document.
