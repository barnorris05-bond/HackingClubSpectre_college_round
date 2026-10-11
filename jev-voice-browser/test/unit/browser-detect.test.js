import { test } from "node:test";
import assert from "node:assert/strict";
import {
  classifyExecutable,
  parseOpenCommand,
  parseRegSz,
  realProfileDirFor,
  resolveBrowserOption,
  resolveChromeProfile,
  detectDefaultBrowser,
  probeCdp,
  isProcessRunning,
} from "../../src/browser-detect.js";

test("classifyExecutable maps known exes", () => {
  assert.deepEqual(classifyExecutable("C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"), {
    engine: "chromium",
    channel: "chrome",
    name: "Chrome",
  });
  assert.deepEqual(classifyExecutable("C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"), {
    engine: "chromium",
    channel: "msedge",
    name: "Edge",
  });
  assert.deepEqual(classifyExecutable("C:\\Program Files\\Zen Browser\\zen.exe"), {
    engine: "firefox",
    name: "Zen",
  });
  assert.deepEqual(classifyExecutable("/usr/bin/firefox"), { engine: "firefox", name: "Firefox" });
  assert.deepEqual(classifyExecutable("C:\\x\\brave.exe"), { engine: "chromium", name: "Brave" });
  assert.equal(classifyExecutable("C:\\x\\arc.exe"), null);
  assert.equal(classifyExecutable(null), null);
});

test("parseOpenCommand handles quoted, unquoted, empty", () => {
  assert.equal(parseOpenCommand('"C:\\Program Files\\Zen Browser\\zen.exe" --args "%1"'), "C:\\Program Files\\Zen Browser\\zen.exe");
  assert.equal(parseOpenCommand("C:\\Tools\\chrome.exe --foo"), "C:\\Tools\\chrome.exe");
  assert.equal(parseOpenCommand(""), null);
  assert.equal(parseOpenCommand(null), null);
});

test("parseRegSz extracts REG_SZ value", () => {
  const out = "\r\nHKEY_... \r\n    ProgId    REG_SZ    FirefoxURL-F0DC299D809B9700\r\n";
  assert.equal(parseRegSz(out), "FirefoxURL-F0DC299D809B9700");
  assert.equal(parseRegSz("no match here"), null);
});

test("realProfileDirFor knows Chromium layouts", () => {
  const env = { LOCALAPPDATA: "C:\\Users\\u\\AppData\\Local" };
  assert.ok(realProfileDirFor("Chrome", env).endsWith("Google\\Chrome\\User Data"));
  assert.ok(realProfileDirFor("Edge", env).endsWith("Microsoft\\Edge\\User Data"));
  assert.equal(realProfileDirFor("Zen", env), null);
  assert.equal(realProfileDirFor("Firefox", env), null);
});

test("resolveBrowserOption: auto → null, unknowns → bundled fallback", () => {
  assert.equal(resolveBrowserOption("auto"), null);
  assert.deepEqual(resolveBrowserOption("chrome"), { engine: "chromium", channel: "chrome", name: "Chrome" });
  const unknown = resolveBrowserOption("arc");
  assert.equal(unknown.name, "Chromium (bundled)");
  assert.equal(unknown.unknownOption, "arc");
});

test("resolveBrowserOption: zen found / missing", () => {
  const found = resolveBrowserOption("zen", {
    env: { ProgramFiles: "C:\\Program Files", "ProgramFiles(x86)": "C:\\Px86", LOCALAPPDATA: "C:\\L" },
    existsSync: (p) => p === "C:\\Program Files\\Zen Browser\\zen.exe",
  });
  assert.equal(found.executablePath, "C:\\Program Files\\Zen Browser\\zen.exe");
  assert.equal(found.engine, "firefox");
  const missing = resolveBrowserOption("zen", { env: {}, existsSync: () => false });
  assert.equal(missing.missingExe, "Zen");
});

function regExec(map) {
  return async (file, args) => {
    assert.equal(file, "reg");
    const key = `${args[1]}|${args[2] || ""}|${args[3] || ""}`;
    if (!(key in map)) throw new Error(`reg: no mock for ${key}`);
    return map[key];
  };
}

