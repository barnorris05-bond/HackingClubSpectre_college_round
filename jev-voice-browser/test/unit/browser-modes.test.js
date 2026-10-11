import { test } from "node:test";
import assert from "node:assert/strict";
import { BrowserManager, DEFAULT_PROFILE_DIR } from "../../src/browser.js";

function fakePage() {
  return {
    on() {},
    isClosed: () => false,
    url: () => "about:blank",
    goto: async () => {},
  };
}

function fakeContext(pages = null) {
  const list = pages || [fakePage()];
  return {
    addInitScript: async () => {},
    on() {},
    pages: () => list,
    newPage: async () => {
      const p = fakePage();
      list.push(p);
      return p;
    },
  };
}

function fakeCdpBrowser(ctx) {
  return { contexts: () => [ctx], newContext: async () => ctx, close: async () => {} };
}

test("explicit --cdp attaches live, leaves tabs alone", async () => {
  const bm = new BrowserManager();
  let connected = null;
  await bm.launch({ cdp: "http://127.0.0.1:9222" }, { connectCdp: async (url) => ((connected = url), fakeCdpBrowser(fakeContext())) });
  assert.equal(connected, "http://127.0.0.1:9222");
  assert.equal(bm.live, true);
  assert.equal(bm.ownsBrowser, false);
  assert.ok(bm.mode.startsWith("cdp-flag"));
});

test("probe hit → cdp-live attach, no navigation", async () => {
  const bm = new BrowserManager();
  const ctx = fakeContext();
  await bm.launch(
    { startUrl: "https://example.com/", live: true },
    {
      detectDefaultBrowser: async () => ({ engine: "chromium", channel: "chrome", name: "Chrome" }),
      probeCdp: async () => true,
      connectCdp: async () => fakeCdpBrowser(ctx),
    },
  );
  assert.ok(bm.mode.startsWith("cdp-live"));
  assert.equal(bm.live, true);
  assert.equal(bm.ownsBrowser, false);
});

test("probe miss + not running → spawn debuggable, then attach", async () => {
  const bm = new BrowserManager();
  const state = { probed: false };
  const spawns = [];
  await bm.launch(
    { live: true, browser: "auto" },
    {
      detectDefaultBrowser: async () => ({
        engine: "chromium",
        channel: "chrome",
        name: "Chrome",
        executablePath: "C:\\fake\\chrome.exe",
        realProfileDir: "C:\\L\\Google\\Chrome\\User Data",
      }),
      probeCdp: async () => state.probed,
      isProcessRunning: async () => false,
      spawner: async (exe, args) => {
        spawns.push({ exe, args });
        state.probed = true;
      },
      connectCdp: async () => fakeCdpBrowser(fakeContext()),
    },
  );
  // channel brand resolved to a real exe via well-known paths on this machine
  assert.equal(spawns.length, 1);
  assert.equal(spawns[0].exe, "C:\\fake\\chrome.exe");
  assert.ok(spawns[0].args.some((a) => a.startsWith("--remote-debugging-port=")));
  assert.ok(spawns[0].args.some((a) => a.includes("User Data")));
  assert.ok(bm.mode.startsWith("cdp-spawned"));
  assert.equal(bm.ownsBrowser, false);
});

test("running but not debuggable → loud error, never kills", async () => {
  const bm = new BrowserManager();
  await assert.rejects(
    bm.launch(
      { live: true },
      {
        detectDefaultBrowser: async () => ({ engine: "chromium", channel: "msedge", name: "Edge" }),
        probeCdp: async () => false,
        isProcessRunning: async () => true,
      },
    ),
    /without remote debugging/,
  );
});

test("Zen → skipped (Firefox protocol hangs); bundled Chromium instead", async () => {
  const bm = new BrowserManager();
  const calls = [];
  await bm.launch(
    { startUrl: "about:blank", browser: "auto" },
    {
      detectDefaultBrowser: async () => ({ engine: "firefox", name: "Zen", executablePath: "C:\\Zen\\zen.exe" }),
      launchPersistent: async (dir, opts) => ((calls.push({ dir, opts })), fakeContext()),
    },
  );
  assert.equal(calls.length, 1);
  assert.equal(calls[0].dir, DEFAULT_PROFILE_DIR);
  assert.equal(calls[0].opts.executablePath, undefined);
  assert.equal(calls[0].opts.channel, undefined);
  assert.equal(bm.mode, "bundled Chromium (dedicated profile)");
  assert.equal(bm.live, false);
  assert.equal(bm.ownsBrowser, true);
});

