/**
 * Default-browser detection + live-attach helpers (Phase 1+2).
 *
 * All OS interaction goes through injectable deps so this module is unit-testable
 * without a registry, a browser, or a network port.
 *
 *   detectDefaultBrowser() → { engine, name, channel?, executablePath?, realProfileDir?, source }
 *     engine: "chromium" | "firefox"   (Playwright driver family to use)
 *     channel: Playwright channel ("chrome" | "msedge") for branded Chromium
 *     executablePath: full path to a Firefox-family exe (Zen / Firefox) or
 *       Chromium-fork exe (Brave / Vivaldi / …) for Playwright's executablePath
 *     realProfileDir: the browser's real user-data dir (Chromium only; needed
 *       to spawn a debuggable instance that owns the live session)
 *     source: "registry" | "startmenu" | "wellknown" | "fallback"
 */
import path from "node:path";
import { execFile } from "node:child_process";
import { existsSync as fsExistsSync } from "node:fs";

const execFileAsync = (file, args) =>
  new Promise((resolve, reject) =>
    execFile(file, args, { windowsHide: true }, (err, stdout) => (err ? reject(err) : resolve(stdout))),
  );

/** exe basename (lowercased) → Playwright driver info. */
export function classifyExecutable(exePath) {
  const base = String(exePath || "")
    .split(/[\\/]/)
    .pop()
    .toLowerCase()
    .replace(/\.exe$/, "");
  switch (base) {
    case "chrome":
      return { engine: "chromium", channel: "chrome", name: "Chrome" };
    case "msedge":
      return { engine: "chromium", channel: "msedge", name: "Edge" };
    case "brave":
      return { engine: "chromium", name: "Brave" };
    case "vivaldi":
      return { engine: "chromium", name: "Vivaldi" };
    case "opera":
      return { engine: "chromium", name: "Opera" };
    case "zen":
      return { engine: "firefox", name: "Zen" };
    case "firefox":
      return { engine: "firefox", name: "Firefox" };
    default:
      return null;
  }
}

/**
 * Parse a registry `shell\open\command` default value into an exe path.
 * e.g. `"C:\Program Files\Zen Browser\zen.exe" --args "%1"` → `C:\Program Files\Zen Browser\zen.exe`
 */
export function parseOpenCommand(cmd) {
  const s = String(cmd || "").trim();
  if (!s) return null;
  if (s.startsWith('"')) {
    const end = s.indexOf('"', 1);
    return end > 1 ? s.slice(1, end) : null;
  }
  // Unquoted: exe path is the first token ending in .exe
  const m = s.match(/^(.+?\.exe)\b/i);
  return m ? m[1] : s.split(/\s+/)[0] || null;
}

/** `reg query` output → the REG_SZ value. */
export function parseRegSz(output) {
  const m = String(output || "").match(/REG_SZ\s+(.+?)\s*$/m);
  return m ? m[1].trim() : null;
}

function joinProfile(env, ...parts) {
  const root = env.LOCALAPPDATA || env.USERPROFILE;
  if (!root) return null;
  return path.join(root, ...parts);
}

/** Real Chromium user-data dirs by browser name (live-takeover spawn target). */
export function realProfileDirFor(name, env = process.env) {
  switch (name) {
    case "Chrome":
      return joinProfile(env, "Google", "Chrome", "User Data");
    case "Edge":
      return joinProfile(env, "Microsoft", "Edge", "User Data");
    case "Brave":
      return joinProfile(env, "BraveSoftware", "Brave-Browser", "User Data");
    case "Vivaldi":
      return joinProfile(env, "Vivaldi", "User Data");
    default:
      return null;
  }
}

/**
 * Resolve a Chrome profile NAME (as shown in the profile picker, e.g. "Ian")
 * to its user-data dir + profile directory, via the `Local State` info_cache.
 * Returns { userDataDir, profileDirectory } or { error, available }.
 * Deps: { env, readFile } (readFile defaults to node:fs/promises).
 */
