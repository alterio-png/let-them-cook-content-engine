# G3A.1 PostgreSQL upload authority

This additive migration leaves every G0/G1/G2/G3A source and migration unchanged. The legacy SQLite authority remains available only to its historical callers. The shared API uses the private `g3a1_private` schema and PostgreSQL as the source of truth for reservations, quota and receipts.

## Transaction and authorization boundary

Every mutating operation locks the verified user's current membership `FOR SHARE`, then the organization's quota row `FOR UPDATE`, then its reservation. Membership revocation waits for an already authorized transaction; subsequent operations fail. Authentication and session verification remain the HTTP layer's responsibility. Database authorization uses `auth.uid()` and current membership, never user-editable metadata or an organization header.

API logins retain only the historical `authenticated` and session-reader role boundary. They receive no ledger write privileges. Narrow `SECURITY DEFINER` functions use an empty search path, explicit qualified relations, organization and owner checks, and no default `PUBLIC` execution. They live in a private schema that must stay outside the Data API's exposed schemas. Tables have RLS; authenticated reads require current organization membership and, for reservations/receipts, immutable ownership. Anonymous access and raw authenticated DML are denied.

The separate `g3a1_upload_reconciler` role is `NOLOGIN`, `NOINHERIT`, `NOBYPASSRLS`, has no table privileges, and can execute only internal recovery functions. Provisioning a dedicated service login and granting that role is an operator action. Never grant it to an HTTP login. Internal recovery can preserve a historical receipt after the owner loses membership or is deleted from Auth. Historical owner UUIDs intentionally have no foreign key to memberships or `auth.users`; organization and composite receipt foreign keys remain restrictive.

## Quota and state machine

Trusted administrators provision `upload_limits` per organization with `max_bytes`, `max_count`, `max_upload_bytes`, `max_ttl_seconds` and `max_lease_seconds`. There is no client provisioning endpoint. Caller-requested TTL and lease lengths are bounded by this database configuration. Expiration and fencing use PostgreSQL's clock. `used_bytes` and `used_count` include all admitted reservations, published receipts and uncertain external operations, until a demonstrably safe release.

The unique organization/request key binds owner, byte count, SHA-256, immutable object key and original expiry. A matching retry returns the same logical grant. Another owner or changed payload conflicts. Expired keys remain tombstones and cannot create a fresh grant. Organization-wide quota admission is serialized, including requests from different processes.

```text
reserved (never claimed) --> publishing --verified object--> uploaded
         |                    |   ^
         | safe expiry        |   | new fenced recovery lease
         v                    v   |
       expired              uncertain
```

Only an expired `reserved` row with fence zero can release quota. Expiration, its audit event and both counter updates commit atomically and occur once. Lazy expiration is bounded to 1,000 rows per admission; explicit expiration supports batches of 1–1,000. Large backlogs require repeated bounded reconciliation batches.

Each claim increments its fence and creates a random bounded lease. The returned `lease_seconds` is the remaining duration calculated with the database clock, allowing heartbeat scheduling to follow a stricter database limit than the replica requested. Heartbeats require the current unexpired token/fence. Completion checks organization, owner, key, bytes, SHA-256, generation and the unexpired fence before inserting the single immutable receipt. A repeated completion with the current fence returns the original receipt after a lost response. A superseded fence cannot commit or change a valid result. Receipt foreign keys bind all immutable reservation claims, including organization and owner. Receipt/audit updates and reservation identity changes are rejected by triggers.

## Recovery, retention and safe GC

The object store and PostgreSQL cannot share an atomic commit. A crash after claiming may have published bytes even when the object is currently missing or its response was lost. Therefore **no previously claimed reservation is automatically refunded or deleted**. A missing object becomes `uncertain` and remains charged. Recovery of the same admitted operation is allowed after its original grant expiry, using a new fenced lease and the same immutable expected object key/body. An expired, never-claimed grant cannot be resumed.

Recovery verifies actual object bytes/hash/generation before recording a receipt. An older writer can only attempt the same immutable key and payload; it cannot overwrite the current PostgreSQL receipt. The local store's create-if-absent contract is required. Remote GCS/R2/S3 execution remains `NOT_VALIDATED` here.

Reservations, receipts and audit events have no automatic deletion policy in this gate. Uploaded and uncertain quota stays charged. A future operator GC policy must first drain/fence every possible writer, verify that no lease can resume, conditionally delete the exact object generation, and commit an explicit release exactly once. A missing-object observation alone is insufficient. No such destructive GC is implemented or authorized in G3A.1.

## Operational rollback

Rollback means stop admitting shared uploads, drain in-flight requests, preserve the PostgreSQL ledger and private object directory, and retain the shared authority for read/recovery. Never switch an admitted organization back to SQLite: it lacks the authoritative counters and receipts. The historical code remains available for historical regression, not as a data migration path. Do not drop this schema while any reservations, receipts or uncertain writes remain.

For a disposable test database only, the database owner can delete events, receipts, reservations and limits in that order before removing fixture organizations/users. No application role has these deletion privileges.

## Validation boundaries

SQL was applied and exercised against real local PostgreSQL 16.15, including concurrent quota admission, lost-response replay, expiry refunds, post-expiry recovery, stale fences and revoked membership. The automated G3A.1 fault/security suite supplies reproducible checks; the PR's final evidence records complete suite results and two-process HTTP measurements.

Current Supabase documentation and changelog were checked on 2026-11-07. `supabase db advisors --type security --level warn` was attempted against its local target and failed because no full Supabase CLI stack listens on port 54322. Advisor execution is `NOT_VALIDATED`; direct PostgreSQL security tests do not assert hosted Supabase validation. No hosted database, cloud resource, deployment or paid service was used.
