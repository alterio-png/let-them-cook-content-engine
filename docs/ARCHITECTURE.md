# Target architecture, deliberately separated from the G0 local spike

## Logical boundary

```
Browser (future authenticated Next.js)
    |  create organization-scoped, consented upload
    v
Control API (authorization, quotas, request fingerprint, cost reservation)
    |  PostgreSQL transaction: asset + job + outbox event
    v
Outbox dispatcher (durable, retryable; never loses committed jobs)
    |  Cloud Tasks HTTP dispatch
    v
Cloud Run service (verifies task identity, reconciles + invokes Jobs API)
    |  execute a Cloud Run Job with stable job ID/lease epoch
    v
Isolated worker (FFmpeg, optional ML provider)
    |  multipart artifacts / object store (GCS vs R2 not chosen)
    v
Manifest + checksums + atomic DB commit with fenced lease
    |  user approval -> official network-specific publishing adapter
    v
Approved publication and analytics states
```

Cloud Tasks cannot call a Cloud Run **Job** as though it were an HTTP service; the control-plane service must trigger Jobs via the API (or use a different orchestrator). Cloud Tasks may deliver tasks more than once and Cloud Run Jobs may retry. Exactly-once processing is an application-level objective, not a cloud-provider guarantee.

## Operation lifecycle
1. API validates identity, org membership, content rights flag, budget, input asset and quotas. Record a stable client `operationId` separate from browser session language.
2. One database transaction inserts `render_jobs`, `render_outbox` and event history; `UNIQUE(organization_id,request_key)` rejects conflicting reuse.
3. Outbox dispatcher sends Cloud Task with stable event ID. Duplicate dispatch is safe.
4. Orchestrator verifies signed service identity, atomically claims or reconciles job; increments lease epoch/fencing token. Old attempts cannot overwrite new outcomes.
5. Worker downloads scoped object with minimum necessary credentials, validates bytes/MIME with ffprobe and caps duration, pixels, codec, frame count, resource use and timeout.
6. Worker writes temporary named artifacts and a manifest with SHA-256; validates ffprobe dimensions, sound and duration. Then stores immutable final objects or promotes a verified generation.
7. DB transaction commits success ONLY if attempt still owns current lease token. If a response is lost, reconcile authoritative DB+object state before creating any second effect.
8. User sees review UI; publication requires separate explicit approval and own idempotent record. Never auto-publish from render success.

## Control-plane contracts to build before pilot
- `POST /v1/assets/uploads` creates a short-lived multipart upload for org-owned storage, server-recorded size/rights and per-tenant quota.
- `POST /v1/renders` requires `operationId` and `assetId`; returns `jobId`, current `state` and `requestFingerprint`.
- `GET /v1/renders/{jobId}` enforces organization scope, returns artifacts only when completed and verified.
- `POST /v1/renders/{jobId}/reconcile` restricted internal admin operation, complete audit.
- `POST /v1/publications` requires user approval, platform-scoped OAuth and network API idempotency/reconciliation.

## Storage and tenancy
Storage keys include `org_uuid / asset_uuid / job_uuid / generation / rendition`. Do not use user-supplied filenames as authoritative keys. Short-lived signed URLs; no public buckets for draft/customer files. Retention TTL and deletion policy must be defined for EU and US customers.

## Runtime scaling
- CPU render and transcription as separately metered job stages; AI provider behind adapter.
- Per-org concurrency caps, weighted fair queuing, backpressure, cancellation, queue age alarm and DLQ.
- Initial target: 10 orgs, 50 requested jobs, bounded concurrency; verify no duplicate billing and no cross-tenant access.
- GPU only after real transcription/reframing throughput and cost benchmarks.
- Cloud Run Jobs retry/timeout/parallelism must be explicitly bounded and tested. Separate Google Cloud project and billing alerts for staging.

## Deployment gates
No credentials or infrastructure provisioned. Dockerfile in `infra/` is an unbuilt proposal; it is NOT a pinned, hardened, license-cleared release image.
