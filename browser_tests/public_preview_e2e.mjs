// Static preview QA: real synthetic artifacts; no remote backend or credentials.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium, webkit } from 'playwright-core';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const dist = path.join(root, 'public_preview/dist');
const evidence = path.resolve(process.env.PREVIEW_QA_OUTPUT || '/tmp/ltc-preview-qa');
fs.mkdirSync(evidence, {recursive: true});
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
const manifest = JSON.parse(fs.readFileSync(path.join(dist, 'media/manifest.json')));
const report = {scope: 'static synthetic preview only; no physical iPhone acceptance', engines: [], tests: [], harnessDiagnostics: []};
const headers = Object.fromEntries(fs.readFileSync(path.join(dist, '_headers'), 'utf8').split('\n')
  .filter(line => /^\s+[^:]+:/.test(line)).map(line => {
    const at = line.indexOf(':'); return [line.slice(0, at).trim(), line.slice(at + 1).trim()];
  }));
assert.match(headers['Content-Security-Policy'], /connect-src 'self'/);
const mime = {'.html':'text/html; charset=utf-8','.js':'application/javascript','.css':'text/css',
  '.json':'application/json','.mp4':'video/mp4','.jpg':'image/jpeg','.svg':'image/svg+xml','.txt':'text/plain'};
