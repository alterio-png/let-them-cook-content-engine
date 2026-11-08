# User-local WebKit QA runtime

No root/system package changes. Playwright WebKit 26.5/v2336 was downloaded from official Playwright CDN using the existing installed Playwright CLI. Exact official Debian trixie packages were downloaded from the environment's configured snapshot.debian.org APT repositories to `downloads/` and extracted with dpkg-deb to `root/`.

Downloaded packages: libgtk-4-1 4.18.6+ds-2; libgraphene-1.0-0 1.10.8-5; libharfbuzz-icu0 10.2.0-1+deb13u1; libmanette-0.2-0 0.2.12-1; libhyphen0 2.8.8-7+b2; libgles2 1.7.0-1+b2; libcairo-script-interpreter2 1.18.4-1+b1; libhidapi-hidraw0 0.14.0-1+b2; libglvnd0 1.7.0-1+b2; liblzo2-2 2.10-3+b1.

Use:

```js
const {webkit, devices} = require('playwright');
const browser = await webkit.launch({
  headless: true,
  executablePath: '/workspace/scratch/webkit-libs/webkit-local.sh'
});
const context = await browser.newContext(devices['iPhone 13']);
```

The wrapper executes the original bundle's MiniBrowser binary, with its standard bundle variables plus the user-local library path. The original bundle wrapper overwrites LD_LIBRARY_PATH, so setting only the outer environment is insufficient. The original host check also checks libGLES2 via system ldconfig and cannot see user-local libs. Providing the explicit executable wrapper lets the loader use them without touching system configuration.

Actual launch, navigation, screenshots and script execution passed against the old public preview. This is desktop Linux WebKit emulating an iPhone viewport, not a physical device and not Apple's distributed Safari binary. It does not establish iOS hardware codec/download support.
