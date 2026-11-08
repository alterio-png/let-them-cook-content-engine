# Reproducible local proof, 2026-11-08

Actual GitHub clone: `/workspace/g3br0-clean-01`, clean working tree at
`e418760bd998e97272f12fdeefc1487108c9a8c3`. Full executor exited **0**.
`environment.json`, test logs and `pipeline-result.json` record that execution.
This is local synthetic evidence, not cloud validation. Later browser-launch
diagnostic/CI adjustments are validated by the final PR checks; they are not
retroactively attributed to this source SHA.

| Proof | Actual outcome |
| --- | --- |
| Baseline | 155/155 SHA-256 checks OK; all previous files unchanged |
| Historical suites | 276 tests OK: 12 + 36 + 49 + 118 + 10 + 44 + 7 |
| New suites | 31 tests OK in 21.714 s, no skips |
| Browser | PASS, 7.903 s, sandbox enabled, zero external page/context requests or page errors |
| Media | Three actual downloaded H.264/AAC MP4, 2.30 s, manifest hashes match |
| SRT | Each decoded frame equals an independently encoded supplied-SRT reference; differs from plain control |
| Concurrency | Two actual API processes; eight callers; 100 duplicate reservations, 20 distinct keys |
| Quota | Duplicate set: one allocation; distinct set: 10 admitted/10 quota denials, exact shared charge |
| Replays | 100 ingestion and 100 render repeats: one asset/job/outbox; three verified private downloads |
| HTTP | 478 physical attempts, 57 explicit 503 retries, zero transport-uncertain retries/exhaustions |
| Recovery | Actual ingester/renderer SIGKILL, natural lease takeover, old fence denied, orphan retained |

`independent-negative-summary.json` preserves actual per-case and workload
counts. The 401 responses were deliberate authentication-negative probes;
overload was 503, not a false logout. A lost successful reservation response
was abandoned on the actual socket and replayed without another allocation.
The SIGKILL checkpoints were **after ingestion publication** and **after the
first render object publication**, not a claim that a running codec was killed
at an arbitrary instruction. Historical native cancellation tests remain intact.

Resource sampling: 117 successful samples, zero unavailable; sampled combined
PostgreSQL clients peaked at 7, API login peaked at its global backstop of 6.
These per-consumer maxima did not all occur together. Parent/descendant summed
RSS peaked at 1,074,819,072 bytes; sampled live CPU total peaked at 7.72 s.
See `resource-summary.json`: RSS can double-count shared pages; container
PostgreSQL/Auth CPU/RSS and exited-process cumulative CPU are excluded. No
fleet capacity, SLA, sustained production load or billing estimate follows.

Teardown retained stopped owned local resources and private objects because
unused charged reservations remained. No uncertain quota was refunded and no
R0 object garbage collection ran. Dumps/configs/ready files are intentionally
absent here. Retention is private, not an exported recovery database.

The initial diagnostic HTTP client run had two BrokenPipe errors under early
503 rejection; `diagnostic-negative-initial-summary.json` preserves it. The
client was corrected to read the already-sent 503, with separate bounded
transport-uncertainty accounting. The first clone run exited 125 because the
historical fixed port 55460 was occupied; its failure record is preserved. The
new wrapper selects free local ports and exports historical-phase failures.
Old monitor samples that saw only the monitor itself are **NOT_VALIDATED**.

`SHA256SUMS.json` covers the proof files existing before this explanatory
README. MP4s and screenshots contain only generated G0 test material. No human
listening is claimed. See `docs/G3BR0_MANUAL_OBSERVATION.md` and the separate
human smoke checklist. Cloud/hosted Auth/IAM/costs remain NOT_VALIDATED/UNKNOWN.
