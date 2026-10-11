/**
 * Browser management: drive the user's own browser when possible, else fall back
 * to Playwright's bundled Chromium — always with a persistent profile.
 *
 * Modes (in order):
 *   1. explicit --cdp url            → attach to that debugging endpoint
 *   2. live Chromium on 127.0.0.1:9222 → attach (Chrome/Edge started with --remote-debugging-port)
 *   3. Chromium not running            → spawn it debuggable with the real profile, then attach
 *   4. Chromium running, not debuggable → abort with relaunch instructions (never kill it)
 *   5. Firefox-family (Zen/Firefox)    → launch the brand via executablePath (dedicated profile)
 *   6. anything else                   → bundled Chromium (dedicated profile)
 *
 * Attached-live sessions NEVER auto-navigate: the user's tabs are left alone.
 */
import path from "node:path";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";
import { chromium } from "playwright";
import { installOverlay } from "./overlay.js";
import { collectElementsInPage, buildSnapshot } from "./snapshot.js";
import {
  detectDefaultBrowser,
  resolveBrowserOption,
  resolveChromeProfile,
  candidatesFor,
  probeCdp,
  isProcessRunning,
  processNameFor,
} from "./browser-detect.js";

/** Resolve a Playwright channel to an exe path via well-known install locations. */
function channelExe(channel) {
  const name = channel === "chrome" ? "Chrome" : channel === "msedge" ? "Edge" : null;
  if (!name) return null;
  return candidatesFor(name, process.env).find((p) => {
    try {
      return fs.existsSync(p);
    } catch {
      return false;
    }
  });
}

