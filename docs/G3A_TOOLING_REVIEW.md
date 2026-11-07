# G3A local tooling provenance and validation boundaries

All new dependencies are confined to an external virtual environment and local
containers. No cloud principal, production credential or paid service was used.
`requirements-g3a.lock` includes the unchanged G1 lock by reference and exact
binary-wheel hashes; installation uses `--require-hashes --only-binary=:all:`.
The Auth source/image/Python dependency review is in G3A_AUTH.md. The Terraform
tool/provider pins and restrictions are in G3A_IAC.md. Local `init -backend=false`
and provider schema validation were executed without cloud credentials, plan,
apply or Google API calls. This does not validate effective deployed IAM.

The official Supabase CLI was used only for local migration generation/advisor
inspection. The current GitHub release reported **v3.0.0**. Before execution,
the official repository's MIT license metadata and release checksum were checked;
the Linux amd64 archive SHA-256 was:

`eeb61edcd59a4735fcb5e58596574f25d03d420efca779f43ddb307e1cc5c754`

Commands `supabase --version`, `supabase migration --help` and `supabase migration
new --help` were inspected. `supabase migration new g3a_cloud_contracts --workdir
/workspace/content-engine` generated the committed migration filename. The CLI
binary is not vendored or required to execute tests; it was not linked to a
hosted project. Actual local migration SQL is applied through psycopg to the
explicitly guarded disposable database, without repeated migration history writes.

The current changelog and November CLI v3 breaking changes were reviewed before
the Auth integration. A local-only `supabase db advisors --type security
--fail-on error` attempt reported `ECONNREFUSED 127.0.0.1:54322`, because a complete
Supabase CLI stack is not running. It did not contact a remote project. Therefore
**official whole-stack/hosted advisors: NOT_VALIDATED**. This does not replace
the real privilege/RLS/security tests against the isolated PostgreSQL and official
Auth component. No `supabase start`, hosted mutation or paid provisioning was
performed to disguise this limitation.

Pinning/provenance is not a complete vulnerability scan, native-container SBOM or
legal clearance. Exact FFmpeg image/binary distribution, codec patent/license,
transitive native dependencies and ongoing security maintenance require separate
review before hostile/customer media, staging image approval or commercialization.
