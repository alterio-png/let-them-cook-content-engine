# Candidate GitHub repositories | current inspection 2026-11-07

Source: direct metadata and README retrieval from GitHub via connected API. The SHAs below are **observed upstream HEADs**, not commits incorporated into this project. Commercial use approval requires the entire dependency graph, codec/build features, model weights, samples, patents, data sharing and notices to be reviewed, not just the repository's SPDX label.

| Candidate | Observed HEAD SHA | Root license indicator | G0 role | Gate |
|---|---|---|---|---|
| [FFmpeg/FFmpeg](https://github.com/FFmpeg/FFmpeg) | `ea624d1e8d4712995f5652bfc12579f9f7b294d1` | GitHub NOASSERTION; README mainly LGPL, GPL optional | Codec, scale, padding, encode | CORE, **distribution licensing pending** |
| [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) | `846467d436117e44d54ad74f555d85c75834edb0` | MIT | Local transcription candidate | RESEARCHING, models/deps/GPU review pending |
| [Anil-matcha/AI-Youtube-Shorts-Generator](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator) | `13451d439b71643bd3e9df091e0f3ca786610dc9` | MIT | Architecture reference for clip ranking | REFERENCE ONLY, API mode defaults to MuAPI; local still uses external LLM unless configured; no adoption |
| [motion-canvas/motion-canvas](https://github.com/motion-canvas/motion-canvas) | `7b91435c301d530351dcf5ebb91dd139c002e405` | MIT | Template animation candidate | RESEARCHING, performance/build review pending |
| [gitroomhq/postiz-app](https://github.com/gitroomhq/postiz-app) | `5a131eb01d91eeeb53c76ba6f76a068e89f8ee18` | AGPL-3.0 | Scheduling design/reference, possible external API | DO NOT EMBED in commercial proprietary product without legal review |
| [modelscope/FunClip](https://github.com/modelscope/FunClip) | `cdcb53fe3d4b37365178ae4f779296e72b7beb6a` | MIT | Caption + clip reference | RESEARCHING, dependencies/models not audited |

## Significant findings
- This G0 prototype calls the **existing OS FFmpeg binary**. The environment binary has `--enable-gpl` and `--enable-libx264`, so shipping this binary in a commercial container requires a separate license analysis and proper notices. GitHub's `NOASSERTION` is not a grant of unrestricted rights.
- `faster-whisper` documents PyAV-bundled FFmpeg, CTranslate2, CPU/GPU dependencies and model downloads. Evaluate separately from the repository's code license.
- AI YouTube Shorts Generator's README explicitly says API mode uses MuAPI by default, whereas local mode uses yt-dlp/faster-whisper/FFmpeg, with an OpenAI or Gemini LLM for ranking. Its README claims are not independent performance guarantees, and source media rights still matter.
- Motion Canvas focuses on code-authored 2D animation; it is NOT a replacement for the complete media transcoding/AI pipeline.
- Postiz is AGPL-3.0. A compatible external API service might reduce development cost, but it requires commercial, privacy, rate-limit and integration review. No Postiz code or service is used now.
- Treat repo stars, descriptions and recent commits as discovery signals only. No security audit, reproducible build, vulnerability SBOM or license compliance sign-off has been performed.

## Next G1 review contract
For each adopted dependency: pin version/commit + artifact SHA; inspect authoritative LICENSE/NOTICE files, transitive dependencies, codecs, models, generated assets, external network calls and known advisories; run isolated tests; assign maintenance owner; document rollback and alternative.
