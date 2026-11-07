# Object publication contract review

Reviewed source: `/workspace/issue12-pr9-clean`, exact HEAD `e40357d08fa3c0b258b63c0d3a811cbab2b23206`.

Result: no OPEN correctness finding from this focused review. Source was unchanged and `git status --porcelain` returned empty after checks.

Read AGENTS.md, README.md, docs/G0_DECISION.md; independently reviewed engine/object_store.py, shared_upload_authority.py source-copy/readback/publication/resume/reconcile methods, relevant upload failure tests, and generation handling in engine/gcs_store.py. engine/object_store.py has no diff from main `7a271382487ac4d16e278e5ccb8c345279b3cc16`.

Key reasoning:

- Local create writes/fsyncs a private staging inode and atomically hard-links the complete object. Existing keys accept only the same key/hash/size metadata. Shared OS-user processes are explicitly trusted by this backend.
- The shared authority copies and hashes the exact admitted source into a private temporary file before claiming. Later mutations or replacements of the original caller source cannot change published bytes.
- Receipt admission re-downloads actual bytes, independently hashes them and checks key/size/hash. An existing receipt's non-null generation must match the observed provider generation. The publication response's available generation must also agree with readback.
- Shared recovery performs no destructive deletion/refund. Remote provider execution and hard process I/O termination remain outside this local proof.

Actual commands, exit status 0:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/workspace/issue12-pr9-clean /workspace/g3a-venv/bin/python /workspace/issue12-evidence/backend-review/object-contract/probe_contract.py > /workspace/issue12-evidence/backend-review/object-contract/probe_contract.log 2>&1
PYTHONDONTWRITEBYTECODE=1 /workspace/g3a-venv/bin/python -m unittest media_tests.g2.test_object_store -v > /workspace/issue12-evidence/backend-review/object-contract/object_store_suite.log 2>&1
```

Independent diagnostic: 6 tests PASS, no skips. Historical object store suite: 18 tests PASS, no skips. The six independent checks cover original-path replacement after source freeze, mismatched/oversized source, self-consistent incorrect downloaded bytes, incorrect metadata, missing/changed committed generation and 20 concurrent conflicting creates. All use synthetic bytes in automatically cleaned temporary directories.

NOT_RUN here: SQL transaction/fencing tests (handled by parent reviewer), remote GCS/R2/S3, cloud resources, power-loss/filesystem durability simulation, production rollout. No production claim is made.
