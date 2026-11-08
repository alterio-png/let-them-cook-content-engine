# Old preview bootstrap investigation — 2026-11-08 UTC

Target: https://letthemcook-ux-preview.netlify.app/
Deployed entry references `/assets/index-CQpW72G9.js` (183,797 bytes) and `/assets/index-Dm8eSrAx.css`.

## Observed

- Served HTML contains only `LetThemCook / Loading workspace...` inside `#app`. It has no visible recovery action or static product content.
- Published JavaScript is an older Creator Intelligence/Auth-only UX: creators, castboards, campaigns, invitation login. It is not the PR #16 Content Engine frontend.
- Bootstrap `gn()` registers auth events and awaits `I.auth.getSession()` before initial render `S()`. There is a catch for rejected initialization, but no page-level timeout for pending initialization.
- The Auth client enables `persistSession`, `autoRefreshToken`, and `detectSessionInUrl` against an existing remote Supabase host. No login, account creation, real credentials, mutation, or direct remote Auth probe was attempted in this audit.
- Clean Chromium 144 using an emulated iPhone 13 viewport renders the login screen within five seconds. Four same-origin resources, zero JS errors, zero failed requests, zero Auth request. This does not reproduce the reported phone incident.
- Fault injection A: block hashed module load. After six seconds the page still shows only `Loading workspace...`, and the console records `net::ERR_FAILED`. The HTML has no graceful degraded view.
- Fault injection B: put an obviously synthetic expired session in the isolated test context, intercept the matching Auth refresh request, and hold it locally (never sent upstream). After six seconds the page still shows only `Loading workspace...`; no JS error is emitted and no recovery action appears.

## Conclusion and limits

The failure mode is reproduced: initial content is coupled to module availability and potentially pending Auth initialization, without a useful static fallback or bounded page-level bootstrap. The exact cause on the user's physical iPhone is **not established**. Device storage/session, network, module loading and WebKit differences remain possible triggers. Do not label the phone issue as a confirmed Supabase outage or Safari defect.

## Prevention for replacement Content Engine preview

1. Render useful HTML (product shell and truthful capabilities) before JavaScript.
2. A public synthetic demo must not initialize remote Auth or wait for a backend it does not use.
3. Enhancement failures must leave navigable content and an explicit recovery link. Use bounded waits for optional media/metadata and show errors locally.
4. Test JS disabled, script/network failure, blocked storage, mobile narrow widths and touch controls.
5. Serve HTML with revalidation and use content hashes or versioning for JS/CSS; expose reviewed source SHA in the preview.
6. Keep public demo media synthetic and same-origin. Label playback/download as recorded artifacts, not an online FFmpeg service.

## Browser coverage

Chromium emulation is evidence for responsive layout and behavior, not physical iPhone acceptance. Initially, official Playwright WebKit 26.5/v2336 could not launch: missing `libgtk-4.so.1`, `libgraphene-1.0.so.0`, `libharfbuzz-icu.so.0`, `libmanette-0.2.so.0`, `libhyphen.so.0`, and `libGLESv2.so.2`. Neither `sudo` nor `cloud-agent-packages` exists. The initial launch blocker was subsequently resolved as documented below. No system configuration changes were made. A physical iPhone smoke remains pending.

## Evidence

`chromium.json`, `chromium.png`, `webkit.json`, `blocked_module.json/png`, `pending_auth.json/png`; reproducible Playwright scripts `diagnose.cjs`, `faults.cjs`. Session data in the fault test is synthetic only. Logs contain resource URLs, not headers, tokens or personal data.

### WebKit blocker resolved with user-local test dependencies

Subsequent authorized investigation downloaded the exact official Debian packages into `/workspace/scratch/webkit-libs/`, extracted them under the current user's directory, and launched the original WebKit binary through a wrapper that adds those local libraries. No sudo or system changes. See [WebKit test runtime](../evidence/public-preview/2026-11-08/old-preview/WEBKIT_RUNTIME.md) for versions and launch invocation.

WebKit 26.5/v2336 with iPhone 13 emulation now renders the old login successfully, zero page errors and zero failed network requests. It emits one old CSP warning about a refused inline stylesheet. Evidence `webkit.json/png` now records this successful load. Physical iPhone acceptance remains pending.
