# Independent backend publication review — Issue 12 / PR 9

Reviewed exact source HEAD: `e40357d08fa3c0b258b63c0d3a811cbab2b23206` in `/workspace/issue12-pr9-clean`; base `7a271382487ac4d16e278e5ccb8c345279b3cc16`. Source stayed unchanged: final `git status --porcelain=v1` was empty.

Result: **no OPEN correctness finding** from this scoped review. This supports the tested local PostgreSQL/private-disk contract only; it is not the complete Issue 12 gate or human merge approval.

Read AGENTS.md, README.md, G0 decision/log, the full live GitHub Issue 12 body and its empty comments list, all G3A1 design/schema/runbook/decision/benchmark documents, the complete shared authority, shared HTTP adapter and additive SQL migration, and relevant failure/deadline/replica test sources. An additional reviewer independently examined the object-store/publication boundary.

## Actual fresh results

| Execution | Result | Evidence |
| --- | --- | --- |
| Existing upload ledger failure + deadline modules against a fresh real PostgreSQL instance | 21 PASS, 0 skips, exit 0 | `failure-deadline-tests.log` |
| Independent backend probes: real heartbeat row-lock timeout, late object writer after newer fence, delayed receipt trigger crossing deadline, admitted upload finishing after original TTL | 4 PASS, 0 skips, exit 0 | `probe-backend.log`, `probe_backend.py` |
| Independent real SIGKILL after SQL receipt commit, before caller receives result | 1 PASS, 0 skips, exit 0 | `probe-sigkill-after-commit.log`, `probe_backend.py` |
| Independent object contract probes | 6 PASS, 0 skips, exit 0 | `object-contract/probe_contract.log` |
| Historical object-store suite rerun by the additional reviewer | 18 PASS, 0 skips, exit 0 | `object-contract/object_store_suite.log` |
| Total scoped test executions | **50 PASS, 0 skips** | These overlap the root review's larger suite; do not add them to its unique test count. |

PostgreSQL was **16.15**, official image `postgres@sha256:5264710be20f409750ac6dc4e5706dcef929c87e241605463f0f9aefa932a8d9`, isolated loopback port **55612**, database `g1_backend`, own container `issue12-backend-pg`. Python was **3.12.14** at `/workspace/g3a-venv/bin/python`. All data was synthetic; request handlers used newly created restricted nonowner NOINHERIT authenticated logins.

## Reviewed correctness conclusions

- Admission serializes organization quota and preserves immutable owner/body/hash/key/expiry identity. Only never-claimed expiry releases quota. All claimed failure paths retain quota and a recovery identity.
- Source bytes are copied and hashed before the durable claim. Object publication uses immutable create-if-absent storage; receipt admission separately reads and hashes actual stored bytes and checks metadata plus available generation.
- A durable claim precedes storage I/O. Crashes before/after object storage, SQL rollback, response loss and post-TTL recovery preserve one charge and at most one immutable receipt.
- Renewal uses the duration actually returned by PostgreSQL. Database-derived deadlines are checked after authorization/quota/reservation locks, after receipt/state/event writes, and through remaining-budget statement timeouts. An actual heartbeat lock failure prevented the caller from committing, and a receipt trigger delay caused complete rollback.
- The independent stale-writer probe deliberately blocked the first object writer past its operation deadline, completed a fresh authenticated resume with fence 2, then released the first writer. Its immutable write was harmless; its completion failed with `UploadBusy`; the current receipt, charge and state were unchanged.
- The independent SIGKILL probe stopped a fresh child inside `after_commit`, observed the durable receipt, killed that process with SIGKILL before any client result, then replayed through a new authority. The exact database receipt, single quota charge and fence 1 were preserved.
- The replay/GET paths verify actual object integrity before returning a completed receipt; the plain Python `receipt()` method is explicitly documented as metadata only. The HTTP paths use verified reconciliation.

No finding matrix row is warranted: no reproduced correctness defect remains OPEN in this scope.

## Commands and cleanup

The database password below is a redacted local disposable fixture value, never a hosted credential. Commands were run from the reviewed checkout, with all outputs outside it:

```sh
env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_TLS -u DOCKER_TLS_VERIFY -u DOCKER_CERT_PATH docker --host=unix:///var/run/docker.sock run --detach --rm --name issue12-backend-pg --publish 127.0.0.1:55612:5432 --env POSTGRES_PASSWORD=LOCAL_DISPOSABLE_PASSWORD --env POSTGRES_DB=g1_backend postgres@sha256:5264710be20f409750ac6dc4e5706dcef929c87e241605463f0f9aefa932a8d9
G1_TEST_DSN=postgresql://postgres:LOCAL_DISPOSABLE_PASSWORD@127.0.0.1:55612/g1_backend /workspace/g3a-venv/bin/python scripts/prepare_g3a1_database.py
G1_TEST_DSN=postgresql://postgres:LOCAL_DISPOSABLE_PASSWORD@127.0.0.1:55612/g1_backend PYTHONPATH=.:integration:upload_tests /workspace/g3a-venv/bin/python -m unittest g3a1.test_ledger_failures g3a1.test_deadline -v
G1_TEST_DSN=postgresql://postgres:LOCAL_DISPOSABLE_PASSWORD@127.0.0.1:55612/g1_backend PYTHONPATH=.:integration:upload_tests /workspace/g3a-venv/bin/python /workspace/issue12-evidence/backend-review/probe_backend.py
G1_TEST_DSN=postgresql://postgres:LOCAL_DISPOSABLE_PASSWORD@127.0.0.1:55612/g1_backend PYTHONPATH=.:integration:upload_tests /workspace/g3a-venv/bin/python /workspace/issue12-evidence/backend-review/probe_backend.py Probe.test_sigkill_after_sql_commit_before_client_result_replays -v
env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_TLS -u DOCKER_TLS_VERIFY -u DOCKER_CERT_PATH docker --host=unix:///var/run/docker.sock stop issue12-backend-pg
```

The first diagnostic command ran four tests before the SIGKILL test was appended; the second targeted command ran that added test. Separate logs prove both counts. Preparation exited 0 (`database-prepare.log`). All fixture reservations and temporary HTTP roles were removed before container shutdown (`fixture-cleanup.log`: both zero). The own container was stopped and automatically removed (`container-cleanup.log`). No other container/resource was touched. Additional object-store commands and scope are recorded in `object-contract/REVIEW.md`.

NOT_RUN by this reviewer: the root review's complete 276-test/GoTrue/benchmark/IaC workflow reproduction, remote PostgreSQL/Supabase security advisors, remote GCS/R2/S3 consistency, power-loss durability, cloud IAM/OIDC/Tasks/Jobs/Run, supervisor hard I/O termination, cloud costs, production rollout. No code fixes, source edits, commits, pushes, GitHub approvals/merges, cloud resources or Terraform plan/apply were performed.
