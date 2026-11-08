// Actual local browser proof. Never records credentials, headers, HARs or traces.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright-core');
const args = process.argv.slice(2);
function argument(name) {
  const at = args.indexOf(name);
  assert(at >= 0 && args[at + 1], `Required argument: ${name}`);
  return path.resolve(args[at + 1]);
}
const readyPath = argument('--ready');
const output = argument('--output');
assert(!fs.existsSync(output), 'Use a new evidence directory');
fs.mkdirSync(output, { recursive: true, mode: 0o700 });
fs.chmodSync(output, 0o700);
const write = (name, value) => fs.writeFileSync(path.join(output, name),
  typeof value === 'string' ? value : JSON.stringify(value, null, 2) + '\n', { mode: 0o600 });
const sha256 = value => createHash('sha256').update(value).digest('hex');
const ready = JSON.parse(fs.readFileSync(readyPath, 'utf8'));
const origin = new URL(ready.url);
assert(origin.protocol === 'http:' && origin.hostname === '127.0.0.1' && origin.port &&
  !origin.username && !origin.password && !origin.search && !origin.hash, 'Only isolated loopback API allowed');
const user = ready.users?.[0];
assert(user && user.email && user.password, 'Missing isolated test account');
const org = user.organization_id ?? user.org_id;
const fixture = ready.fixtures.find(item => item.organization_id === org);
assert(fixture, 'Missing organization-scoped synthetic fixture');
for (const kind of ['video', 'caption']) {
  const bytes = fs.readFileSync(fixture[kind + '_path']);
  assert(bytes.length > 0 && bytes.length <= 1048576, 'Bounded synthetic fixture required');
  assert.equal(bytes.length, fixture[kind + '_bytes']);
  assert.equal(sha256(bytes), fixture[kind + '_sha256']);
}
assert(fixture.duration_ms > 0 && fixture.duration_ms <= 5000, 'Browser proof is limited to five synthetic seconds');

