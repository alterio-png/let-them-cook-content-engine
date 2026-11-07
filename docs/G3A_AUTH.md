# G3A Auth: verified local component and trust boundary

Status: **LOCAL_OFFICIAL_AUTH_VERIFIED**, with ten real integration tests on
2026-11-07. This is the official Supabase Auth/GoTrue component against real
PostgreSQL, not a hosted Supabase project or the complete Supabase stack.
Hosted Auth, Data API/PostgREST, SMTP, OAuth providers, MFA, signing-key
rotation and production authentication are **NOT_VALIDATED**.

## Reproduce in an isolated clone

Requirements: Linux x86_64, Python 3.12, Docker, and the existing G0 FFmpeg /
ffprobe build. Network is needed only to obtain the pinned reviewed images
and wheels. No account, cloud credential or paid resource is needed.

```bash
python3 -m venv /tmp/ltc-g3a-venv
/tmp/ltc-g3a-venv/bin/pip install --require-hashes --only-binary=:all: -r requirements-g3a.lock
G3A_PYTHON=/tmp/ltc-g3a-venv/bin/python scripts/run_local_auth.sh
/tmp/ltc-g3a-venv/bin/python -m unittest cloud_tests.g3a.test_auth -v
```

The runner creates fresh uniquely named PostgreSQL/Auth containers and a
dedicated Docker bridge, binds both services only on `127.0.0.1`, and removes
containers, anonymous database volume and network on success or failure.
CPU, memory, PID and Auth privilege limits are explicit. Auth is non-root,
read-only, drops all Linux capabilities, and has a small noexec tmpfs.
Rootless Docker does not publish ports on an `--internal` bridge; the runner
uses a dedicated bridge with masquerading disabled. This is **not** a verified
egress firewall. No outbound identity provider or SMTP integration is configured.

Passwords and the local HMAC signing secret are generated for each run, exist
only in that disposable environment, and are never printed. Fixtures use
synthetic `example.invalid` identities and synthetic FFmpeg media. Local mail
autoconfirm is deliberately enabled for this fixture, not a production default.

`scripts/prepare_auth_database.py` refuses non-loopback DSNs, database names
outside `g3a_auth_*`, an existing Auth schema at bootstrap, or an existing engine
at the second stage. It creates only the Auth owner/schema/roles before starting
GoTrue. **The official image performs 75 Auth migrations**, supplying real
`auth.users`, password hashing, `auth.sessions`, refresh tokens, RLS and
`auth.uid()`. No G1 Auth fixture is applied. The second stage applies unchanged
G0/G1/G2 SQL and provisions the narrow API/session roles. The bootstrap is a local
test harness, not an approved hosted Supabase migration.

## Verification and authorization

`engine.cloud_auth.AuthVerifier.verify(raw_token)` returns only
`VerifiedIdentity(user_id: UUID, session_id: UUID)`. Verification uses PyJWT,
not custom signature cryptography, and requires:

- An operator-configured exact issuer and audience; required expiry, issued-at,
  UUID subject and UUID session ID; valid signature and timestamps.
- Exact algorithm/key agreement from a configured key map; HS256, ES256 or
  RS256 only. Public JWKS loading permits RSA keys of at least 2048 bits or EC
  P-256. HS256 is used only by the disposable official local Auth fixture.
- `role == authenticated` and `is_anonymous == false`; service-role credentials,
  anonymous sign-ins, `alg=none`, unknown key IDs, key/algorithm confusion and
  URL/key-bearing JWT headers are rejected. The verifier never fetches a URL.
- A fresh successful PostgreSQL session check on **every request**. The official
  `auth.sessions` row must match both the signed subject and session ID, and
  `not_after` must be absent or in the future. Database failures fail closed.

`user_metadata` and `app_metadata` are never used for organization membership,
role, identity or quota decisions. The HTTP boundary installs only the verified
subject into a transaction, switches to the non-bypass `authenticated` role,
and checks current PostgreSQL membership/RLS. The unchanged G1 submission
function holds membership locks during the transaction. Upload and download
authorization rechecks membership on use. A revocation after a request's liveness
check does not cancel an already running HTTP operation; there is no claim of
distributed atomic cancellation. Subsequent requests reject revoked sessions.

The API login is `NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE
NOREPLICATION`, owns no application/Auth tables, and can switch only to
`authenticated` and `g3a_session_reader`. The latter can select only `id`,
`user_id` and `not_after` from `auth.sessions`, under an own-subject RLS policy.
It cannot read passwords, metadata, refresh tokens or complete session rows.
No new Auth `SECURITY DEFINER` helper is needed. JWT validity alone never grants
an organization membership. Auth/session lookup opens one bounded short
connection; concurrent HTTP connections have a separate configured cap. Hosted
pool sizing and the combined connection budget remain a G3B validation gate.

