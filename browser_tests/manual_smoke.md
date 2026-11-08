# Local browser manual smoke

This checklist records an independent operator exercising the actual local UI.
The automated `r0_e2e.mjs` result is `LOCAL_BROWSER_E2E` and cannot mark this
manual checklist as passed. The execution record must include its actual UTC
time, source SHA, browser version, operator, artifact directory and observed
outcome. An unexecuted item is `NOT_EXECUTED`, not `PASS`.

Start the isolated laboratory as documented in `scripts/run_g3br0_local.sh`.
Read its private ready file locally to obtain the loopback URL and synthetic
test account. Do not paste the password, bearer token, ready file or capability
URLs into logs, screenshots or the PR. Keep generated artifacts outside Git.

1. Open the loopback UI in an installed Chromium/Chrome browser. Confirm the
   page identifies this as a local synthetic-media experiment.
2. Enter the laboratory email and password and sign in. Confirm the expected
   organization is displayed. Open another unsigned-in browser context and
   confirm that it cannot access authenticated API/download routes.
3. Choose the generated synthetic MP4 and supplied SRT with the file inputs,
   or use the labeled synthetic sample action. Submit and watch the real
   asynchronous ingestion transition to a validated asset. Confirm that the
   render action is available only after validation.
4. Render the three videos. Observe the pending/running/completed states and
   preview all three actual video outputs. Play a segment where the supplied
   SRT cue is visible and listen for the reference tone.
5. Download 9:16, 1:1, 4:5 and the final manifest using the UI. Reopen the
   downloaded files in the browser/media player. Verify the dimensions,
   duration, audio streams and SHA-256 values against the manifest using
   FFprobe and SHA-256 tools. Save probes/hashes with the execution record.
6. Sign out. Confirm the UI returns to login, previews/downloads are cleared,
   and the previous signed session fails a new authenticated operation.
7. Record failures and limitations without replacing them with the automated
   suite result. This local smoke does not validate hosted Auth, remote
   staging, cloud capacity, costs or production readiness.

| Record field | Required execution value |
| --- | --- |
| Gate | `LOCAL_MANUAL_SMOKE` |
| Status | `NOT_EXECUTED` until a separate actual execution record exists |
| UTC time / operator | Actual observer and timestamp |
| Source / environment | Git SHA, browser, PostgreSQL, Auth and FFmpeg versions |
| Evidence | Screenshots without credentials, probes, output hashes, observations |
| Limitations | All unexecuted or blocked checks explicitly listed |
