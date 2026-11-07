# Security and privacy gates

- Media from customers is sensitive. Isolate organizations at authentication, API authorization, PostgreSQL RLS, object namespace and signed upload/download URLs.
- The local SQLite demo only tests relative filesystem boundaries; it is not proof of cloud tenant isolation or a secure API.
- FFmpeg is a complex native parser. Treat every video as untrusted. Production requires non-root immutable container, seccomp/isolation, CPU/memory/pixel/time caps, minimal filesystem, ephemeral scratch space and dependency patching.
- The bundled example container is not approved: it uses a broad OS package and no immutable base digest, and the local ffmpeg is GPL-enabled.
- Require validated MIME+magic bytes and probes, maximum total bytes, max track/frame/pixel duration, and quota limits before heavy decoding. Never interpolate user strings into shell or filter graphs.
- Media ingestion should accept authorized user-owned uploads only. No default video scraping or publication of third-party content.
- SRT subtitle mode uses safe basename and child-process argv (not shell); production still requires complete encoding and pathological-subtitle fuzz tests.
- Encrypt credentials with platform-managed KMS or service secret manager. No tokens in chat, repo, logs or browser local storage.
- Social OAuth grants belong to each tenant and network; operate with minimum scopes. Publication only after explicit approval and re-check, plus loss-of-response reconciliation.
- EU/US GDPR data handling, DPA, data residency and retention/deletion requests require policy before real uploads.
- Record structured audit log for render submission, retries, approvals, bills and deletion events; redact personal identifiers in telemetry.
- Abuse protection: file type/size quotas, per-account limits, capacity reservation, malware scan if required, alerting for cost spikes.
- Security tests required before launch: auth bypass, horizontal tenant escalation, mass concurrency, interrupted upload, lost result, duplicated queue delivery, stale worker completion, deleted object, SSRF and media decoder fuzzing.

Current local spike is **not** network-accessible and uses only synthetic test media. SQL draft in `sql/` has not been applied, tested or reviewed for complete role permissions.
