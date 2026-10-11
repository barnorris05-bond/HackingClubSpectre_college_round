/**
 * Regression tests for the "infinite spiral" fix + box-command gating:
 *  - wait-forever answers stop after MAX_DECIDE_ATTEMPTS (per unchanged text), loudly
 *  - new words reset the budget
 *  - box-typed commands bypass is_command but keep every other gate
 *  - checkJev() never throws
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { Controller } from "../../src/controller.js";
import { evaluatePolicy } from "../../src/policy.js";
import { checkJev } from "../../src/jev.js";
import { MAX_DECIDE_ATTEMPTS } from "../../src/constants.js";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function fakeBrowser() {
  return {
    url: "https://example.com/",
    onChange() {
      return () => {};
    },
    tabInfo() {
      return [{ index: 0, url: this.url, active: true }];
    },
    async snapshot() {
      return {
        url: this.url,
        title: "Example",
        site: "example_com",
        searchBoxId: null,
        elements: [{ id: "e01", role: "link", text: "More information" }],
        tabs: this.tabInfo(),
      };
    },
    overlayCalls: [],
    async overlay(fn, ...args) {
      this.overlayCalls.push([fn, ...args]);
    },
    currentUrl() {
      return this.url;
    },
  };
}

const ch = (c, conf = 0.95, extra = {}) => ({ type: "choice", choice: c, confidence: conf, probabilities: { [c]: conf, ...extra } });

/** Answers that always evaluate to `wait` (low intent confidence, high is_command). */
function waitAnswers() {
  return {
    intent: ch("scroll_down", 0.1),
    target: ch("none", 0.9),
    site: ch("none", 0.9),
    complete: { type: "noul", noul: 0.9 },
    is_command: { type: "noul", noul: 0.95 },
    destructive: { type: "noul", noul: 0.02 },
    scroll_amount: { type: "score", score: 1, confidence: 0.9, probabilities: {} },
    tab_direction: ch("none", 0.9),
  };
}

test("wait-forever stops after MAX_DECIDE_ATTEMPTS with a visible reason", async () => {
  const browser = fakeBrowser();
  let calls = 0;
  const decideFn = async () => {
    calls += 1;
    return { answers: waitAnswers(), latencyMs: 5, usage: { input_tokens: 10 }, costUsd: 0, model: "t", requestId: "r", candidates: { text: [], url: [] }, state: {}, questionCount: 8 };
  };
  const c = new Controller({ browser, decideFn, executeFn: async () => ({ ok: true }) });
  await c.start();
  c.handleTranscript({ text: "scroll somewhere maybe", final: false, utteranceId: "u1" });
  await sleep(6500);
  assert.equal(calls, MAX_DECIDE_ATTEMPTS);
  assert.equal(c.utterance.gaveUp, true);
  const warn = c.log.find((e) => e.level === "warn" && e.msg.includes("giving up"));
  assert.ok(warn, "give-up reason is logged");
  assert.ok(warn.msg.includes("intent"), "reason names the failing gate");
  assert.ok(browser.overlayCalls.some(([fn]) => fn === "toast"), "toast shown on the page");
  const callsAfter = calls;
  await sleep(1500);
  assert.equal(calls, callsAfter, "no further requests after give-up");
  await c.close();
});

test("new words reset the attempt budget", async () => {
  const browser = fakeBrowser();
  let calls = 0;
  const decideFn = async () => {
    calls += 1;
    return { answers: waitAnswers(), latencyMs: 5, usage: { input_tokens: 10 }, costUsd: 0, model: "t", requestId: "r", candidates: { text: [], url: [] }, state: {}, questionCount: 8 };
  };
  const c = new Controller({ browser, decideFn, executeFn: async () => ({ ok: true }) });
  await c.start();
  c.handleTranscript({ text: "scroll somewhere", final: false, utteranceId: "u2" });
  await sleep(600); // first request done, silence retry (~900ms) not yet fired
  assert.ok(calls >= 1, "at least one request went out");
  assert.equal(c.utterance.gaveUp, false);
  assert.ok((c.utterance.attempts ?? 0) >= 1);
  c.handleTranscript({ text: "scroll somewhere down please", final: false, utteranceId: "u2" });
  assert.equal(c.utterance.attempts, 0, "budget resets on new words");
  assert.equal(c.utterance.gaveUp, false);
  await c.close();
});

test("box-typed command with laya-like low is_command still acts", async () => {
  const browser = fakeBrowser();
  const executed = [];
  const decideFn = async () => ({
    answers: {
      intent: ch("go_back", 0.99),
      target: ch("none", 0.9),
      site: ch("none", 0.9),
      complete: { type: "noul", noul: 0.9 },
      is_command: { type: "noul", noul: 0.31 }, // laya-like: real command, low gate
      destructive: { type: "noul", noul: 0.02 },
      scroll_amount: { type: "score", score: 1, confidence: 0.9, probabilities: {} },
      tab_direction: ch("none", 0.9),
    },
    latencyMs: 5,
    usage: { input_tokens: 10 },
    costUsd: 0,
    model: "t",
    requestId: "r",
    candidates: { text: [], url: [] },
    state: {},
    questionCount: 8,
  });
  const c = new Controller({ browser, decideFn, executeFn: async (a) => (executed.push(a), { ok: true, detail: "" }) });
  await c.start();
  c.handleCommand("go back");
  await sleep(300);
  assert.equal(executed.length, 1);
  assert.equal(executed[0].type, "go_back");
  await c.close();
});

test("policy fromBox bypasses is_command only", () => {
  const snapshot = { url: "https://example.com/", title: "E", site: "example_com", elements: [] };
  const candidates = { text: [], url: [] };
  const base = {
    intent: ch("scroll_down", 0.95),
    target: ch("none", 0.9),
    site: ch("none", 0.9),
    complete: { type: "noul", noul: 0.9 },
    is_command: { type: "noul", noul: 0.1 },
    destructive: { type: "noul", noul: 0.05 },
    scroll_amount: { type: "score", score: 1, confidence: 0.9, probabilities: {} },
    tab_direction: ch("none", 0.9),
  };
  assert.equal(evaluatePolicy({ answers: base, candidates, snapshot }).decision, "ignore");
  const r = evaluatePolicy({ answers: base, candidates, snapshot, fromBox: true });
  assert.equal(r.decision, "act");
  assert.equal(r.reasons.find((x) => x.name === "is_command").pass, true);
});

test("checkJev: true on ok, false on failure — never throws", async () => {
  assert.equal(await checkJev(async () => ({ ok: true })), true);
  assert.equal(await checkJev(async () => ({ ok: false })), false);
  assert.equal(
    await checkJev(async () => {
      throw new Error("refused");
    }),
    false,
  );
});
