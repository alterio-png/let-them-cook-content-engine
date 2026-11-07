# G3A private Google Cloud Storage contract

**Remote GCS: NOT_VALIDATED.** No GCP resource, bucket, service account or paid
request was created. The implementation is a real JSON REST adapter with injected
server authentication; its evidence uses an actual loopback HTTP fault server
that implements only the required contract. This server is neither Google Cloud
Storage nor an official emulator. Provider IAM, billing, quotas, latency,
resumable-session behavior and Cloud Run networking need authorized G3B evidence.

## Adapter and authority

`engine/gcs_store.py:GCSObjectStore` implements the unchanged G2 `ObjectStore`
protocol. The bucket and endpoint are server configuration. Production accepts
only `https://storage.googleapis.com`; explicit `allow_loopback_http=True` permits
numeric loopback origins solely for isolated contract tests. The underlying
`UrllibCloudTransport` injects OAuth bearer tokens from a server-owned callable,
disables environment proxies and redirects, validates destination hosts, bounds
timeouts and response bytes, and sanitizes transport exceptions. Token refresh
is the caller's responsibility; no credentials are accepted from API users.
No Google SDK or new package is required.

Organization UUIDs and each key component use the original G2 validation rules.
Validated inputs retain `<org>/assets/<asset>/...`; API uploads remain
`<org>/uploads/<grant>/unvalidated.bin` pending trusted ingestion; outputs retain
`<org>/<job>/<fence>/{vertical,square,feed}.mp4` and `manifest.json`. A tenant cannot
supply another tenant's prefix, unscoped path, encoded slash, traversal or unsafe
component. The organization passed to this adapter is already authenticated and
authorized by the API/worker. This class does not authenticate users, grant a DB
lease, decide which fence is authoritative or authorize an orphan for deletion.
The unchanged PostgreSQL transaction and row lock remain that authority.

Private bucket configuration, uniform bucket-level access, public-access
prevention and minimum IAM are prerequisites in the declarative IaC contract.
The adapter never requests public ACLs, public URLs or public object metadata.
It cannot prove a remote bucket's IAM without G3B. Do not infer private remote
storage from a local test.

## Immutable upload, checksums and uncertainty

1. Open the source with no-follow descriptors through every ancestor; accept a
   bounded regular file. Hash its actual bytes before publication.
2. Reconcile the same named key first. Replay succeeds only when a complete
   generation's actual bytes match SHA-256 and size; conflicting bytes are an
   immutable conflict, and missing/corrupt metadata cannot be accepted.
3. Initiate a private resumable upload with `ifGenerationMatch=0`, immutable key,
   exact size and SHA-256 metadata. No overwrite/update request exists.
4. Send bounded 256 KiB-multiple chunks. Trust each `308` acknowledgement's Range,
   rather than assuming that the provider accepted every sent byte. After an
   interrupted response, query status with `Content-Range: bytes */<size>` before
   sending remaining bytes. Retries, stalls, response bytes and total upload
   duration are bounded.
5. After completion, precondition every media read against its observed generation
   and compute SHA-256 from downloaded bytes. Metadata hash alone is never proof.
   A lost success or expired session can succeed only after named-key byte
   verification. An unprovable state raises `GCSReconciliationRequired`; callers
   must not commit the PostgreSQL job or automatically replace the object.

The resumable Location must use the exact configured origin and bucket upload
path with an upload ID. Redirects, arbitrary URLs, metadata endpoints, other
buckets/ports and attacker hosts fail before credential forwarding. Session URIs
are bearer secrets: they are held only in the server process, never returned as
object metadata, never logged, and never offered as a client upload grant.

`upload_deadline` defaults to 300 seconds (maximum 600); it bounds server work,
not the provider's session retention. A process crash can leave an incomplete
provider session until provider expiry. The adapter does not claim to shorten
GCS session lifetime or enumerate/cancel sessions after a crash. No incomplete
session is treated as a published object. A new authorized retry remains
create-only and first reconciles the immutable named key.

## Temporary uploads/downloads and lifecycle