## Evidence and limits

The initial successful official run produced `Ran 10 tests in 3.450s / OK`.
The tests call the real official HTTP signup, password sign-in and logout APIs:

- Signature verification and official password/session persistence are checked.
  After real logout, the still-unexpired JWT remains cryptographically valid,
  but the session check and actual HTTP API reject it.
- User deletion and an expired retained session row are rejected. Session-reader
  grants/RLS deny password reads and unscoped session reads.
- Two organizations are isolated with real verified tokens and RLS; malicious
  user metadata cannot grant access. Membership removal hides rows immediately;
  role downgrade prevents new upload grants and use of a previously issued grant.
- A real token submits through the actual loopback HTTP server, verifies
  idempotent reuse, reaches unchanged G2 PostgreSQL/outbox state, executes real
  FFmpeg in a separate worker, obtains all three outputs and authorizes a private
  download whose bytes and SHA-256 match the final manifest.

The nine `cloud_tests/g3a/test_auth.py` tests are separately labeled
**LOCAL_CRYPTO_CONTRACT**, using locally signed synthetic JWTs. They cover
expiry/issuer/audience/required claims, malformed or forged signatures, ES256 /
RS256 public JWKS, weak/private/symmetric JWKS rejection, role and anonymous
rejection, metadata, uncached revocation and database failure. They do not stand
in for official Auth integration or hosted service evidence.

G3B must validate hosted issuer/audience, approved public signing keys and
rotation/revocation propagation, secrets and TLS, Data API grants/advisors,
provider availability, session policies, pool budgets, MFA/OAuth where required,
and exact permitted client flows. The CLI database advisors require a complete
Supabase environment; hosted/whole-stack advisor execution is **NOT_VALIDATED**.
Local explicit privilege/RLS tests are evidence only for the tested database.

## Reviewed sources and dependency pinning

Reviewed on 2026-11-07, before running the official image or installing packages:

| Component | Pin and review |
| --- | --- |
| Supabase Auth | Official release `v2.197.0`, tag commit `4eee58f296d9698a1c2c0ae14d7a0b379c7622d3`; Linux amd64 image `supabase/gotrue@sha256:839f529492d116b4e8b7777c953a27c381d34c15a744b1bcefde5eefaa1f9f9f`; source MIT license, official release notes, Dockerfile and migration source reviewed. No candidate application code is imported. |
| PostgreSQL | Existing reviewed G1/G2 image pin `postgres@sha256:5264710be20f409750ac6dc4e5706dcef929c87e241605463f0f9aefa932a8d9` (16.15). |
| JWT verification | PyJWT 2.15.1 (MIT); cryptography 50.0.2 (Apache-2.0 OR BSD-3-Clause), cffi 2.1.1 (MIT-0), pycparser 3.12 (BSD-3-Clause). Official PyPI metadata, wheel filenames, dependency requirements and SHA-256 reviewed; hash-locked binary-only installation in an external venv. |

The existing G1 driver dependencies are included by reference with unchanged
hashes. New package versions/hashes are in `requirements-g3a.lock`; its current
wheel targets are Linux x86_64/Python 3.12 (glibc >=2.34 for cryptography).
No vendored binary or unpinned Docker tag is accepted by this runner. Pinning is
not a security audit or an exhaustive CVE/transitive container license inventory;
those distribution checks remain required before staging or commercial shipping.

Authoritative references:

- [Current changelog](https://supabase.com/changelog.md), including [November 4 CLI v3 changes](https://supabase.com/changelog/supabase-cli-v3) and [October 12 TLS CBC deprecation](https://supabase.com/changelog/cbc-tls-ciphers-deprecated). No hosted CLI command or old TLS cipher is used here.
- [Sessions and strict session-ID checks](https://supabase.com/docs/guides/auth/sessions), [JWTs](https://supabase.com/docs/guides/auth/jwts), [signing keys](https://supabase.com/docs/guides/auth/signing-keys), [RLS](https://supabase.com/docs/guides/database/postgres/row-level-security).
- [Official Auth release](https://github.com/supabase/auth/releases/tag/v2.197.0), [pinned source license](https://github.com/supabase/auth/blob/v2.197.0/LICENSE), [official RLS/grants migration](https://github.com/supabase/auth/blob/v2.197.0/migrations/20240612123726_enable_rls_update_grants.up.sql).
