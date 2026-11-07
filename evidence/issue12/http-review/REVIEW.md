# Issue 12 / PR 9 independent HTTP and Auth review

Reviewed source: `/workspace/issue12-pr9-clean`, HEAD `e40357d08fa3c0b258b63c0d3a811cbab2b23206`.
Scope: `engine/shared_upload_http.py`, inherited `engine/cloud_http.py` and
`engine/cloud_auth.py`, HTTP/Auth integration tests, and relevant shared
ledger calls. Read AGENTS, README, G0 decision, Issue 12, G3A Auth and G3A.1
contracts. No source edits, commits, pushes, approvals, merge or deployments.

**Result: no OPEN material finding in this scope.** This review adds no blocker
to requesting human consideration of a controlled local-scope merge. It does
not replace the full regression, SQL, lease/deadline, backpressure and IaC gates.
It supplies no G3B/staging/cloud readiness or authorization.

## Independent execution

Primary evidence: [official-probe-extended.log](official-probe-extended.log).
The diagnostic used a newly created official GoTrue 2.197.0 and PostgreSQL
16.15 laboratory, actual signup/password/login/logout, current `auth.sessions`,
real private disk, restricted API credentials and two spawned HTTP processes
(PIDs 190546 / 190571, diagnostic parent 190479). It exited 0. The custom probe
also started bounded in-process HTTP servers with deliberately paused real
ObjectStore I/O to control the exact revocation ordering; these are identified
as diagnostic servers, not additional independent replica processes.

```sh
G3A1_PYTHON=/workspace/g3a-venv/bin/python \
  bash /workspace/issue12-evidence/http-review/run_probe_official.sh \
  > /workspace/issue12-evidence/http-review/official-probe-extended.log 2>&1
cd /workspace/issue12-pr9-clean
/workspace/g3a-venv/bin/python -m unittest cloud_tests.g3a.test_auth -v \
  > /workspace/issue12-evidence/http-review/auth-crypto.log 2>&1
```

The external runner is copied from the pristine `scripts/run_g3a1_auth.sh`, with
only its checkout directory and final diagnostic invocation changed. It retains
the original pinned PG/Auth images, temporary secrets, local ports, resource
limits, and cleanup trap. It creates official Auth tables through GoTrue; it
never calls the G1 auth fixture preparer. `probe_official.py` reuses only the
original official-suite setup helpers, then executes its own diagnostic order.
This is not presented as the repository's full 276-test run. The initial,
narrower successful diagnostic is retained as `official-probe.log`; the script
was subsequently extended and the primary log above was produced by its current
contents.

The separate inherited cryptographic suite passed **9 tests, zero skips** in
0.055s. That suite is LOCAL_CRYPTO_CONTRACT evidence, not official Auth issuance.

## Verified behaviors

| Boundary | Independent observation |
| --- | --- |
| Organization and forged headers | Actual user token cannot admit uploads into the other org: 403 on both replicas; identity headers without a bearer token: 401 on both. User signup metadata deliberately contains a fake organization and service role. |
| Writer downgrade | After SQL owner-to-viewer downgrade, new reservation and existing PUT grant both return 403 on both replicas. |
| Official session expiry | A real retained session with `not_after` in the past returns 401 on both replicas and the restricted checker returns false. |
| Owner binding | Another actual user with editor membership in the same org gets 400 for receipt lookup and PUT use of the original owner's grant on both replicas. |
| New publication revocation | Membership deletion commits while real storage publication is paused; after release, HTTP returns 403, receipt count stays zero, reservation remains publishing, and quota remains charged. No nested membership deadlock. |
| Resume framing | Duplicate Content-Length: 400; Transfer-Encoding: 400; declared 1,048,577-byte body: 413; duplicate Authorization: 401. Rejections carry no-store and Connection: close. |
| Total receive timeout | One-byte-at-a-time resume body returns 408 after 1.002s with a configured 1s receive deadline. |
| Private binding | 0.0.0.0, 10.0.0.1 and IPv6 ::1 all rejected; transport remains IPv4 loopback-only. |
| Shared-only operation | Zero SQLite files in the independent official lab. |
| JWT contract suite | Nine inherited checks pass: issuer/audience/required claims/expiry, signature and algorithm trust, JWKS rules, anonymous/service-role rejection, metadata exclusion and failing session backend. |

Source review additionally confirms the selected application requires a
PostgresUploadAuthority using the exact same API DSN, rejects legacy SQLite or
privileged/mismatched initialization, and delegates historical render/download
routes. The authority and HTTP principal checks reject owner/bypass/superuser
credentials and extra inherited roles. The official lab's API login has only
authenticated/session-reader membership; SQL service reconciliation remains
separate. Generic public error categories do not echo DSNs, JWTs or exceptions.

## Revocation candidates investigated and closed

| Candidate | Source and reproduction | Owner / status / merge impact |
| --- | --- | --- |
| Session logout during already admitted PUT | `shared_upload_http.py:55` checks session liveness before `:70` publication; authority completion receives user identity rather than a session. Real GoTrue logout was committed before releasing paused put_file. The checker was false, but the in-flight request committed one receipt and returned 201. Fresh requests returned 401 on both replicas. | HTTP/Auth; RESOLVED as documented contract, no defect severity and no merge blocker. `docs/G3A_AUTH.md:72-74` explicitly states revocation after request liveness does not cancel an already running HTTP operation. |
| Membership deletion during existing receipt verification | `shared_upload_authority.py:378-380` and `:450-452` authorize in SQL, read actual stored bytes, then replay an existing immutable receipt. Revocation committed while get_file was paused; in-flight GET returned 200 and PUT replay 201; subsequent requests on both replicas returned 403. No new receipt or quota charge occurred. | HTTP/shared authority; RESOLVED as request admission/read authorization boundary, no defect severity and no merge blocker. The docs promise fresh membership in each short SQL transaction, not atomic cancellation of in-flight storage read/replay. The mutation path separately rechecks membership and was independently denied after revocation. |

These observations constrain the claim: revocation rejects subsequent requests
and prevents a new receipt after membership revocation; it does not promise to
cancel every already admitted request. Requiring stricter cancellation would be
a new contract and needs an explicit design, not relabeling this local gate.

## Environment, provenance and cleanup

The Docker skill and Supabase skill/public Auth session documentation were read.
The current public changelog is retained as `supabase-changelog.md`; listed CLI
v3 and TLS changes do not alter this direct local test. No hosted project calls,
cloud credentials, login or paid resources were used. No dependency was installed.
The two own Auth/PG laboratories exited through their cleanup traps; a final
Docker listing contained no `g3a1-auth-*` containers. Other reviewers' containers
were left untouched. The source worktree remained clean at the reviewed SHA.

Remote Auth, hosted Supabase, production ingress/TLS, remote ObjectStores,
hard cancellation of arbitrary blocking I/O and combined production pool sizing
remain NOT_VALIDATED. The existing docs explicitly retain those boundaries.