The HTTP boundary's organization-scoped upload grants own the short, server-
controlled TTL and expected size/SHA-256. Bytes go through the authenticated API
and server-owned ObjectStore; a GCS resumable URI is never a client capability.
Downloads similarly pass through authenticated HTTP with a short-lived grant
where supported. **Direct GCS signed uploads/downloads: NOT_VALIDATED and not
implemented.** Do not manufacture a signed URL with a bespoke HMAC and describe
it as a provider signature. A future adapter requires official signing semantics,
separate signer IAM and G3B expiry/cross-tenant evidence.

`inventory()` paginates within one canonical organization, verifies every
complete object's bytes, and either returns the complete bounded snapshot or
fails. Cross-tenant results, cycles, invalid entries, missing/corrupt objects,
item/page limits and transient failures never return partial orphan candidates.
`delete_generation()` requires the observed generation and SHA-256 and sends an
`ifGenerationMatch=<generation>` DELETE. A replacement between inspection and
DELETE is protected by the provider precondition. Lost DELETE success is retried
with the same condition; an already absent generation is safe to acknowledge.
The caller must still hold the PostgreSQL lock and protect current/completed
fences. Do not apply blanket age-based lifecycle deletion to inputs or completed
outputs: an object age is not proof that its fence is stale.

## Local evidence and remaining limits

Reproduce without credentials, Docker or a GCP account:

```sh
python3 -m unittest cloud_tests.g3a.test_gcs -v
```

The local fault server checks concurrent immutable creation, actual byte/hash
round trip, resumable chunks/partial acknowledgements, lost chunk/final responses,
429 bounds, vanished sessions, corrupt metadata/media, two organizations,
private destination modes, malformed keys, malicious Location URLs, redirects,
loopback opt-in, conditional deletion races, lost DELETE response, paginated
orphan inventory, limit/cycle/cross-tenant failure and missing completed objects.
With `G1_TEST_DSN` and the historical `integration:media_tests` PYTHONPATH,
one additional integration test executes unchanged G2 ingestion, PostgreSQL
submission/outbox, durable local dispatcher, real FFmpeg, this REST adapter,
three byte/probe-verified outputs, final manifest and completed replay through
the local HTTP server, including a lost upload success. Without PostgreSQL this
test explicitly skips; it must run in the complete blocking runner. This is
local adapter/pipeline evidence, still not remote GCS evidence.

The checked-in runner's raw logs and final exact-commit CI are the execution
record; this document does not replace that evidence.

Reads are buffered with a hard `max_object_bytes` bound (128 MiB default), and
uploads hold one bounded chunk in memory (8 MiB default). Configuration must fit
worker memory and FFmpeg limits. `stat()` deliberately reads the whole generation
for integrity; repeated verification incurs GET/egress cost and latency. Cloud
performance, resource use, provider retry behavior, object versioning/retention,
end-to-end G2 execution against remote GCS and costs remain **NOT_VALIDATED**. A remote
metadata SHA-256 claim, fake-server success or local concurrency run supplies no
cloud scalability evidence.

## Official references reviewed

Retrieved on 2026-11-07; documentation requests only, no GCP API resource calls:

- [Cloud Storage request preconditions](https://docs.cloud.google.com/storage/docs/request-preconditions):
  generation match `0` creates only when there is no live object; mismatches
  return `412 Precondition Failed`; use generation preconditions for retries.
- [Performing resumable uploads](https://docs.cloud.google.com/storage/docs/performing-resumable-uploads):
  keep Location private; anyone holding a session URI can upload without further
  authentication; chunk acknowledgements use `308` and Range; query status after
  uncertainty rather than assuming all bytes were accepted.
- [JSON API objects.get](https://docs.cloud.google.com/storage/docs/json_api/v1/objects/get)
  and [objects.delete](https://docs.cloud.google.com/storage/docs/json_api/v1/objects/delete):
  generation preconditions protect exact object generations.

These references define the intended provider contract; they are not proof of
remote adapter compatibility. G3B must prove that contract on a private temporary
bucket with scoped service identities and an explicitly approved budget.