test("brand launch failure → bundled Chromium fallback", async () => {
  const bm = new BrowserManager();
  const calls = [];
  await bm.launch(
    { startUrl: "about:blank", browser: "auto", headless: true }, // headless skips the live-attach path
    {
      detectDefaultBrowser: async () => ({ engine: "chromium", channel: "chrome", name: "Chrome" }),
      resolveChromeProfile: async () => ({ error: "skip Ian path in this test" }),
      launchPersistent: async (dir, opts) => {
        calls.push(opts);
        if (calls.length === 1) throw new Error("nope"); // brand attempt fails…
        return fakeContext(); // …bundled retry succeeds
      },
    },
  );
  assert.equal(calls.length, 2);
  assert.equal(calls[0].channel, "chrome");
  assert.equal(calls[1].executablePath, undefined);
  assert.equal(calls[1].channel, undefined);
  assert.equal(bm.mode, "bundled Chromium (dedicated profile)");
  assert.equal(bm.ownsBrowser, true);
});

test("hung brand launch → timeout → bundled fallback", async () => {
  const bm = new BrowserManager();
  const calls = [];
  await bm.launch(
    { startUrl: "about:blank", browser: "auto", headless: true, launchTimeoutMs: 60 }, // headless skips live-attach
    {
      detectDefaultBrowser: async () => ({ engine: "chromium", channel: "chrome", name: "Chrome" }),
      resolveChromeProfile: async () => ({ error: "skip Ian path in this test" }),
      launchPersistent: async (dir, opts) => {
        calls.push(opts);
        if (calls.length === 1) return new Promise(() => {}); // hang forever…
        return fakeContext(); // …bundled retry succeeds
      },
    },
  );
  assert.equal(calls.length, 2);
  assert.equal(bm.mode, "bundled Chromium (dedicated profile)");
});

test("close() never shuts an attached browser", async () => {
  const bm = new BrowserManager();
  let closed = 0;
  const cdpBrowser = { contexts: () => [fakeContext()], close: async () => closed++ };
  await bm.launch({ cdp: "http://x:9222" }, { connectCdp: async () => cdpBrowser });
  await bm.close();
  assert.equal(closed, 0);
});

test("default is installed Chrome + Ian profile (owned, visible, real logins)", async () => {
  const bm = new BrowserManager();
  const calls = [];
  const saved = process.env.BROWSER;
  delete process.env.BROWSER;
  try {
    await bm.launch(
      { startUrl: "about:blank" },
      {
        resolveChromeProfile: async (want) => {
          assert.equal(want, "Ian");
          return { userDataDir: "C:\\U\\User Data", profileDirectory: "Default" };
        },
        isProcessRunning: async () => false,
        launchPersistent: async (dir, opts) => ((calls.push({ dir, opts })), fakeContext()),
      },
    );
  } finally {
    if (saved !== undefined) process.env.BROWSER = saved;
  }
  assert.equal(calls.length, 1);
  assert.equal(calls[0].dir, "C:\\U\\User Data");
  assert.equal(calls[0].opts.channel, "chrome");
  assert.ok(calls[0].opts.args.some((a) => a === "--profile-directory=Default"));
  assert.equal(calls[0].opts.headless, false);
  assert.ok(bm.mode.includes('profile "Ian"'));
  assert.equal(bm.live, false);
  assert.equal(bm.ownsBrowser, true);
});

test("Chrome running at startup → instructional error (never kills, never hijacks)", async () => {
  const bm = new BrowserManager();
  await assert.rejects(
    bm.launch(
      { browser: "chrome", startUrl: "about:blank" },
      {
        resolveChromeProfile: async () => ({ userDataDir: "C:\\U\\User Data", profileDirectory: "Default" }),
        isProcessRunning: async () => true,
      },
    ),
    /profile lock/,
  );
});

test("missing Chrome profile name → Chrome brand window (dedicated profile)", async () => {
  const bm = new BrowserManager();
  const calls = [];
  await bm.launch(
    { browser: "chrome", chromeProfile: "Nobody", startUrl: "about:blank" },
    {
      resolveChromeProfile: async () => ({ error: 'Chrome profile "Nobody" not found', available: ["Ian"] }),
      launchPersistent: async (dir, opts) => ((calls.push({ dir, opts })), fakeContext()),
    },
  );
  assert.equal(calls.length, 1);
  assert.equal(calls[0].dir, DEFAULT_PROFILE_DIR);
  assert.equal(calls[0].opts.channel, "chrome");
  assert.equal(bm.mode, "brand Chrome (dedicated profile)");
});
