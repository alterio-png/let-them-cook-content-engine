# Independent closure QA — Issue 12

Reviewed source: `e40357d08fa3c0b258b63c0d3a811cbab2b23206` in the separate clean `/workspace/issue12-pr9-clean` checkout. Audit-document checkout `/workspace/issue12-audit` was at historical main `7a271382487ac4d16e278e5ccb8c345279b3cc16` and clean when checked. No source edits, provisioning, database writes, dependency installs, approvals, merges, deployment or Terraform operation were performed by this QA.

## Verdict

**GO to request a separate human review/merge decision for the local PR #9 scope**, subject to final evidence-package completeness. No material source correctness issue or hidden merge-blocking finding was found in the reports/raw evidence reviewed here. This is not a GitHub approval or authorization to merge, and supplies no staging, production, throughput, cloud or G3B-P1 readiness.

Read AGENTS.md, README.md, docs/G0_DECISION.md, Issue 12 JSON, all four independent review reports, the backend object-contract report, relevant raw logs and diagnostic scripts, reviewed HTTP/authority and Auth/schema/design contract passages, full regression runner/workflow, and IaC log.

## Independently checked facts

- The source checkout resolves to the exact reviewed SHA and `git status --short` is empty.
- `full-276.txt` has exactly **276 distinct successful unittest IDs**, with no duplicate IDs and zero skipped-test markers: **12 + 36 + 49 + 118 + 10 + 44 + 7 = 276**. Extra reviewer diagnostics and reruns overlap or supplement this set; they must never inflate the 276 unique-test claim. The initial HTTP probe log is historical and its extended log is the evidence for the current diagnostic contents, as disclosed by its reviewer.
- All **114** lines in `G3A1_BASELINE_SHA256SUMS.txt` independently match file bytes from `git show 7a271382487ac4d16e278e5ccb8c345279b3cc16:<path>`. The full run also records the baseline checksum pass. This proves preservation of the designated history, not that every future document branch was tested.
- The raw IaC log has Terraform 1.17.1, Google provider 8.10.0, `init -backend=false`, `validate.valid=true`, zero errors and zero warnings. It supports local schema validation only; it includes no plan/apply claim.
- Fresh shared-benchmark raw events independently match the first two summary cohorts: **100 logical / 140 physical / 40 recovered 503 / max 2 attempts**, and **20 logical / 20 physical / no 503 / max 1 attempt**. Runtime records the reviewed SHA and clean worktree, Python 3.12.14, PostgreSQL 16.15, five logical CPUs, and 2026-11-07 19:46:04–19:46:06 UTC. The publication cohort records five physical expected-status lifecycle calls; its numeric `operation` value is reused and is not a cross-route unique operation identity.
- Archived CI is separately identified: **100 logical / 305 physical / 205 recovered 503 / max 5 attempts**. It must not be described as the fresh run, nor as throughput. Its original archive hash is `dd8464d5ada9c45eb46c24518daad9077522477a76bfa0cd7b557a4d53fb52da`.
- Existing SQL/backend/HTTP reviewer checksum manifests all validate in their original locations. SQL evidence supplies 36 separate PASS diagnostics; backend report explicitly labels its 50 executions as overlapping the unique full-suite set. HTTP extended raw log discloses both investigated revocation outcomes rather than hiding successful in-flight responses.

## Findings interpretation

The HTTP logout observation is explicitly within `docs/G3A_AUTH.md`'s existing admission boundary: revocation after liveness check does not cancel a running request. Receipt GET/replay authorization occurs in a short SQL transaction before storage readback; membership deletion during that readback allows the already authorized immutable receipt response, while fresh requests fail. `docs/G3A1_DESIGN.md` explicitly avoids a membership transaction across storage I/O. A new receipt completion after membership deletion was independently denied, retained its claimed quota, and produced no receipt. These are disclosed contract boundaries, not evidence of a hidden new mutation authorization bypass.

Carry **BP-02, BP-03 and BP-04 (Medium), plus BP-05 (Low), OPEN** in the final findings matrix. They restrict sustained/mixed load, aggregate resource budgeting and observability acceptance; they do not establish corrupt idempotency/quota or a local authorization bypass. BP-01 is RESOLVED as accounting ambiguity, without claiming a new source fix. Do not convert the other four to RESOLVED merely because the local gate passes.

## Final packaging requirements

1. Retain complete diagnostic source alongside raw logs; omit generated `__pycache__` files. Include all nested backend/object-contract evidence and HTTP provenance/support files in the final package, not only files listed in the narrower reviewer manifests.
2. Generate a final **relative-path** manifest for the complete frozen package. Backend and HTTP per-review manifests currently contain original absolute `/workspace/issue12-evidence/...` paths; they authenticate the original collection but cannot alone authenticate a relocated package. Do not silently present their path layout as portable.
3. Preserve originals and document path adaptation. SQL diagnostic constants use fixed disposable DSNs/ports/output directory; the HTTP external runner uses a fixed checkout/diagnostic path. The backpressure reanalysis script additionally needs `/workspace/g3a1-evidence/final-head-ci.zip`, which is outside the current evidence directory. Include that exact archive or document its authoritative retrieval and hash plus required original path before claiming reproducibility.
4. Keep **tested source SHA**, **historical main**, **untested document-review branch SHA** and archived-CI versus fresh-local runtime provenance distinct. A documentation-only branch based on historical main is not itself the source on which 276 tests ran.
5. Record explicit human approval as pending. Current PR authorship cannot be used for self-approval. Preserve the requirement for actual merge SHA and all five applicable workflows on new main before PR #11 synchronization.

A limited textual scan across the evidence found no JWT-shaped token, private-key header, GitHub token or common API-key shape. DSNs found are local synthetic fixture DSNs or variable-based local runner construction; they are not hosted credentials. This is a narrowly described review scan, not a general credential/security certification.