const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1');
  const relative = decodeURIComponent(url.pathname) === '/' ? 'index.html' : decodeURIComponent(url.pathname).slice(1);
  const file = path.resolve(dist, relative);
  if (!file.startsWith(dist + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
    res.writeHead(404, headers); res.end('Not found'); return;
  }
  const data = fs.readFileSync(file);
  const responseHeaders = {...headers, 'Content-Type':mime[path.extname(file)] || 'application/octet-stream',
    'Accept-Ranges':'bytes'};
  const range = /^bytes=(\d+)-(\d*)$/.exec(req.headers.range || '');
  if (range) {
    const start = Number(range[1]); const end = Math.min(Number(range[2] || data.length - 1), data.length - 1);
    if (start >= data.length || start > end) {res.writeHead(416, responseHeaders);res.end();return;}
    res.writeHead(206, {...responseHeaders, 'Content-Length':end-start+1,'Content-Range':`bytes ${start}-${end}/${data.length}`});
    res.end(data.subarray(start,end+1));
  } else {res.writeHead(200, {...responseHeaders,'Content-Length':data.length});res.end(data);}
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const engines = process.env.PREVIEW_QA_ENGINES ? process.env.PREVIEW_QA_ENGINES.split(',')
  : process.env.PREVIEW_QA_WEBKIT === '1' ? ['chromium', 'webkit'] : ['chromium'];
assert(engines.length && engines.every(engine=>['chromium','webkit'].includes(engine)));
const browsers = [];
function record(engine, name, detail = {}) { report.tests.push({engine, name, result:'PASS', ...detail}); }
async function visible(page) {
  assert(await page.locator('h1').isVisible());
  assert(!/Loading workspace/.test(await page.locator('body').innerText()));
  assert(await page.locator('#download-link').isVisible());
}
async function noOverflow(page) {
  const sizes = await page.evaluate(() => ({viewport:innerWidth, width:document.documentElement.scrollWidth}));
  assert(sizes.width <= sizes.viewport + 1, JSON.stringify(sizes));
}
async function waitState(page, state) {
  await page.waitForFunction(value => document.querySelector('#integrity-status').dataset.state === value, state, {timeout:12000});
}
async function rawLink(page) {
  assert(!((await page.locator('#download-link').getAttribute('href')) || '').startsWith('blob:'));
  assert.equal(await page.locator('#download-link').getAttribute('download'), null);
}

try {
  for (const engine of engines) {
    const browser = engine === 'chromium'
      ? await chromium.launch({headless:true, executablePath:process.env.BROWSER_PATH || '/usr/bin/chromium',chromiumSandbox:true})
      : await webkit.launch({headless:true, ...(process.env.WEBKIT_PATH ? {executablePath:process.env.WEBKIT_PATH} : {})});
    browsers.push(browser);
    report.engines.push({name:engine, version:browser.version(),...(engine==='chromium'?{chromiumSandbox:true}: {})});
    const context = await browser.newContext({viewport:{width:390,height:844}, isMobile:true, hasTouch:true, acceptDownloads:true});
    const page = await context.newPage();
    page.setDefaultTimeout(8000);
    const errors = [], external = [], unsafe = [];
    let phase = 'initial navigation';
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => {if(message.type()==='error') errors.push({message:message.text(),location:message.location(),phase});});
    context.on('request', request => {
      if (!request.url().startsWith(origin) && !request.url().startsWith('blob:')) external.push(request.url());
      if (!['GET','HEAD'].includes(request.method())) unsafe.push({method:request.method(),url:request.url()});
    });
    await page.goto(origin, {waitUntil:'networkidle'});
    await visible(page);
    await noOverflow(page);
    phase='mobile screenshot';
    await page.screenshot({path:path.join(evidence, `${engine}-mobile.png`), fullPage:true,caret:'initial'});
    phase='playback and interactions';
    await page.waitForFunction(()=>{const video=document.querySelector('#output-video');return video.readyState>0 || video.error;});
    const metadata = await page.locator('#output-video').evaluate(video => {
      if(video.error)throw new Error('video metadata failed');
      return {width:video.videoWidth,height:video.videoHeight,duration:video.duration};
    });
    assert.equal(metadata.width,270); assert.equal(metadata.height,480);
    assert(Math.abs(metadata.duration-2.3)<0.05);
    await page.locator('#output-video').scrollIntoViewIfNeeded();
    await page.locator('#output-video').evaluate(video => {video.muted=true;return video.play();});
    await page.waitForFunction(() => document.querySelector('#output-video').currentTime > 0.15);
    await page.locator('#output-video').evaluate(video => video.pause());
    record(engine,'actual MP4 metadata and playback',metadata);

    for (const output of manifest.outputs) {
      await page.locator(`[data-format=${output.preset}]`).click();
      assert.equal(await page.locator(`[data-format=${output.preset}]`).getAttribute('aria-pressed'),'true');
      await page.waitForFunction(expected => {
        const video = document.querySelector('#output-video');
        return video.readyState>=2 && video.videoWidth===expected.width && video.videoHeight===expected.height;
      }, output);
      await page.locator('#output-video').scrollIntoViewIfNeeded();
      await page.locator('#output-video').evaluate(video=>{video.muted=true;return video.play();});
      await page.waitForFunction(()=>document.querySelector('#output-video').currentTime>0.15);
      await page.locator('#output-video').evaluate(video=>video.pause());
      record(engine,`actual ${output.preset} MP4 playback`,{width:output.width,height:output.height});
      await page.locator('#verify-button').click();
      await waitState(page,'verified');
      assert((await page.locator('#download-link').getAttribute('href')).startsWith('blob:'));
      const downloadPromise = page.waitForEvent('download');
      await page.locator('#download-link').click();
      const download = await downloadPromise;
      const bytes = fs.readFileSync(await download.path());
      assert.equal(bytes.length,output.size_bytes);
      assert.equal(sha256(bytes),output.sha256);
      assert.equal(sha256(bytes),sha256(fs.readFileSync(path.join(root,'evidence/g3br0/2026-11-08/browser',output.preset+'.mp4'))));
      record(engine,`verified downloaded ${output.preset}`,{bytes:bytes.length,sha256:sha256(bytes)});
    }
    for (let i=0;i<3;i++) await page.locator('#walkthrough-next').click();
    assert(await page.locator('#walkthrough-next').isDisabled());
    assert.equal(await page.locator('#pipeline-steps [aria-current=step]').getAttribute('data-step'),'3');
    await page.locator('#walkthrough-reset').click();
    assert.equal(await page.locator('#pipeline-steps [aria-current=step]').getAttribute('data-step'),'0');
    record(engine,'walkthrough is explanatory and resettable');
    for (const viewport of [{width:320,height:568},{width:844,height:390},{width:1440,height:1000}]) {
      await page.setViewportSize(viewport); await noOverflow(page);
      if(viewport.width===1440) {phase='desktop screenshot';await page.screenshot({path:path.join(evidence,`${engine}-desktop.png`),fullPage:true,caret:'initial'});phase='resize';}
      record(engine,`no horizontal overflow ${viewport.width}x${viewport.height}`);
    }
    assert.equal(await page.locator('footer a[href="./release.json"]').getAttribute('target'),'_blank');
    const releaseResponse=await context.request.get(origin+'/release.json');
    assert(releaseResponse.ok());assert.equal((await releaseResponse.json()).backend_connected,false);
    // Use a second application document: native JSON/text viewers inject their own
    // inline stylesheet under strict CSP and are outside this application's HTML.
    phase='back navigation';
    await page.goto(origin+'/?qa=history'); await page.goBack({waitUntil:'networkidle'});
    await visible(page);
    await page.locator('[data-format=square]').click();
    await page.locator('#verify-button').click(); await waitState(page,'verified');
    record(engine,'back navigation remains interactive');
    // Playwright's WebKit screenshot helper injects a stylesheet that strict CSP rejects.
    // Keep that exact, bounded harness event in evidence; never suppress application errors.
    const screenshotWarning="Refused to apply a stylesheet because its hash, its nonce, or 'unsafe-inline' does not appear in the style-src directive of the Content Security Policy.";
    const screenshotEvents=errors.filter(error=>engine==='webkit' && error.message===screenshotWarning &&
      ['mobile screenshot','desktop screenshot'].includes(error.phase) && error.location?.url===origin+'/');
    assert(screenshotEvents.length<=2 && new Set(screenshotEvents.map(e=>e.phase)).size===screenshotEvents.length);
    report.harnessDiagnostics.push(...screenshotEvents.map(event=>({engine,...event,
      classification:'Playwright screenshot helper inline style blocked by unchanged strict CSP'})));
    assert.deepEqual(errors.filter(error=>!screenshotEvents.includes(error)),[]);
    assert.deepEqual(external,[]);assert.deepEqual(unsafe,[]);
    record(engine,'clean same-origin-only GET requests; zero application JS/console errors');
    await context.close();

    const noJS = await browser.newContext({javaScriptEnabled:false, viewport:{width:390,height:844}});
    const staticPage = await noJS.newPage(); await staticPage.goto(origin); await visible(staticPage); await noOverflow(staticPage);
    await staticPage.locator('.all-files summary').click();
    for(const preset of ['vertical','square','feed']) assert(await staticPage.locator(`.all-files a[href="./media/${preset}.mp4"]`).isVisible());
    await noJS.close(); record(engine,'JavaScript disabled leaves useful HTML and three raw MP4 links');

    const blocked = await browser.newContext({viewport:{width:390,height:844}});
    await blocked.route('**/*.js',route=>route.abort());
    const blockedPage=await blocked.newPage();await blockedPage.goto(origin);await visible(blockedPage);await noOverflow(blockedPage);
    await blocked.close();record(engine,'blocked bootstrap script never hides initial content');

    const deniedStorage=await browser.newContext();
    await deniedStorage.addInitScript(()=>{for(const name of ['localStorage','sessionStorage'])Object.defineProperty(window,name,{get(){throw new Error('storage denied for QA');}});});
    const storagePage=await deniedStorage.newPage();await storagePage.goto(origin);await visible(storagePage);
    await storagePage.locator('[data-format=feed]').click();await storagePage.locator('#verify-button').click();await waitState(storagePage,'verified');
    await deniedStorage.close();record(engine,'blocked storage does not prevent selection or verification');

    const failureContext=await browser.newContext({viewport:{width:390,height:844}});
    const failurePage=await failureContext.newPage();await failurePage.goto(origin);
    const corrupted=Buffer.from(fs.readFileSync(path.join(dist,'media/vertical.mp4')));corrupted[corrupted.length-1]^=1;
    await failurePage.route('**/media/vertical.mp4',route=>route.request().resourceType()==='fetch'
      ?route.fulfill({status:200,contentType:'video/mp4',body:corrupted}):route.continue());
    await failurePage.locator('#verify-button').click();await waitState(failurePage,'error');await rawLink(failurePage);
    assert.match(await failurePage.locator('#integrity-status').innerText(),/não corresponde/);
    await failurePage.unroute('**/media/vertical.mp4');
    await failurePage.locator('#verify-button').click();await waitState(failurePage,'verified');
    record(engine,'same-size media corruption blocks verified download; retry recovers');

    await failurePage.route('**/media/manifest.json',route=>route.fulfill({status:404,body:'missing'}));
    await failurePage.locator('#verify-button').click();await waitState(failurePage,'error');await rawLink(failurePage);
    assert(!(await failurePage.locator('#verify-button').isDisabled()));
    await failurePage.unroute('**/media/manifest.json');
    await failurePage.locator('#verify-button').click();await waitState(failurePage,'verified');
    record(engine,'manifest 404 clears previous verification and supports retry');

    let releasePending;
    const pendingGate = new Promise(resolve=>{releasePending=resolve;});
    await failurePage.route('**/media/manifest.json',async route=>{await pendingGate;try{await route.continue();}catch{/* cancelled by bounded fetch */}});
    const started=Date.now();await failurePage.locator('#verify-button').click();await waitState(failurePage,'error');
    assert(Date.now()-started>=7500 && Date.now()-started<12000);
    assert.match(await failurePage.locator('#integrity-status').innerText(),/8 segundos/);await rawLink(failurePage);
    releasePending();await failurePage.unroute('**/media/manifest.json');
    await failurePage.locator('#verify-button').click();await waitState(failurePage,'verified');
    record(engine,'hung manifest request times out at 8 seconds and retry recovers');

    let releaseSwitch;
    const switchGate=new Promise(resolve=>{releaseSwitch=resolve;});
    await failurePage.route('**/media/manifest.json',async route=>{await switchGate;try{await route.continue();}catch{/* cancellation expected */}});
    await failurePage.locator('#verify-button').click();await waitState(failurePage,'pending');
    await failurePage.locator('[data-format=square]').click();await rawLink(failurePage);
    assert.equal(await failurePage.locator('#integrity-status').getAttribute('data-state'),'idle');
    assert(!(await failurePage.locator('#verify-button').isDisabled()));
    releaseSwitch();await failurePage.unroute('**/media/manifest.json');
    await failurePage.locator('#verify-button').click();await waitState(failurePage,'verified');
    record(engine,'format switch cancels pending verification without stale verified state');
    await failureContext.close();
  }
  report.result='PASS';
} catch(error) {
  report.result='FAIL';report.error={message:error.message,stack:error.stack};
  throw error;
} finally {
  fs.writeFileSync(path.join(evidence,'summary.json'),JSON.stringify(report,null,2)+'\n');
  for(const browser of browsers)await browser.close();
  await new Promise(resolve=>server.close(resolve));
  console.log(`${report.result}: ${report.tests.length} checks; evidence ${evidence}`);
}
