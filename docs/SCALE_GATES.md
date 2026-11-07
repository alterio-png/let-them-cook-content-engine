# Scale gates and acceptance metrics

## G0 - local render feasibility: PASS (limited)
- [x] Synthetic source audio/video; FFmpeg output dimensions and hashes verified for three profiles.
- [x] Captions from user-supplied SRT preserved in output video.
- [x] Duplicate key concurrent request returns running/completed without starting duplicate render; unique DB constraint verified.
- [x] Same key changed payload rejected; corrupt/missing artifact requires reconciliation.
- [x] Tenant path and symlink escapes rejected locally.
- [ ] GitHub CI of this new project (repository not created yet).

## G1 - upstream licensing and production binary: NOT STARTED
- [ ] Review FFmpeg exact distribution options, codec licenses and patent implications; supply NOTICE and SBOM.
- [ ] Pin dependency artifact SHAs, registry origin and release versions; G1-G4 approvals for any third-party source execution.
- [ ] Real file fuzz tests and resource cap demonstration.

## G2 - cloud foundation: NOT STARTED
- [ ] PostgreSQL migration and RLS tenant tests with actual Supabase auth contexts.
- [ ] Atomic job/outbox creation; 100 concurrent identical requests => one logical operation, one outbox event.
- [ ] Cloud Tasks duplicate/lost response and delayed event reconciliation.
- [ ] Leases/fencing: a stale worker cannot overwrite a newer final state.
- [ ] Real object storage checksum validation, scoped signed URLs and lifecycle deletion.
- [ ] Cloud worker memory/CPU/time isolation demonstrated and logs collected.

## G3 - closed beta: NOT STARTED
- [ ] 10 organizations, request 50 jobs simultaneously with enforced concurrency limits.
- [ ] Fault injection at every pipeline boundary: no silent loss or duplicate billed renders.
- [ ] Measure p50/p95 end-to-end time, retry rate, cost per processed input minute and per output minute.
- [ ] Performance budget per video cohort; no production assumption before measurements.
- [ ] Authenticated review app, brand assets, localization and accessible editor.

## G4 - launch: NOT AUTHORIZED
- [ ] Official platform OAuth and publication approved; real network API dry run.
- [ ] Metered plans, quotas and payments isolated from job execution.
- [ ] Restore drills, incident ownership, backup strategy, retention/delete request handling.
- [ ] Human sign-off: security, licenses, product, operations, unit economics.

## Unit economics model
Record for every job: input/output duration, vCPU-seconds, GiB-seconds, GPU-seconds, model inference tokens/minutes, storage bytes/days, inter-cloud egress, queue operations, repeated attempts, support allowance. Gross margin target >70% is a business hypothesis, not a measured figure. Benchmark GCS against R2 because Cloud Run-to-R2 traffic may incur GCP transfer charges even when R2 egress is free.