export async function resolveChromeProfile(want = "Ian", deps = {}) {
  const env = deps.env || process.env;
  const platform = deps.platform || process.platform;
  const readFile = deps.readFile || ((p) => import("node:fs/promises").then((fs) => fs.readFile(p, "utf8")));
  const home = env.USERPROFILE || env.HOME || "";
  const roots =
    platform === "darwin" && home
      ? [path.join(home, "Library", "Application Support", "Google", "Chrome")]
      : platform === "linux" && home
        ? [path.join(home, ".config", "google-chrome")]
        : env.LOCALAPPDATA
          ? [path.join(env.LOCALAPPDATA, "Google", "Chrome", "User Data")]
          : [];

  let info = null;
  let userDataDir = null;
  for (const root of roots) {
    try {
      const raw = await readFile(path.join(root, "Local State"));
      info = JSON.parse(raw)?.profile?.info_cache || null;
      if (info) {
        userDataDir = root;
        break;
      }
    } catch {
      // try next root
    }
  }
  if (!info) return { error: "Chrome Local State not found", available: [] };
  const available = Object.entries(info).map(([dir, v]) => ({ dir, name: v?.name || dir }));
  const hit = available.find((p) => String(p.name).toLowerCase() === String(want).toLowerCase());
  if (!hit) return { error: `Chrome profile "${want}" not found`, available: available.map((p) => p.name) };
  return { userDataDir, profileDirectory: hit.dir };
}

/** Well-known install paths for a browser name (Windows; empty elsewhere). Exported for the spawn path. */
export function candidatesFor(name, env) {
  const pf64 = env["ProgramFiles"] || "C:\\Program Files";
  const pf86 = env["ProgramFiles(x86)"] || "C:\\Program Files (x86)";
  const local = env.LOCALAPPDATA || "";
  switch (name) {
    case "Chrome":
      return [path.join(pf64, "Google", "Chrome", "Application", "chrome.exe")];
    case "Edge":
      return [
        path.join(pf86, "Microsoft", "Edge", "Application", "msedge.exe"),
        path.join(pf64, "Microsoft", "Edge", "Application", "msedge.exe"),
      ];
    case "Zen":
      return [
        path.join(pf64, "Zen Browser", "zen.exe"),
        ...(local ? [path.join(local, "Zen Browser", "zen.exe")] : []),
      ];
    case "Firefox":
      return [
        path.join(pf64, "Mozilla Firefox", "firefox.exe"),
        path.join(pf86, "Mozilla Firefox", "firefox.exe"),
      ];
    case "Brave":
      return [path.join(pf64, "BraveSoftware", "Brave-Browser", "Application", "brave.exe")];
    default:
      return [];
  }
}

export function processNameFor(info) {
  if (info?.executablePath) return String(info.executablePath).split(/[\\/]/).pop().toLowerCase();
  if (info?.channel === "chrome") return "chrome.exe";
  if (info?.channel === "msedge") return "msedge.exe";
  return null;
}

/**
 * Detect the OS default browser. Never throws — falls back to bundled Chromium.
 * Deps (all optional, for tests): { platform, env, exec, existsSync }
 */
