# Run the real local browser demonstration

Use a disposable Linux workstation with Docker on `/var/run/docker.sock`,
Python 3.12, Node >=20, an installed Chromium/Chrome, and FFmpeg/FFprobe with
libx264, AAC and libass. No cloud account or production credential is needed.
The two pinned Docker images and hash-locked packages are reviewed dependencies;
first startup may retrieve them from their public official registries.

```sh
git clone https://github.com/alterio-png/let-them-cook-content-engine.git
cd let-them-cook-content-engine
git checkout feat/g3b-r0-local-browser-01
python3 -m venv /tmp/ltc-r0-venv
/tmp/ltc-r0-venv/bin/pip install --require-hashes --only-binary=:all: -r requirements-g3a.lock
npm ci --prefix browser_tests --ignore-scripts --omit=optional
G3BR0_PYTHON=/tmp/ltc-r0-venv/bin/python \
  bash scripts/run_g3br0_local.sh --output /tmp/ltc-r0-demo-01
```

The output directory must be new and outside the checkout. Startup creates only
its own local Docker network, PostgreSQL and official GoTrue containers, users,
two synthetic organizations and private local ObjectStore. It prints the
loopback browser URL and path to the operator-only `public-ready.json`.
Despite that historical field name, this 0600 file contains disposable account
passwords: **never publish it**. It also identifies the synthetic fixture paths.
Open the printed URL in a browser on the same machine, log in with a seeded
account, select the organization, use **Amostra sintética**, upload/validate,
then **Renderizar**. Wait for the actual completed state and download the
three verified MP4 files plus manifest. Only the administrator-approved
synthetic pair is accepted; uploading arbitrary customer media is outside scope.

The token lives in page memory; reload/logout clears the UI session. Files and
SQL state remain private in the foreground lab. To demonstrate lost-response
recovery use the recovery control in the existing page session, which reuses
the request keys. A fresh page is not a persistent client workspace.

Ctrl-C stops admissions and drains the worker, allowing the existing 120 s job
bound. Fully observed terminal labs remove only their owned Docker resources.
If work is uncertain or draining cannot complete, stopped owned DB/Auth/network
resources are retained. Read `retained-resources.json` and `teardown-state.json`
privately; retain the DB dump and objects together. Do not remove/refund an
uncertain allocation merely because the process stopped. Reconciliation is
report-only (`python -m engine.local_runtime reconcile --config WORKER_CONFIG`).
The report can include live unpublished inputs; it is not garbage-collection
authorization. Config files are privileged operator files, never browser input.

## Reproduce the complete proof

Use a new path each run. The following runs all historical suites unchanged,
then focused real PostgreSQL ingestion tests, real GoTrue/API negative and
concurrency/failure tests, and the real Chromium browser E2E:

```sh
G3BR0_PYTHON=/tmp/ltc-r0-venv/bin/python \
G3BR0_EVIDENCE_DIR=/tmp/ltc-r0-proof-private-01 \
G3BR0_EXPORT_DIR=/tmp/ltc-r0-proof-public-01 \
  bash scripts/run_g3br0_checks.sh
```

No browser executable means failure, never a skipped PASS. `R0_CHROMIUM` can
select an already installed compatible binary; record the actual version.
The browser driver uses the OS sandbox when available; a root-container launch
requiring `--no-sandbox` is explicitly recorded and does not prove browser
sandbox enforcement. See `G3BR0_BROWSER_DEPENDENCIES.md` for license/hashes.

`--test` uses an additional database in the same disposable cluster for the
historical internal `auth.uid` fixture tests. Those tests are distinct from the
actual official GoTrue database/session tests. `--prepare-tests` starts a
foreground lab with that isolated fixture database for an independent reviewer;
normal manual mode does not require test administrative credentials.

Never upload the private evidence tree. The exporter allowlists sanitized test
logs, fixed-field events/resources and synthetic browser outputs/screenshots.
It excludes configs/ready files, tokens, raw Auth logs, DB dumps and private
objects. Inspect any failed-test log before sharing. CI uploads the exported
tree only; five historical workflows remain unchanged.

For manual exploration, follow `browser_tests/manual_smoke.md` and record the
actual operator, time, browser and outcomes. Automated E2E does not establish a
human-operated smoke test or hosted service. Both proof types must state their
operator and execution method truthfully.