test("detectDefaultBrowser: registry Zen ProgId", async () => {
  const sel = await detectDefaultBrowser({
    platform: "win32",
    env: {},
    existsSync: () => true,
    exec: regExec({
      "HKCU\\Software\\Microsoft\\Windows\\Shell\\Associations\\UrlAssociations\\http\\UserChoice|/v|ProgId":
        "    ProgId    REG_SZ    FirefoxURL-F0DC299D809B9700",
      "HKCR\\FirefoxURL-F0DC299D809B9700\\shell\\open\\command|/ve|":
        '    (Default)    REG_SZ    "C:\\Program Files\\Zen Browser\\zen.exe" --osint -- "%1"',
    }),
  });
  assert.equal(sel.name, "Zen");
  assert.equal(sel.engine, "firefox");
  assert.equal(sel.executablePath, "C:\\Program Files\\Zen Browser\\zen.exe");
  assert.equal(sel.source, "registry");
});

test("detectDefaultBrowser: registry Chrome ProgId → channel, real profile", async () => {
  const sel = await detectDefaultBrowser({
    platform: "win32",
    env: { LOCALAPPDATA: "C:\\L" },
    existsSync: () => true,
    exec: regExec({
      "HKCU\\Software\\Microsoft\\Windows\\Shell\\Associations\\UrlAssociations\\http\\UserChoice|/v|ProgId":
        "    ProgId    REG_SZ    ChromeHTML",
      "HKCR\\ChromeHTML\\shell\\open\\command|/ve|":
        '    (Default)    REG_SZ    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --single-argument %1',
    }),
  });
  assert.equal(sel.channel, "chrome");
  assert.equal(sel.executablePath, undefined);
  assert.ok(sel.realProfileDir.endsWith("Google\\Chrome\\User Data"));
});

test("detectDefaultBrowser: registry failure → well-known fallback", async () => {
  const sel = await detectDefaultBrowser({
    platform: "win32",
    env: { ProgramFiles: "C:\\Program Files", "ProgramFiles(x86)": "C:\\Px86", LOCALAPPDATA: "C:\\L" },
    existsSync: (p) => p === "C:\\Program Files\\Zen Browser\\zen.exe",
    exec: async () => {
      throw new Error("reg not available");
    },
  });
  assert.equal(sel.name, "Zen");
  assert.equal(sel.source, "wellknown");
});

test("detectDefaultBrowser: nothing found → bundled fallback (never throws)", async () => {
  const sel = await detectDefaultBrowser({
    platform: "win32",
    env: {},
    existsSync: () => false,
    exec: async () => {
      throw new Error("nope");
    },
  });
  assert.equal(sel.name, "Chromium (bundled)");
  assert.equal(sel.source, "fallback");
});

test("probeCdp: true on ok, false on error or !ok", async () => {
  assert.equal(await probeCdp(9222, async () => ({ ok: true })), true);
  assert.equal(await probeCdp(9222, async () => ({ ok: false })), false);
  assert.equal(
    await probeCdp(9222, async () => {
      throw new Error("refused");
    }),
    false,
  );
});

test("isProcessRunning parses tasklist CSV", async () => {
  const yes = await isProcessRunning("chrome.exe", {
    platform: "win32",
    exec: async () => '"chrome.exe","1234","Console","1","100,000 K"\r\n',
  });
  const no = await isProcessRunning("chrome.exe", { platform: "win32", exec: async () => "INFO: No tasks\r\n" });
  assert.equal(yes, true);
  assert.equal(no, false);
  assert.equal(await isProcessRunning(null), false);
});

const LOCAL_STATE = JSON.stringify({
  profile: {
    info_cache: {
      Default: { name: "Ian" },
      "Profile 1": { name: "AI Study" },
    },
  },
});

test("resolveChromeProfile matches by display name (case-insensitive)", async () => {
  const r = await resolveChromeProfile("ian", {
    env: { LOCALAPPDATA: "C:\\L" },
    platform: "win32",
    readFile: async (p) => {
      assert.ok(p.endsWith("Local State"));
      return LOCAL_STATE;
    },
  });
  assert.equal(r.userDataDir, "C:\\L\\Google\\Chrome\\User Data");
  assert.equal(r.profileDirectory, "Default");
});

test("resolveChromeProfile: unknown name lists available profiles", async () => {
  const r = await resolveChromeProfile("Ian Lopes", {
    env: { LOCALAPPDATA: "C:\\L" },
    platform: "win32",
    readFile: async () => LOCAL_STATE,
  });
  assert.ok(r.error.includes("Ian Lopes"));
  assert.deepEqual(r.available, ["Ian", "AI Study"]);
});

test("resolveChromeProfile: unreadable Local State → error, no throw", async () => {
  const r = await resolveChromeProfile("Ian", {
    env: {},
    platform: "win32",
    readFile: async () => {
      throw new Error("no file");
    },
  });
  assert.ok(r.error);
  assert.deepEqual(r.available, []);
});
