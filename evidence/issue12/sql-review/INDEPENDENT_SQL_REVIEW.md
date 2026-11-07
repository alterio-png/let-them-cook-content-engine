# Issue12 independent SQL review

Reviewed HEAD: `e40357d08fa3c0b258b63c0d3a811cbab2b23206`.
Main reference: `7a271382487ac4d16e278e5ccb8c345279b3cc16`.

No material SQL defect was found in this review. No SQL blocker was identified
for the remaining human merge review. This is review assistance, not a human
GitHub approval or authorization to merge, deploy, or use hosted resources.

## Scope and method

Read AGENTS.md, README.md, G0_DECISION.md, Issue12 in full, G3A.1 design/schema/
decision/runbook, the additive upload migration, and the prior membership-lock
helper. Used the Supabase skill, current public Supabase changelog and public
documentation, plus cloud runtime Docker guidance. No hosted Supabase endpoint
was called. A separate static second pass found no material application-reachable
SQL defect independently of the author's conclusions.

Applied the complete historical-plus-additive migration chain to a fresh own
loopback PostgreSQL 16.15 container, pinned to
`postgres@sha256:5264710be20f409750ac6dc4e5706dcef929c87e241605463f0f9aefa932a8d9`.
Used separate actual restricted NOINHERIT API and recovery logins, synthetic
fixture users/organizations/body only, and port 55611. The reviewed checkout
remained clean before and after checks. All diagnostic code and evidence are
outside that checkout.

## Actual result

**36 independent checks passed; final fresh preparation and both diagnostic
scripts exited 0.** These checks are additional review diagnostics, not an
addition to the PR's claimed 276-test count.

- The actual catalog has four RLS tables and 27 functions with empty search
  paths; no function grants PUBLIC execution. All 112 role/table/privilege cells
  match the intended table boundary. User operations, helpers and service
  operations have separate execution boundaries. Temporary function shadows
  could not hijack builtins in the definer path.
- Null identity, viewer writes, cross-organization claims, raw DML, helper calls
  and authenticated service calls fail. RLS hides another owner's row even
  within the same organization. A membership downgrade waits for an already
  authorized transaction, then denies subsequent writes.
- Same-key owner/size/hash conflicts fail. Matching retries retain the original
  identity and expiry. Twenty-four concurrent matching SQL calls yield one
  grant, one reservation and one charge. Twelve concurrent distinct requests
  against a three-operation/100-byte cap admit three and reject nine; committed
  usage is 93 bytes/3 operations.
- Cumulative usage includes uploaded, reserved and uncertain operations.
  Unused expiry refunds exactly once and retains the expired key tombstone.
  A deliberately failing expiry audit insertion rolls back state and counter
  changes together. A claimed uncertain operation remains charged after its
  original TTL and lease.
- Stale fenced completion fails without changes; current-fence replay preserves
  one immutable receipt. Composite FKs reject mismatched receipt owner, org,
  size and SHA even for trusted-owner test inserts.
- Actual quota-row lock delay past the absolute deadline rejects completion.
  A separate audit-trigger delay after receipt insertion also rejects the
  attempt, atomically rolling back receipt, state and audit.
- Actual Auth fixture user deletion removes user access while retaining the
  historical receipt and quota. The isolated recovery role can read the stable
  uploaded result through its internal function without table privileges.

## Source anchors

Migration `supabase/migrations/20261107150813_shared_upload_authority.sql`:

| Property | Lines |
| --- | --- |
| Private schema and recovery role | 4–12 |
| Quota/identity/state constraints | 14–47 |
| Composite receipt FKs | 51–64 |
| Immutable identity/receipt/audit triggers | 79–105 |
| RLS and read ACL | 107–119 |
| Lock order and safe expiry refund | 140–173 |
| Idempotent fingerprint and cumulative admission | 175–209 |
| Claims, TTL, lease and monotone fence | 212–258 |
| Post-lock heartbeat deadline | 261–301 |
| Receipt identity/fence and post-write deadline checks | 304–365 |
| Uncertainty preserves charge | 368–403 |
| Current-member read/expiry/metrics | 406–450 |
| Final helper/user/service execution ACLs | 453–473 |

The prior `g3a_private.lock_api_membership` helper, in migration
`20261107123845_g3a_cloud_contracts.sql` lines 48–65, checks the current subject,
locks the current membership FOR SHARE and restricts writes to owner/admin/editor.

## Findings and boundaries

No material finding was opened. Consequently there is no unresolved SQL fix or
RESOLVED product finding to attribute to this independent review. Owner for
maintaining the reviewed SQL/role contract is the PR #9 maintainer.

Known documented limits remain: migration-owner provisioning is trusted;
the schema must stay outside Data API exposed schemas; user/session authenticity
is enforced by the backend; compatibility SQL wrappers without an operation
deadline still exist while the shared adapter uses the bounded wrappers;
COMMIT/fsync acknowledgement is not a hard real-time guarantee. No destructive
GC or refund for previously claimed work is present. These are explicit local
design boundaries, not new blockers found in this review.

Hosted Supabase advisor execution, deployed Auth/Data API settings, remote object
generation semantics, cloud capacity and production behavior are NOT_VALIDATED
by these checks. The parent integration review owns the full suite and merge
readiness decision; this report covers SQL authority only.

## Reproduction and raw evidence

On a fresh local pinned container with the synthetic disposable DB named
`g1_issue12_sql` on loopback port 55611:

```sh
cd /workspace/issue12-pr9-clean
G1_TEST_DSN=postgresql://postgres:issue12-local-only@127.0.0.1:55611/g1_issue12_sql \
  /workspace/g3a-venv/bin/python scripts/prepare_g3a1_database.py
/workspace/g3a-venv/bin/python /workspace/issue12-evidence/sql-review/sql_adversarial_review.py
/workspace/g3a-venv/bin/python /workspace/issue12-evidence/sql-review/sql_constraint_quota_review.py
```

Raw files in this directory:

- `prepare.log`: exact fresh migration-chain application/version output.
- `adversarial.log`: 27 checks and final PASS marker.
- `constraint-quota.log`: nine supplemental checks and final PASS marker.
- `results.json`, `constraint-quota-results.json`: structured check results.
- `catalog.json`, `privilege-matrix.json`: actual function/role/RLS catalog data.
- `provenance.json`: reviewed source SHA, clean tree, runtime and content hashes.
- `sql_adversarial_review.py`, `sql_constraint_quota_review.py`: complete review
  diagnostics, including local-only synthetic credentials.

Only the reviewer's own `issue12-sql-review` disposable container is removed
after evidence collection. Other agents' databases and resources are untouched.
