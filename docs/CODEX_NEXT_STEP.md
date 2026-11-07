# Codex task brief: G1 after the new private repository exists

## Preconditions
- The owner creates private `alterio-png/let-them-cook-content-engine` and imports this verified G0 baseline.
- Confirm `main` has the same file hashes as `G0_SHA256SUMS.txt`; run GitHub CI (real ffmpeg media tests) on the actual first commit.
- Do not touch, reset or rebase `alterio-png/let-them-cook-creative-os`, including its open PR #7.

## Single isolated objective
Prove the **PostgreSQL transactional control plane and idempotent worker handoff**, before implementing UI/AI/social publishing. Keep production credentials absent.

## Exact actions
1. Read README, AGENTS, G0_DECISION, SECURITY, ARCHITECTURE and SCALE_GATES. Check current GitHub branch, no unpublished changes.
2. Create `feat/g1-postgres-outbox-01` off the exact authorized main SHA.
3. Audit `sql/001_schema.sql` against an isolated local PostgreSQL/Supabase stack. Do not assume the draft SQL passes without running it.
4. Add migration tests for organization RLS using two distinct users and two orgs; verify cross-org read and foreign key cannot bypass org boundary.
5. Implement operation submit in a single transaction: auth, quotas, stable `request_key`, immutable request digest, render job, outbox and audit event. On duplicate, reconcile instead of republishing.
6. Provide a worker handoff test using stub queue adapter with duplicate/lost result, crash before/after commit, stale lease/fencing and no repeat side effects.
7. Add duration/pixel caps and cumulative cost reservation to the contract, with fake cost tests.
8. Run G0 regression, G1 tests, lint/type check if added; record exact versions, SHA, CI URLs and outcomes.
9. Open a DRAFT PR with evidence, potential blockers and rollback; do not merge/deploy automatically.

## Explicit exclusions
No real user/customer video, no real OAuth/social publication, no cloud resource, no AI model/weights, no buying services, no license approvals, no production migration, no merge. Focus on correctness and provenance, not a dashboard.

## Acceptance
- 100 concurrent requests for identical operation: exactly one logical job + one outbox event.
- Two orgs with separate users cannot read/write each other's jobs or assets.
- Every lost response/timeout/429 is reconcilable and cannot trigger duplicate effect.
- Worker with expired fence token cannot commit after a newer valid attempt.
- Full tests pass on the exact PR HEAD and startup instructions are reproducible from clean clone.