export async function detectDefaultBrowser(deps = {}) {
  const platform = deps.platform || process.platform;
  const env = deps.env || process.env;
  const exec = deps.exec || execFileAsync;
  const existsSync = deps.existsSync || fsExistsSync;
  const fallback = () => ({ engine: "chromium", name: "Chromium (bundled)", source: "fallback" });

  if (platform !== "win32") {
    // Non-Windows: no registry; try well-known macOS/Linux locations.
    const unixPaths = [
      "/usr/bin/google-chrome",
      "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
      "/usr/bin/firefox",
      "/Applications/Firefox.app/Contents/MacOS/firefox",
    ];
    for (const p of unixPaths) {
      if (existsSync(p)) {
        const cls = classifyExecutable(p);
        if (cls) return finish({ ...cls, executablePath: cls.channel ? undefined : p }, "wellknown", env, existsSync);
      }
    }
    return fallback();
  }

  // 1. UserChoice ProgId → HKCR open command → exe path.
  try {
    const userChoiceKey =
      "HKCU\\Software\\Microsoft\\Windows\\Shell\\Associations\\UrlAssociations\\http\\UserChoice";
    const progId = parseRegSz(await exec("reg", ["query", userChoiceKey, "/v", "ProgId"]));
    if (progId) {
      const cmd = parseRegSz(await exec("reg", ["query", `HKCR\\${progId}\\shell\\open\\command`, "/ve"]));
      const exe = parseOpenCommand(cmd);
      const cls = exe && classifyExecutable(exe);
      if (cls) return finish({ ...cls, executablePath: cls.channel ? undefined : exe }, "registry", env, existsSync);
    }
  } catch {
    // fall through to next source
  }

  // 2. StartMenuInternet client.
  try {
    for (const hive of ["HKCU", "HKLM"]) {
      const client = parseRegSz(await exec("reg", ["query", `${hive}\\SOFTWARE\\Clients\\StartMenuInternet`, "/ve"]));
      if (client) {
        let cmd = null;
        try {
          cmd = parseRegSz(
            await exec("reg", ["query", `${hive}\\SOFTWARE\\Clients\\${client}\\shell\\open\\command`, "/ve"]),
          );
        } catch {
          cmd = null;
        }
        const exe = parseOpenCommand(cmd);
        const cls = exe && classifyExecutable(exe);
        if (cls) return finish({ ...cls, executablePath: cls.channel ? undefined : exe }, "startmenu", env, existsSync);
      }
    }
  } catch {
    // fall through
  }

  // 3. Well-known install paths (Chrome → Edge → Zen → Firefox → Brave).
  for (const name of ["Chrome", "Edge", "Zen", "Firefox", "Brave"]) {
    for (const p of candidatesFor(name, env)) {
      if (existsSync(p)) {
        const cls = classifyExecutable(p);
        return finish({ ...cls, executablePath: cls.channel ? undefined : p }, "wellknown", env, existsSync);
      }
    }
  }

  return fallback();
}

function finish(info, source, env, existsSync) {
  const out = { ...info, source };
  if (out.engine === "chromium" && !out.channel && out.executablePath) {
    // Chromium fork via executablePath: keep path only if it exists.
    if (!existsSync(out.executablePath)) delete out.executablePath;
  }
  if (out.engine === "chromium") out.realProfileDir = realProfileDirFor(out.name, env) || undefined;
  return out;
}

/**
 * Resolve an explicit --browser / BROWSER value to driver info.
 * Returns null for "auto" (caller should detect). Never throws — unknown
 * values resolve to bundled Chromium with a warning flag.
 */
export function resolveBrowserOption(want, deps = {}) {
  const env = deps.env || process.env;
  const existsSync = deps.existsSync || fsExistsSync;
  const w = String(want || "auto").toLowerCase();
  if (w === "auto") return null;
  const table = {
    chrome: { engine: "chromium", channel: "chrome", name: "Chrome" },
    edge: { engine: "chromium", channel: "msedge", name: "Edge" },
    chromium: { engine: "chromium", name: "Chromium (bundled)" },
    zen: { engine: "firefox", name: "Zen" },
    firefox: { engine: "firefox", name: "Firefox" },
  };
  const base = table[w];
  if (!base) return { engine: "chromium", name: "Chromium (bundled)", unknownOption: want };
  if ((w === "zen" || w === "firefox") && !base.executablePath) {
    const found = candidatesFor(base.name, env).find((p) => existsSync(p));
    if (!found) return { engine: "chromium", name: "Chromium (bundled)", missingExe: base.name };
    return { ...base, executablePath: found };
  }
  return { ...base };
}

/** True when something answers the CDP HTTP endpoint. Never throws. */
export async function probeCdp(port = 9222, fetchFn = fetch) {
  try {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 1500);
    try {
      const res = await fetchFn(`http://127.0.0.1:${port}/json/version`, { signal: ctl.signal });
      return Boolean(res && res.ok);
    } finally {
      clearTimeout(t);
    }
  } catch {
    return false;
  }
}

/** True when a process image is running. Never throws. Deps: { platform, env, exec }. */
export async function isProcessRunning(exeName, deps = {}) {
  if (!exeName) return false;
  const platform = deps.platform || process.platform;
  const exec = deps.exec || execFileAsync;
  try {
    if (platform === "win32") {
      const out = await exec("tasklist", ["/FI", `IMAGENAME eq ${exeName}`, "/FO", "CSV", "/NH"]);
      return String(out).toLowerCase().includes(`"${String(exeName).toLowerCase()}"`);
    }
    const out = await exec("pgrep", ["-x", exeName]);
    return String(out).trim().length > 0;
  } catch {
    return false;
  }
}
