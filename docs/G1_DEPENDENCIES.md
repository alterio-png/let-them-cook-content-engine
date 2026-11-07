# Local G1 dependency review

Scope: isolated proof execution authorized by Issue #2, not distribution approval.
No candidate GitHub source, AI weights or customer content was downloaded/executed.

| Artifact | Pin / license | Review and boundary |
|---|---|---|
| PostgreSQL official Docker image | `postgres@sha256:5264710be20f409750ac6dc4e5706dcef929c87e241605463f0f9aefa932a8d9` (16-bookworm, observed 16.15) | Official Docker Library image; local disposable DB only; PostgreSQL license, Debian OS dependencies remain unapproved for a product release. |
| psycopg Python wheel | 3.2.13, LGPL-3.0-only | PyPI wheel metadata and included LICENSE inspected before install; no extra dependency groups. Hash in requirements-g1.lock. |
| psycopg-binary CPython 3.12 Linux x86_64 wheel | 3.2.13, LGPL-3.0-only | PyPI binary wheel, includes libpq (observed 17.6) and native dependencies; local test process only. Artifact hash pinned. Native license/advisory inventory needed before distribution. |
| typing-extensions | 4.16.0, PSF-2.0 | Metadata/LICENSE inspected, sole Python transitive dependency on this platform; hash pinned. |
| FFmpeg/ffprobe | Existing Debian 7.1.5-0+deb13u1 locally | Unchanged G0 binary; GPL/libx264 codec clearance remains pending. CI uses Ubuntu package and records behavior independently. |

Packages were downloaded as wheels from the configured PyPI index, inspected
without importing, then installed in `/workspace/g1-venv`. The version, dependency
metadata, license files and SHA-256 were inspected. Install with `--require-hashes
--only-binary=:all:`; the lock intentionally targets Python 3.12 Linux x86_64
rather than accepting unreviewed wheels for other platforms. No service billing,
telemetry integration or network queue client was introduced.

This is a provenance/limited license triage, not a complete vulnerability scan,
SBOM, hardened runtime or legal sign-off. Local proof dependencies can be removed
with the virtual environment and disposable container. Maintainer for any future
release must be assigned by the owner; production dependency review remains open.