/** Reject if a launch hangs (a hang bypasses try/catch fallback otherwise). */
function withTimeout(promise, ms, label) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${label} timed out after ${Math.round(ms / 1000)}s`)), ms);
  });
  return Promise.race([promise.finally(() => clearTimeout(timer)), timeout]);
}

/** Spawn a browser detached (survives us); resolves once spawned, not once ready. */
function spawnDetached(target, args, { executablePath } = {}) {
  return new Promise((resolve, reject) => {
    const exe = executablePath || target;
    const child = spawn(exe, args, { detached: true, stdio: "ignore", windowsHide: true });
    child.on("error", reject);
    // Give the OS a beat to fail fast (bad path), then hand off; readiness is probed by the caller.
    setTimeout(() => {
      child.unref();
      resolve();
    }, 400);
  });
}

async function waitFor(fn, { timeout = 10000, every = 250 } = {}) {
  const t0 = Date.now();
  for (;;) {
    try {
      if (await fn()) return true;
    } catch {
      // keep polling
    }
    if (Date.now() - t0 >= timeout) return false;
    await new Promise((r) => setTimeout(r, every));
  }
}

const __dirname = path.dirname(fileURLToPath(import.meta.url));
export const DEFAULT_PROFILE_DIR = path.join(__dirname, "..", ".browser-profile");

export class BrowserManager {
  constructor() {
    this.context = null;
    this.browser = null;
    this.active = null;
    this.pages = [];
    this.listeners = new Set();
    this.mode = "unlaunched"; // human-readable launch mode for startup logs
    this.live = false; // true when attached to the user's live session (tabs are theirs)
    this.ownsBrowser = true; // false on CDP attach: close() must NOT shut the user's browser
  }

  onChange(fn) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }
  _emit() {
    for (const fn of this.listeners) fn(this);
  }

  /**
   * @param {object} opts
   * @param {string} opts.browser "chrome" (default, installed Chrome + your profile) or edge|zen|firefox|chromium|auto. BROWSER env also honored.
   * @param {string} opts.chromeProfile Chrome profile NAME to drive (default "Ian"). CHROME_PROFILE env also honored.
   * @param {boolean} opts.live take over the live session via CDP instead of opening an owned window. --live flag.
   * @param {number} opts.cdpPort local CDP probe/spawn port (default 9222). CDP_PORT env also honored.
   */
  async launch(
    { headless = false, cdp = null, browser = null, chromeProfile = null, live = false, cdpPort = null, launchTimeoutMs = null, profileDir = DEFAULT_PROFILE_DIR, startUrl = "about:blank" } = {},
    deps = {},
  ) {
    const want = String(browser ?? process.env.BROWSER ?? "chrome").toLowerCase();
    const profileWant = chromeProfile ?? process.env.CHROME_PROFILE ?? "Ian";
    const liveWant = Boolean(live || process.env.VB_LIVE) || Boolean(cdp);
    const port = Number(cdpPort ?? process.env.CDP_PORT) || 9222;
    const detect = deps.detectDefaultBrowser || detectDefaultBrowser;
    const probe = deps.probeCdp || probeCdp;
    const running = deps.isProcessRunning || isProcessRunning;
    const resolveProfile = deps.resolveChromeProfile || resolveChromeProfile;
    const launchPersistent = deps.launchPersistent || null; // test hook
    const connectCdp = deps.connectCdp || ((url) => chromium.connectOverCDP(url));
    const spawner = deps.spawner || spawnDetached;

    if (cdp) {
      this.browser = await connectCdp(cdp);
      this.context = this.browser.contexts()[0] || (await this.browser.newContext());
      this.mode = `cdp-flag ${cdp}`;
      this.live = true;
      this.ownsBrowser = false;
      return this._setup({ startUrl: null });
    }

    // Resolve which browser brand to use.
    let sel = null;
    const explicit = want === "auto" ? null : (deps.resolveBrowserOption || resolveBrowserOption)(want);
    if (explicit) {
      if (explicit.unknownOption) {
        console.warn(`Unknown --browser "${want}", falling back to bundled Chromium.`);
        sel = { engine: "chromium", name: "Chromium (bundled)" };
      } else if (explicit.missingExe) {
        console.warn(`${explicit.missingExe} not found, falling back to bundled Chromium.`);
        sel = { engine: "chromium", name: "Chromium (bundled)" };
      } else {
        sel = explicit;
      }
    } else {
      sel = await detect();
      console.log(`Default browser detected: ${sel.name} (via ${sel.source})`);
    }

    // Live-attach path: only with --live/--cdp. Chromium engines only, never
    // headless (CI owns its window). Default is an owned window (below).
    if (liveWant && sel.engine === "chromium" && !headless) {
      if (await probe(port)) {
        this.browser = await connectCdp(`http://127.0.0.1:${port}`);
        this.context = this.browser.contexts()[0] || (await this.browser.newContext());
        this.mode = `cdp-live 127.0.0.1:${port} (${sel.name})`;
        this.live = true;
        this.ownsBrowser = false;
        console.warn("Attached to your live browser session — voice commands act on your real tabs.");
        return this._setup({ startUrl: null });
      }
      const exeName = processNameFor(sel);
      const exePath = sel.executablePath || channelExe(sel.channel);
      const alreadyRunning = exeName ? await running(exeName) : false;
      if (!alreadyRunning && exePath) {
        // Spawn the user's browser debuggable with its REAL profile so the
        // session (tabs, logins) is theirs. Only possible while it is closed:
        // a running instance would ignore the debugging flag (single-instance lock).
        const userDataDir = sel.realProfileDir || profileDir;
        const args = [`--remote-debugging-port=${port}`, `--user-data-dir=${userDataDir}`];
        console.log(`Starting ${sel.name} with remote debugging on 127.0.0.1:${port}…`);
        await spawner(exePath, args, { executablePath: exePath });
        if (await waitFor(() => probe(port), { timeout: 10000, every: 250 })) {
          this.browser = await connectCdp(`http://127.0.0.1:${port}`);
          this.context = this.browser.contexts()[0] || (await this.browser.newContext());
          this.mode = `cdp-spawned ${sel.name} (:${port})`;
          this.live = true;
          this.ownsBrowser = false;
          console.warn("Attached to your live browser session — voice commands act on your real tabs.");
          return this._setup({ startUrl: null });
        }
        console.warn(`Could not reach ${sel.name} on :${port} after spawning; falling through to a dedicated window.`);
      } else if (alreadyRunning) {
        throw new Error(
          `${sel.name} is running without remote debugging, so voice-browser cannot attach to its tabs.\n` +
            `  Option 1 (live tabs): quit ${sel.name}, then re-run — it will be started debuggable automatically.\n` +
            `  Option 2 (separate window): re-run with --browser chromium.\n` +
            `  Option 3 (manual): start it yourself with --remote-debugging-port=${port}, then re-run.`,
        );
      }
    }

    // Brand launch in the dedicated automation profile (or bundled fallback).
    // NOTE: Playwright's launchers are methods — always call them on their
    // driver object (or via the injected test hook), never detached, or
    // `this` is undefined inside Playwright and it throws `'_playwright'`.
    // NOTE 2: Firefox-family browsers (Zen, Firefox) are NOT launched even when
    // detected: Playwright drives Firefox via its Juggler protocol, which stock
    // and fork builds don't speak — the launch hangs forever (verified with Zen:
    // 25s+, no output). Skip straight to bundled Chromium with a clear message.
    const launchChromium = launchPersistent || ((dir, opts) => chromium.launchPersistentContext(dir, opts));
    const timeoutMs = Number(launchTimeoutMs) || 60000;
    const windowArgs = headless ? [] : ["--window-size=1280,900", "--window-position=40,40"];
    const viewport = headless ? { width: 1280, height: 900 } : null;
    if (sel.engine === "firefox") {
      console.warn(
        `${sel.name} is Firefox-based and cannot be automated by Playwright (launch hangs); using bundled Chromium instead.`,
      );
    }
    // Chrome + named profile: drive the user's real Chrome window (own profile, own logins).
    // Requires Chrome to be CLOSED at startup — a running instance holds a single-instance
    // lock on the user-data dir and the launch fails. Never kill it; instruct instead.
    if (sel.engine === "chromium" && (sel.channel === "chrome" || sel.name === "Chrome")) {
      let prof = null;
      try {
        prof = await resolveProfile(profileWant);
      } catch (err) {
        console.warn(`Chrome profile lookup failed (${err.message || err}); continuing without it.`);
      }
      if (prof && !prof.error) {
        const exeName = "chrome.exe";
        if (await running(exeName)) {
          throw new Error(
            `Chrome is already running, so voice-browser cannot open your "${profileWant}" window (profile lock).\n` +
              `  Quit Chrome once, then re-run — your "${profileWant}" window opens automatically.\n` +
              `  Or: re-run with --live after starting Chrome yourself with --remote-debugging-port=${port}.`,
          );
        }
        try {
          console.log(`Launching Chrome with your "${profileWant}" profile (real logins, visible window)…`);
          this.context = await withTimeout(
            launchChromium(prof.userDataDir, {
              channel: "chrome",
              headless,
              viewport,
              args: [...windowArgs, `--profile-directory=${prof.profileDirectory}`],
              ignoreDefaultArgs: ["--enable-automation"],
            }),
            timeoutMs,
            `Chrome "${profileWant}" launch`,
          );
          this.mode = `brand Chrome + profile "${profileWant}" (your real window)`;
          this.live = false;
          this.ownsBrowser = true;
          console.warn("Driving your real Chrome profile — voice commands act with your logins. Destructive actions still ask first.");
          return this._setup({ startUrl });
        } catch (err) {
          console.warn(`Chrome "${profileWant}" profile is in use; launching Google Chrome with a dedicated profile.`);
        }
      } else if (prof) {
        console.warn(
          `Chrome profile "${profileWant}" not found${prof.available?.length ? ` (available: ${prof.available.join(", ")})` : ""}; continuing without it.`,
        );
      }
    }
    try {
      if (sel.engine === "chromium" && sel.channel) {
        console.log(`Launching ${sel.name} (dedicated automation profile)…`);
        this.context = await withTimeout(
          launchChromium(profileDir, {
            channel: sel.channel,
            headless,
            viewport,
            args: windowArgs,
            ignoreDefaultArgs: ["--enable-automation"],
          }),
          timeoutMs,
          `${sel.name} launch`,
        );
        this.mode = `brand ${sel.name} (dedicated profile)`;
      } else if (sel.engine === "chromium" && sel.executablePath) {
        console.log(`Launching ${sel.name} (dedicated automation profile)…`);
        this.context = await withTimeout(
          launchChromium(profileDir, {
            executablePath: sel.executablePath,
            headless,
            viewport,
            args: windowArgs,
            ignoreDefaultArgs: ["--enable-automation"],
          }),
          timeoutMs,
          `${sel.name} launch`,
        );
        this.mode = `brand ${sel.name} (dedicated profile)`;
      } else {
        throw new Error("no launchable Chromium brand; using bundled Chromium");
      }
    } catch (err) {
      // Brand launch failed or hung → bundled Chromium always works.
      console.warn(`Brand launch failed (${err.message || err}); falling back to bundled Chromium.`);
      this.context = await withTimeout(
        launchChromium(profileDir, {
          headless,
          viewport: headless ? { width: 1280, height: 900 } : null,
          args: headless ? [] : ["--window-size=1280,900", "--window-position=40,40"],
          ignoreDefaultArgs: ["--enable-automation"],
        }),
        timeoutMs,
        "bundled Chromium launch",
      );
      this.mode = "bundled Chromium (dedicated profile)";
    }
    this.live = false;
    this.ownsBrowser = true;
    return this._setup({ startUrl });
  }

  /** Shared post-launch wiring. startUrl=null leaves live sessions untouched. */
  async _setup({ startUrl }) {
    await this.context.addInitScript(installOverlay);

    this.context.on("page", (page) => this._track(page));
    // Always open a new browser tab for the profile session
    const freshPage = await this.context.newPage();
    this.active = freshPage;
    if (startUrl && startUrl !== "about:blank") {
      await this.active.goto(startUrl, { waitUntil: "domcontentloaded" }).catch(() => {});
    }
    this._emit();
    return this;
  }

  _track(page) {
    if (this.pages.includes(page)) return;
    this.pages.push(page);
    this.active = page;
    page.on("close", () => {
      this.pages = this.pages.filter((p) => p !== page);
      if (this.active === page) this.active = this.pages[this.pages.length - 1] || null;
      this._emit();
    });
    page.on("framenavigated", (frame) => {
      if (frame === page.mainFrame()) this._emit();
    });
    this._emit();
  }

  get page() {
    if (!this.active || this.active.isClosed()) this.active = this.pages.find((p) => !p.isClosed()) || null;
    return this.active;
  }

  async ensurePage(waitMs = 6000) {
    if (this.page && !this.page.isClosed()) return this.page;
    const t0 = Date.now();
    while (!this.context && Date.now() - t0 < waitMs) {
      await new Promise((r) => setTimeout(r, 200));
    }
    if (!this.page && this.context) {
      await this.context.newPage().catch(() => {});
    }
    return this.page;
  }

  async setActive(page) {
    this.active = page;
    await page.bringToFront().catch(() => {});
    this._emit();
  }

  tabInfo() {
    return this.pages.map((p, i) => ({ index: i, url: p.url(), active: p === this.active }));
  }

  /** URL of the active page, or null (sync; used to record what an action led to). */
  currentUrl() {
    try {
      return this.active && !this.active.isClosed() ? this.active.url() : null;
    } catch {
      return null;
    }
  }

  /** Snapshot the active page (URL, title, compact element list, search box, site). */
  async snapshot() {
    const page = await this.ensurePage();
    if (!page) {
      return buildSnapshot(
        { url: "about:blank", title: "", scrollY: 0, scrollHeight: 0, viewportHeight: 0, elements: [] },
        { tabs: this.tabInfo() },
      );
    }
    try {
      await page.waitForLoadState("domcontentloaded", { timeout: 1500 }).catch(() => {});
      const data = await page.evaluate(collectElementsInPage);
      return buildSnapshot(data, { tabs: this.tabInfo() });
    } catch (err) {
      // e.g. navigation in progress; return a minimal snapshot
      return buildSnapshot({ url: page.url(), title: "", scrollY: 0, scrollHeight: 0, viewportHeight: 0, elements: [] }, { tabs: this.tabInfo(), error: String(err.message || err) });
    }
  }

  /** Call window.__vb.<fn>(...args) in the active page, swallowing errors. */
  async overlay(fn, ...args) {
    const page = this.page;
    if (!page) return;
    await page.evaluate(installOverlay).catch(() => {}); // no-op if already installed by the init script
    await page
      .evaluate(
        ([fn, args]) => {
          if (!window.__vb) return false;
          return window.__vb[fn](...args);
        },
        [fn, args],
      )
      .catch(() => {});
  }

  async close() {
    // Attached sessions belong to the user: detach, never shut their browser.
    if (!this.ownsBrowser) return;
    await this.context?.close().catch(() => {});
    await this.browser?.close().catch(() => {});
  }
}
