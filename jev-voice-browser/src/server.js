/**
 * Node server: serves the control page, bridges WebSocket <-> Controller, owns the API key.
 *
 *   node src/server.js [--port 8787] [--host 127.0.0.1] [--headless] [--cdp ws://127.0.0.1:9222/devtools/browser/...] [--live] [--browser chrome] [--chrome-profile Ian] [--start-url https://...]
 */
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import express from "express";
import { WebSocketServer } from "ws";
import { BrowserManager } from "./browser.js";
import { Controller } from "./controller.js";
import { hasApiKey, getJevBaseUrl, checkJev } from "./jev.js";
import { MODEL, QUESTIONS, T } from "./constants.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export function parseArgs(argv) {
  const out = {
    port: Number(process.env.PORT) || 8787,
    // Bind to loopback only by default: anyone who can reach this port can drive the browser
    // and spend your API credits. Use --host 0.0.0.0 deliberately if you need LAN access.
    host: process.env.HOST || "127.0.0.1",
    headless: false,
    cdp: null,
    // Chrome-only default: installed Chrome + your profile in a visible window.
    // BROWSER env also honored (edge|zen|firefox|chromium|auto for detection).
    browser: process.env.BROWSER || "chrome",
    chromeProfile: process.env.CHROME_PROFILE || "Ian",
    // --live takes over the live session via CDP instead of opening an owned window.
    live: false,
    cdpPort: Number(process.env.CDP_PORT) || 9222,
    startUrl: "https://example.com/",
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--port") out.port = Number(argv[++i]);
    else if (a === "--host") out.host = argv[++i];
    else if (a === "--headless") out.headless = true;
    else if (a === "--cdp") out.cdp = argv[++i];
    else if (a === "--browser") out.browser = argv[++i];
    else if (a === "--chrome-profile") out.chromeProfile = argv[++i];
    else if (a === "--live") out.live = true;
    else if (a === "--cdp-port") out.cdpPort = Number(argv[++i]);
    else if (a === "--start-url") out.startUrl = argv[++i];
  }
  return out;
}

export async function startServer(opts = {}) {
  if (!hasApiKey()) {
    console.error("Missing TYPESAFE_API_KEY (or JEV_API_KEY) for cloud JEV. Use ./run.sh or export it first.");
    process.exit(1);
  }
  console.log(`JEV endpoint → ${getJevBaseUrl()}/v1/systemone`);
  const jevOk = await checkJev();
  console.log(jevOk ? `JEV ✓ laya reachable at ${getJevBaseUrl()}` : `JEV ✗ UNREACHABLE at ${getJevBaseUrl()} — start it first (uvicorn server:app --host 127.0.0.1 --port 8000); typed commands will error until then.`);
  const browser = new BrowserManager();
  const controller = new Controller({ browser });

  const app = express();
  app.use(express.static(path.join(__dirname, "public")));
  app.get("/api/state", (_req, res) => res.json(controller.uiState()));
  app.get("/api/questions", (_req, res) => res.json({ model: MODEL, thresholds: T, questions: QUESTIONS }));

  const server = http.createServer(app);
  const wss = new WebSocketServer({ server });

  const broadcast = (type, payload) => {
    const msg = JSON.stringify({ type, payload });
    for (const c of wss.clients) if (c.readyState === 1) c.send(msg);
  };

  controller.on("transcript", (p) => broadcast("transcript", p));
  controller.on("decision", (p) => broadcast("decision", p));
  controller.on("action", (p) => broadcast("action", { ...p, ui: controller.uiState() }));
  controller.on("snapshot", () => broadcast("snapshot", controller.uiState().snapshot));
  controller.on("log", (p) => broadcast("log", p));
  controller.on("candidates", (p) => broadcast("candidates", p));
  controller.on("pending", (p) => broadcast("pending", p));
  controller.on("tabs", (p) => broadcast("tabs", p));

  wss.on("connection", (ws) => {
    ws.send(JSON.stringify({ type: "hello", payload: controller.uiState() }));
    ws.on("message", async (raw) => {
      let msg;
      try {
        msg = JSON.parse(raw.toString());
      } catch {
        return;
      }
      switch (msg.type) {
        case "transcript":
          controller.handleTranscript({ text: msg.text, final: Boolean(msg.final), utteranceId: msg.utteranceId });
          break;
        case "command":
          controller.handleCommand(msg.text);
          break;
        case "undo":
          controller.undo();
          break;
        case "snapshot":
          controller.refreshSnapshot();
          break;
        case "state":
          ws.send(JSON.stringify({ type: "hello", payload: controller.uiState() }));
          break;
        default:
          break;
      }
    });
  });

  const host = opts.host || "127.0.0.1";
  await new Promise((resolve) => server.listen(opts.port, host, resolve));
  const url = `http://localhost:${opts.port}`;
  if (host !== "127.0.0.1" && host !== "localhost") {
    console.warn(`WARNING: listening on ${host} — anyone who can reach this port can control the browser and spend API credits.`);
  }
  console.log(`\nvoice-browser ready → open ${url} in Chrome (mic needs Chrome/Edge)`);
  console.log(`model ${MODEL} · launching browser… (the page above is already usable)\n`);

  // Launch AFTER listening: a slow browser start must never keep the control page from opening.
  // The control page (type commands here) is already live at `url` above.
  try {
    await browser.launch({ headless: opts.headless, cdp: opts.cdp, browser: opts.browser, chromeProfile: opts.chromeProfile, live: opts.live, cdpPort: opts.cdpPort, startUrl: opts.startUrl });
    console.log(`browser mode → ${browser.mode}`);
    console.log(`control page → ${url}  ·  result window: ${browser.mode}`);
    await controller.start();
    controller._log("info", `browser ready — ${browser.mode}`);
  } catch (err) {
    // Stay up: the control page shows the error and the user can fix flags and restart.
    console.error(`\nBrowser launch failed:\n  ${String(err.message || err).split("\n").join("\n  ")}\n`);
    controller._log("error", `browser launch failed: ${err.message || err}`);
  }

  const shutdown = async () => {
    await controller.close();
    await browser.close();
    server.close();
    process.exit(0);
  };
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
  return { app, server, controller, browser, url };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  startServer(parseArgs(process.argv.slice(2))).catch((err) => {
    console.error(err);
    process.exit(1);
  });
}