function command(executable, params, options = {}) {
  return execFileSync(executable, params, {
    timeout: 45000, maxBuffer: 16 * 1024 * 1024,
    stdio: ['ignore', 'pipe', 'pipe'], ...options,
  });
}
function executable() {
  // Prefer the installed Chrome binary over distro Chromium wrapper/stub paths.
  // An explicit operator choice is never silently replaced after a failure.
  const candidates = process.env.R0_CHROMIUM ? [process.env.R0_CHROMIUM] : [
    '/opt/google/chrome/chrome', '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser',
  ];
  for (const candidate of candidates) {
    try {
      fs.accessSync(candidate, fs.constants.X_OK);
      const version = command(candidate, ['--version'], { timeout: 5000 }).toString().trim();
      assert(/^(?:Google Chrome|Chromium)\s/.test(version), 'Executable must identify as Chrome or Chromium');
      return { path: candidate, resolved: fs.realpathSync(candidate), version: version.slice(0, 200) };
    } catch (error) {
      if (process.env.R0_CHROMIUM) throw error;
    }
  }
  throw new Error('Installed Chromium/Chrome is required; no browser download or skipped PASS');
}
function startupDiagnostic(error) {
  // Only called before login. Never emit a later request error or stack, which
  // could contain bearer tokens, request bodies or capability URLs.
  let message = String(error.message ?? error);
  const secrets = [
    ...ready.users.map(item => item.password),
    ...Object.entries(process.env).filter(([key]) => /PASSWORD|SECRET|TOKEN|CREDENTIAL|SIGNING_KEY|_DSN|DATABASE_URL/i.test(key)).map(([, value]) => value),
  ];
  for (const secret of secrets.filter(value => typeof value === 'string' && value.length >= 4).sort((a, b) => b.length - a.length)) {
    message = message.split(secret).join('[REDACTED]');
  }
  message = message.replace(/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g, '[JWT_REDACTED]')
    .replace(/postgres(?:ql)?:\/\/[^\s"'<>]+/g, '[DATABASE_URL_REDACTED]')
    .replace(/Bearer\s+[^\s"'<>]+/gi, 'Bearer [REDACTED]')
    .replace(/\/v1\/(uploads|downloads)\/[^\s"'<>]+/g, '/v1/$1/[CAPABILITY_REDACTED]')
    .replace(/\u001b\[[0-9;]*m/g, '').replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, '');
  let classifier = 'UNCLASSIFIED_STARTUP_ERROR';
  if (/no usable sandbox|sandbox[^\n]*(?:fail|error|denied|not supported)|failed to[^\n]*namespace|apparmor|operation not permitted|permission denied/i.test(message)) classifier = 'SANDBOX_OR_PERMISSION_PATTERN';
  else if (/error while loading shared libraries|missing dependencies/i.test(message)) classifier = 'SYSTEM_DEPENDENCY_PATTERN';
  else if (/enoent|executable.*(?:not found|doesn.t exist)/i.test(message)) classifier = 'EXECUTABLE_UNAVAILABLE_PATTERN';
  else if (/timeout|timed out/i.test(message)) classifier = 'STARTUP_TIMEOUT_PATTERN';
  else if (/browser.*closed|target.*closed|process.*exit|exitcode/i.test(message)) classifier = 'BROWSER_EXIT_PATTERN';
  const limit = 6144;
  const truncated = message.length > limit;
  if (truncated) message = message.slice(0, 2048) + '\n[TRUNCATED]\n' + message.slice(-(limit - 2061));
  return { classifier, message, truncated, classification_scope: 'message pattern only; root cause is not established' };
}
function sanitizedRoute(url) {
  const pathname = new URL(url).pathname;
  if (pathname.startsWith('/v1/uploads/')) return '/v1/uploads/[capability]';
  if (pathname.startsWith('/v1/downloads/')) return '/v1/downloads/[capability]';
  return pathname.replace(/[0-9a-f]{8}-[0-9a-f-]{27,}/gi, '[id]')
    .replace(/\/fixtures\/[^/]+\//, '/fixtures/[fixture]/');
}
function probe(file, name) {
  const value = JSON.parse(command('ffprobe', ['-v', 'error', '-show_streams', '-show_format', '-of', 'json', file]));
  write(name + '.ffprobe.json', value);
  return value;
}
function audioEvidence(file) {
  const pcm = command('ffmpeg', ['-nostdin', '-v', 'error', '-threads', '1', '-i', file,
    '-t', '5', '-vn', '-ac', '1', '-ar', '8000', '-f', 'f32le', 'pipe:1']);
  assert(pcm.length >= 8000, 'Expected decoded nonempty audio');
  let energy = 0;
  const count = pcm.length / 4;
  const start = Math.min(800, Math.floor(count / 10));
  const stop = count - start;
  let real = 0, imaginary = 0;
  for (let i = start; i < stop; i++) {
    const value = pcm.readFloatLE(i * 4);
    assert(Number.isFinite(value));
    energy += value * value;
    // The approved synthetic fixture contains the G0 440 Hz reference tone.
    real += value * Math.cos(2 * Math.PI * 440 * i / 8000);
    imaginary += value * Math.sin(2 * Math.PI * 440 * i / 8000);
  }
  const samples = stop - start;
  const rms = Math.sqrt(energy / samples);
  const toneRatio = 2 * (real * real + imaginary * imaginary) / (samples * energy);
  assert(rms > 0.005, 'Silent audio is not preservation');
  assert(toneRatio > 0.7, 'Synthetic 440 Hz tone must survive the render');
  return { samples, sample_rate: 8000, rms, reference_440hz_energy_ratio: toneRatio };
}
function frame(file, at, width, height) {
  const bytes = command('ffmpeg', ['-nostdin', '-v', 'error', '-threads', '1', '-i', file,
    '-ss', String(at), '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-threads', '1', 'pipe:1']);
  assert.equal(bytes.length, width * height * 3);
  return bytes;
}
function captionEvidence(file, preset, width, height, duration) {
  const caption = fs.readFileSync(fixture.caption_path, 'utf8');
  const cue = /([0-9]{2}):([0-9]{2}):([0-9]{2}),([0-9]{3}) --> ([0-9]{2}):([0-9]{2}):([0-9]{2}),([0-9]{3})/.exec(caption);
  assert(cue, 'Provided SRT must contain a timed cue');
  const seconds = at => Number(cue[at]) * 3600 + Number(cue[at + 1]) * 60 + Number(cue[at + 2]) + Number(cue[at + 3]) / 1000;
  const at = (seconds(1) + Math.min(seconds(5), duration)) / 2;
  assert(at > 0 && at < duration);
  const referenceDir = path.join(output, 'reference-' + preset);
  fs.mkdirSync(referenceDir, { mode: 0o700 });
  fs.copyFileSync(fixture.caption_path, path.join(referenceDir, 'captions.srt'));
  const filter = `scale=${width}:${height}:force_original_aspect_ratio=decrease,pad=${width}:${height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1`;
  for (const withCaption of [false, true]) {
    const destination = path.join(referenceDir, withCaption ? 'with-srt.mp4' : 'without-srt.mp4');
    command('ffmpeg', ['-nostdin', '-hide_banner', '-loglevel', 'error', '-n',
      '-protocol_whitelist', 'file,pipe', '-f', 'mov', '-enable_drefs', '0', '-use_absolute_path', '0',
      '-threads', '1', '-i', path.resolve(fixture.video_path), '-t', String(duration),
      '-map', '0:v:0', '-map', '0:a:0?', '-vf', filter + (withCaption ? ',subtitles=filename=captions.srt' : ''),
      '-filter_threads', '1', '-c:v', 'libx264', '-threads', '1', '-preset', 'veryfast', '-crf', '27',
      '-pix_fmt', 'yuv420p', '-r', '30', '-c:a', 'aac', '-b:a', '128k', '-map_metadata', '-1',
      '-movflags', '+faststart', destination], { cwd: referenceDir });
  }
  const actual = frame(file, at, width, height);
  const expected = frame(path.join(referenceDir, 'with-srt.mp4'), at, width, height);
  const control = frame(path.join(referenceDir, 'without-srt.mp4'), at, width, height);
  let error = 0, changed = 0;
  for (let offset = 0; offset < actual.length; offset += 3) {
    let difference = 0;
    for (let channel = 0; channel < 3; channel++) {
      error += Math.abs(actual[offset + channel] - expected[offset + channel]);
      difference = Math.max(difference, Math.abs(actual[offset + channel] - control[offset + channel]));
    }
    if (offset >= width * Math.floor(height / 2) * 3 && difference > 20) changed++;
  }
  const mae = error / actual.length;
  command('ffmpeg', ['-nostdin', '-v', 'error', '-threads', '1', '-i', file,
    '-ss', String(at), '-frames:v', '1', '-threads', '1', path.join(output, preset + '-caption-frame.png')]);
  assert.equal(sha256(actual), sha256(expected), 'Downloaded frame must exactly match the supplied-SRT independent render');
  assert(changed >= 20, 'Supplied SRT must visibly change the lower-half pixels');
  return { at_seconds: at, actual_rgb_sha256: sha256(actual), reference_rgb_sha256: sha256(expected),
    supplied_srt_sha256: sha256(Buffer.from(caption)), mean_absolute_error_to_supplied_srt_reference: mae,
    lower_half_changed_pixels_vs_without_srt: changed,
    scope: 'pixel evidence against an independently encoded captioned/plain reference, not OCR or aesthetic quality' };
}

const report = { gate: 'LOCAL_BROWSER_E2E', status: 'RUNNING', started_at: new Date().toISOString(),
  node: process.version, browser_dependency: require('playwright-core/package.json').version,
  remote_staging: 'NOT_VALIDATED', manual_smoke: 'NOT_EXECUTED_BY_AUTOMATED_DRIVER',
  fixture: { video_sha256: fixture.video_sha256, caption_sha256: fixture.caption_sha256,
    video_bytes: fixture.video_bytes, caption_bytes: fixture.caption_bytes, duration_ms: fixture.duration_ms },
  assertions: [], outputs: [], network: [] };
let browser, page;
let authToken;
let stage = 'startup';
const started = Date.now();
try {
  report.startup_step = 'select-executable';
  const selectedBrowser = executable();
  report.browser_binary = selectedBrowser.path;
  report.browser_binary_resolved = selectedBrowser.resolved;
  report.browser_version = selectedBrowser.version;
  report.ffmpeg_version = command('ffmpeg', ['-version']).toString().split('\n')[0];
  report.ffprobe_version = command('ffprobe', ['-version']).toString().split('\n')[0];
  try { report.git_head = command('git', ['rev-parse', 'HEAD'], { cwd: fileURLToPath(new URL('..', import.meta.url)) }).toString().trim(); } catch { report.git_head = 'UNAVAILABLE'; }
  const sandboxEnabled = process.getuid?.() !== 0;
  report.browser_os_uid = process.getuid?.() ?? null;
  report.browser_sandbox = sandboxEnabled ? 'ENABLED' : 'DISABLED_ROOT_CONTAINER';
  report.production_browser_sandbox = 'NOT_VALIDATED';
  report.startup_step = 'browser-launch';
  browser = await chromium.launch({ executablePath: selectedBrowser.path, headless: true,
    timeout: 30000, chromiumSandbox: sandboxEnabled, args: ['--disable-dev-shm-usage'] });
  report.startup_step = 'browser-context';
  const context = await browser.newContext({ acceptDownloads: true, viewport: { width: 1440, height: 1000 } });
  let externalRequests = 0, pageErrors = 0;
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== origin.origin && !['blob:', 'data:', 'about:'].includes(url.protocol)) {
      externalRequests++; await route.abort(); return;
    }
    await route.continue();
  });
  report.startup_step = 'browser-page';
  page = await context.newPage();
  report.startup_step = 'complete';
  page.setDefaultTimeout(15000);
  page.on('pageerror', () => { pageErrors++; });
  page.on('response', response => {
    if (new URL(response.url()).origin === origin.origin) report.network.push({
      method: response.request().method(), route: sanitizedRoute(response.url()), status: response.status() });
  });
  const screenshot = name => page.screenshot({ path: path.join(output, name + '.png'), fullPage: true,
    mask: [page.locator('#password')] });
  stage = 'login';
  await page.goto(origin.href, { waitUntil: 'networkidle' });
  await screenshot('01-login');
  await page.locator('#email').fill(user.email);
  await page.locator('#password').fill(user.password);
  const loginResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/local/v1/auth/login' && response.request().method() === 'POST');
  await page.locator('#login-button').click();
  const login = await loginResponse;
  assert.equal(login.status(), 200, 'Actual local GoTrue login must succeed');
  authToken = (await login.json()).access_token;
  assert.equal(typeof authToken, 'string');
  assert.equal(authToken.split('.').length, 3, 'Expected real signed GoTrue JWT');
  await page.locator('#logout-button').waitFor({ state: 'visible' });
  await page.locator('#org-select').selectOption(org);
  await page.waitForFunction(() => !document.querySelector('#video-file')?.disabled);
  report.assertions.push('Official local Auth password grant through same-origin API; signed JWT accepted');

  stage = 'upload-and-ingest';
  await page.locator('#video-file').setInputFiles(fixture.video_path);
  await page.locator('#caption-file').setInputFiles(fixture.caption_path);
  await page.locator('#upload-button').click();
  await page.waitForFunction(() => {
    const button = document.querySelector('#render-button');
    return button && !button.disabled;
  }, null, { timeout: 90000 });
  await screenshot('02-ingested');
  report.assertions.push('Browser file inputs upload the verified synthetic MP4 and supplied SRT; real asynchronous ingestion completes');

  stage = 'render-and-download';
  await page.locator('#render-button').click();
  for (const preset of ['vertical', 'square', 'feed']) {
    await page.locator('#download-' + preset).waitFor({ state: 'visible', timeout: 120000 });
  }
  await screenshot('03-completed');
  const download = async (selector, destination) => {
    const received = page.waitForEvent('download');
    await page.locator(selector).click();
    const event = await received;
    assert.equal(await event.failure(), null);
    await event.saveAs(destination);
    fs.chmodSync(destination, 0o600);
  };
  const manifestPath = path.join(output, 'manifest.json');
  await download('#manifest-download', manifestPath);
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  assert.equal(manifest.outputs.length, 3);
  const dimensions = { vertical: [270, 480], square: [320, 320], feed: [320, 400] };
  const sourceAudio = audioEvidence(fixture.video_path);
  report.source_audio = sourceAudio;
  for (const [preset, [width, height]] of Object.entries(dimensions)) {
    stage = 'verify-download-' + preset;
    const target = path.join(output, preset + '.mp4');
    await download('#download-' + preset, target);
    const bytes = fs.readFileSync(target);
    const metadata = manifest.outputs.find(item => (item.preset ?? item.name) === preset);
    assert(metadata, 'Manifest must include each preset');
    assert.equal(metadata.sha256, sha256(bytes));
    assert.equal(metadata.size_bytes, bytes.length);
    stage = 'verify-ffprobe-' + preset;
    const info = probe(target, preset);
    const video = info.streams.find(stream => stream.codec_type === 'video');
    const audio = info.streams.find(stream => stream.codec_type === 'audio');
    assert(video && audio, 'Video and preserved audio streams required');
    assert.equal(video.width, width);
    assert.equal(video.height, height);
    assert.equal(audio.codec_name, 'aac');
    const duration = Number(info.format.duration);
    assert(Math.abs(duration - fixture.duration_ms / 1000) <= 0.15);
    stage = 'verify-audio-' + preset;
    const audioProof = audioEvidence(target);
    assert(Math.abs(audioProof.rms / sourceAudio.rms - 1) < 0.2, 'Tone RMS must remain within 20 percent');
    stage = 'verify-supplied-srt-' + preset;
    const captionProof = captionEvidence(target, preset, width, height, fixture.duration_ms / 1000);
    report.outputs.push({ preset, file: preset + '.mp4', sha256: sha256(bytes), size_bytes: bytes.length,
      width, height, duration_seconds: duration, audio: audioProof, caption: captionProof });
  }
  report.assertions.push('Three browser MP4 downloads match final manifest bytes and SHA-256; ffprobe verifies dimensions/duration/AAC');
  report.assertions.push('Decoded audio retains audible synthetic 440 Hz tone; each downloaded frame matches supplied-SRT reference and differs from plain control');
  const storage = await page.evaluate(() => ({ local: Object.keys(localStorage), session: Object.keys(sessionStorage),
    tokenInMarkup: /eyJ[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/.test(document.documentElement.outerHTML) }));
  assert.deepEqual(storage.local, []);
  assert.deepEqual(storage.session, []);
  assert.equal(storage.tokenInMarkup, false);
  assert.equal(externalRequests, 0, 'No browser request may leave the isolated loopback API');
  assert.equal(pageErrors, 0, 'Browser must not raise uncaught JavaScript errors');
  stage = 'logout';
  const logoutResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/local/v1/auth/logout' && response.request().method() === 'POST');
  await page.locator('#logout-button').click();
  assert.equal((await logoutResponse).status(), 200, 'Actual Auth logout must finish');
  await page.locator('#login-button').waitFor({ state: 'visible' });
  const revoked = await fetch(new URL('/local/v1/me', origin), { headers: { Authorization: 'Bearer ' + authToken } });
  assert.equal(revoked.status, 401, 'Logged-out signed token must fail actual session verification');
  authToken = undefined;
  await screenshot('04-logged-out');
  report.assertions.push('No browser persistent token storage or external requests; actual logout revokes the previously signed session');
  report.external_requests = externalRequests;
  report.uncaught_page_errors = pageErrors;
  report.status = 'PASS';
} catch (error) {
  report.status = 'FAIL';
  report.failure = { stage, type: error.name, code: error.code ?? null };
  if (stage === 'startup') report.failure.startup_diagnostic = startupDiagnostic(error);
  if (page) {
    try { await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true, mask: [page.locator('#password')] }); } catch { /* preserve primary failure */ }
  }
  // Later request messages/stacks can contain credentials: retain only the
  // bounded, sanitized diagnostic from the pre-login startup boundary above.
  process.exitCode = 1;
} finally {
  authToken = undefined;
  if (browser) {
    try { await browser.close(); } catch {
      if (report.status === 'PASS') { report.status = 'FAIL'; report.failure = { stage: 'browser-cleanup', type: 'BrowserCloseError' }; process.exitCode = 1; }
    }
  }
  report.finished_at = new Date().toISOString();
  report.elapsed_ms = Date.now() - started;
  write('summary.json', report);
  console.log(JSON.stringify({ gate: report.gate, status: report.status, stage: report.failure?.stage ?? 'complete',
    outputs: report.outputs.length, evidence: output }));
}
