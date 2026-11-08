# G3B-R0 browser dependency review

Reviewed on **2026-11-08 UTC**, for the local synthetic-video browser proof in
Issue #15. No browser or npm package is bundled into the product/runtime API.
The test driver uses Node built-ins plus exactly one reviewed npm package.
It does not execute downloaded GitHub source or use a hosted testing service.

| Dependency | Reviewed provenance |
| --- | --- |
| `playwright-core` | Version **1.62.1**, Apache-2.0, Node >=20, no runtime or optional dependencies and no package install scripts |
| Official registry metadata | <https://registry.npmjs.org/playwright-core/1.62.1> |
| Official tarball | <https://registry.npmjs.org/playwright-core/-/playwright-core-1.62.1.tgz> |
| Tarball SHA-256 | `954be1e183d0ddb9748fe0d2d08b0b66a9210c74dd75c397aeb70303b9f08a00` |
| Tarball SRI | `sha512-wPYSwEBJY9GHraISXqyqtx0na0LpO3XEX7jNDhntbex7tzUS7kLnZsOlFruFJB4Hi/rhDMjXGqHewDZ68nYZVw==` |
| Bytes retrieved | 3,070,300 |
| `package/LICENSE` SHA-256 | `45873d00a0dd243596deb4aa23b2493b3d1f0671921bf2538ea431d7380220eb` |

Metadata, tarball SRI, actual tarball SHA-256 and license were independently
read and matched before adding the lock. Tar members were checked to stay
under `package/` without parent traversal. `browser_tests/package-lock.json`
pins the exact official tarball and SRI. Install with scripts disabled:

```sh
npm ci --prefix browser_tests --ignore-scripts --omit=optional
```

That exact installation completed locally. The official npm advisory audit
(`npm audit --prefix browser_tests --omit=optional --audit-level=low --json`)
returned zero known advisories on 2026-11-08 UTC. This is the registry's result
at that time, not a guarantee against undisclosed vulnerabilities.

The larger `playwright` package is deliberately not needed: the standalone
driver uses Node assertions and `playwright-core` to control an already
installed browser. No `fsevents` or test-runner package is added.

## Browser/runtime boundary

The local execution environment already supplies Node 24.22.1 and Chromium
156.0.8078.79. CI may use its preinstalled Google Chrome/Chromium. The driver
accepts `R0_CHROMIUM` or discovers one of the standard installed executable
names and records the actual browser version. It fails rather than skips if
there is no executable or browser launch fails. Custom executable compatibility
is established by the actual E2E run, not assumed from a version string.

The driver explicitly enables Chromium's sandbox for a non-root OS user. A
root container needs the browser's sandbox disabled to start; that exception
is recorded as `DISABLED_ROOT_CONTAINER` in its result. The local environment's
production browser sandbox remains `NOT_VALIDATED`; the exception does not
approve a public browser service or untrusted remote browsing. The driver
blocks non-loopback page requests and uses only small approved synthetic media.

This change does not download Playwright's bundled browser or FFmpeg. The
installed system browser and FFmpeg distribution remain environment binaries,
not redistributed or commercially cleared product components. A future binary
download or container image requires its own provenance/license/hash review.

The browser proof never records a HAR, Playwright trace or request headers:
those could capture passwords, JWTs and capability URLs. It writes sanitized
route/status events, screenshots, downloaded synthetic outputs, hashes and
ffprobe results outside the source checkout. Browser automation is labeled
`LOCAL_BROWSER_E2E`; it does not establish manual testing, hosted Auth, cloud
capacity, billing or `REMOTE_STAGING` readiness.
