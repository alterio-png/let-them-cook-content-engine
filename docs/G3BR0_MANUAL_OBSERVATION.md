# Separate agent-operated browser exploration

Execution: 2026-11-08, approximately **08:33:30–08:35 UTC**. Operator: Codex,
independent of the automated E2E agent. Actual Chromium 156.0.8078.79, sandbox
enabled, PostgreSQL 16.15, official GoTrue 2.197.0 and FFmpeg/FFprobe 7.1.5.
Environment: disposable `g3br0-runtime-live-02`; diagnostic source corresponding
to the initial implementation, before the PostgreSQL monitor correction. This
record makes no claim about that old sampler's connection counts.

Method: individually selected browser actions through a live Node/Playwright
REPL, interleaved with screenshot inspection and direct observation of actual
page state. This was a separate exploratory walkthrough, **not a human using
a mouse/audio device**, and not a rerun disguised as a human manual test.

Observed sequence:

1. Inspected the actual login screen and its loopback/synthetic scope.
2. Entered a disposable seeded account; the organization selector appeared.
3. Used **Usar amostra sintética**. Actual fixture descriptions showed MP4
   493 KiB and SRT 61 bytes (the UI rounds the latter to 1 KiB).
4. Selected **Enviar e validar**. The actual state became **Asset validado e
   com proveniência** and enabled Render.
5. Selected **Renderizar 3 vídeos**. Observed queued/running/completed entries
   in the activity log, then **3/3 arquivos recebidos com SHA-256 correto**.
6. Inspected the completed page and actual preview frame, including the
   supplied caption text. All three HTMLVideoElements reached readyState 4,
   with native dimensions 270×480, 320×320 and 320×400 and duration 2.3 s.
   Starting actual browser video playback succeeded.
7. Used each of the three download controls and the manifest control. Actual
   browser download events saved MP4 bytes and JSON. Independently ran FFprobe
   and SHA-256: all three files contained H.264 + AAC and matched the manifest.
8. Selected **Sair** and observed the login form again. Actual server-side
   revocation of the captured signed session is independently covered by the
   automated browser and negative GoTrue suites.

| Download | Bytes | SHA-256 |
| --- | ---: | --- |
| 9:16 | 60,531 | `54f3dfc95a8ff255993b8f94ccddff3c2f3ee74a4646bf5cedafd5da9879bfcf` |
| 1:1 | 65,956 | `471d81f1c9adce91574a26f6023af30fd4984808c736e2152bc5a941408b81e0` |
| 4:5 | 65,998 | `4b8c57965fd9fddce2d405f5313864765878eea51cc818daa5ccea4882ac5bf5` |

The final clean-clone E2E artifacts supply reproducible probes, frame/audio
comparisons and screenshots. Diagnostic screenshots and downloads are retained
outside the checkout under `/workspace/g3br0-manual-*`. No password, JWT or
privileged connection is included in this document or screenshots.

**Human-operated manual smoke and human listening remain NOT_EXECUTED.** The
automated decoded-audio/reference-tone proof does not imply a person listened.
The checklist in `browser_tests/manual_smoke.md` remains available for the
maintainer's first local run. Neither execution proves remote hosting, cloud
capacity, production codec/legal clearance or a customer-ready application.
