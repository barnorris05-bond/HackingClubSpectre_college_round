/**
 * Execution policy: pure code that turns Jev's answers into ACT / WAIT / IGNORE / CONFIRM /
 * DISAMBIGUATE. Every gate is a number from constants.js so the UI can show WHY.
 */
import {
  T,
  TARGET_INTENTS,
  SITE_HOME,
  SITE_SEARCH,
  DEFAULT_SEARCH_ENGINE,
  SILENCE_COMPLETE_MS,
  PAYLOAD_SILENCE_MS,
  PAYLOAD_INTENTS,
} from "./constants.js";
import { toHttpUrl } from "./spans.js";

const r2 = (x) => Math.round(x * 100) / 100;

function check(reasons, name, value, threshold, pass, note) {
  reasons.push({ name, value: typeof value === "number" ? r2(value) : value, threshold, pass, note });
  return pass;
}

/** Detect known sites directly mentioned in the user's spoken or typed transcript. */
export function detectSpokenSite(transcript) {
  if (!transcript) return null;
  const t = transcript.toLowerCase();
  if (/\byoutube(\.com)?\b/.test(t)) return "youtube";
  if (/\bwikipedia(\.org)?\b/.test(t)) return "wikipedia";
  if (/\bgoogle(\.com)?\b/.test(t)) return "google";
  if (/\bgithub(\.com)?\b/.test(t)) return "github";
  if (/\bamazon(\.com)?\b/.test(t)) return "amazon";
  if (/\breddit(\.com)?\b/.test(t)) return "reddit";
  if (/\b(twitter|x\.com)\b/.test(t)) return "twitter_x";
  if (/\b(hacker\s*news|hn)\b/.test(t)) return "hacker_news";
  if (/\bduckduckgo(\.com)?\b/.test(t)) return "duckduckgo";
  if (/\bexample(\.com|\s+dot\s+com)?\b/.test(t)) return "example_com";
  return null;
}

/** Extract target phrase from commands like "click on shorts", "open shorts", "press login" */
export function extractTargetPhrase(text) {
  if (!text) return "";
  return String(text)
    .replace(/^\s*(?:click(?:\s+on)?|press|tap|open|select|choose|go\s+to)\s+(?:the\s+|a\s+|an\s+)?/i, "")
    .replace(/\s+(?:button|link|tab|option|menu)\s*$/i, "")
    .trim()
    .toLowerCase();
}

/** Search elements list for a phrase matching text, placeholder, or href */
export function findElementByPhrase(elements, phrase) {
  if (!phrase || phrase.length < 2 || !elements) return null;
  const p = phrase.toLowerCase();
  // 1. Exact match on element text or placeholder
  let match = elements.find((e) => (e.text && e.text.toLowerCase() === p) || (e.placeholder && e.placeholder.toLowerCase() === p));
  if (match) return match;
  // 2. Element text starts with phrase or phrase starts with element text
  match = elements.find((e) => e.text && (e.text.toLowerCase().startsWith(p) || (e.text.length >= 3 && p.startsWith(e.text.toLowerCase()))));
  if (match) return match;
  // 3. Substring match on text
  match = elements.find((e) => e.text && e.text.toLowerCase().includes(p));
  if (match) return match;
  // 4. Match in href (e.g., href: "youtube.com/shorts" and phrase is "shorts")
  match = elements.find((e) => e.href && e.href.toLowerCase().includes(p));
  if (match) return match;
  return null;
}

/** Check if element text, placeholder, or href matches a target phrase */
export function elementMatchesPhrase(el, phrase) {
  if (!el || !phrase) return false;
  const p = phrase.toLowerCase().trim();
  const text = (el.text || "").toLowerCase().trim();
  const placeholder = (el.placeholder || "").toLowerCase().trim();
  const href = (el.href || "").toLowerCase().trim();
  return (
    text === p ||
    (text.length >= 2 && text.includes(p)) ||
    (p.length >= 2 && p.includes(text)) ||
    placeholder.includes(p) ||
    href.includes(p)
  );
}

export const DESTRUCTIVE_KEYWORDS =
  /\b(delete|remove|destroy|erase|drop|buy|purchase|checkout|pay|order|sign\s*out|log\s*out|logout|cancel\s+subscription|unsubscribe|transfer|withdraw|format)\b/i;

/** Only truly irreversible actions on buttons/forms should demand spoken confirmation */
export function isDestructiveAction(action, elements = [], transcript = "", destructiveScore = 0) {
  if (!["click_element", "press_enter", "select_option"].includes(action?.type)) {
    return false;
  }
  const el = elements.find((e) => e.id === action.targetId);
  // Navigation links (<a>) and tabs are harmless read-only navigation
  if (el?.role === "link" || el?.role === "tab") {
    return false;
  }
  const labelText = `${action.label || ""} ${el?.text || ""} ${el?.placeholder || ""}`;
  const hasDestructiveKeyword =
    DESTRUCTIVE_KEYWORDS.test(labelText) || DESTRUCTIVE_KEYWORDS.test(transcript || "");
  if (hasDestructiveKeyword) {
    return destructiveScore >= T.destructive;
  }
  // Without destructive keywords, only trigger if model outputs a very high score (>= 0.85) on non-links
  return destructiveScore >= 0.85;
}

/** Top-N choice options by probability, excluding `none`. */
export function topChoices(choiceAnswer, n = 3) {
  if (!choiceAnswer?.probabilities) return [];
  return Object.entries(choiceAnswer.probabilities)
    .filter(([k]) => k !== "none")
    .sort((a, b) => b[1] - a[1])
    .slice(0, n)
    .map(([id, p]) => ({ id, p: r2(p) }));
}

function pickSpan(answer, minConfidence, fallback) {
  if (!answer) return fallback ?? null;
  if (answer.choice === "none") return null;
  if (answer.confidence < minConfidence) return fallback ?? answer.choice;
  return answer.choice;
}

function fillTemplate(tpl, q) {
  return tpl.replace("%s", encodeURIComponent(q));
}

/**
 * @param {object} p
 * @param {object} p.answers   Jev answers (intent, target, site, complete, is_command, destructive, scroll_amount, text_span?, url_span?, tab_direction)
 * @param {object} p.candidates {text: string[], url: string[]}
 * @param {object} p.snapshot  current page snapshot (elements, searchBoxId, site, url)
 * @param {number} p.silentMs  ms since the transcript last changed
 * @param {boolean} p.isFinal  the speech recognizer marked this utterance final
 * @param {object|null} p.pending  a pending destructive action awaiting "confirm"
 * @param {boolean} p.confirmed  user already said confirm for this action
 * @param {boolean} p.fromBox  typed into the command box + Enter: explicit addressing,
 *   so the `is_command` gate is bypassed (mic partials still pass it). Everything else applies.
 * @returns {{decision: string, action?: object, candidates?: Array, reasons: Array, summary: string}}
 */
export function evaluatePolicy({ answers, candidates, snapshot, silentMs = 0, isFinal = false, pending = null, context = null, fromBox = false, transcript = null }) {
  const reasons = [];
  const intent = answers.intent;
  let intentName = intent?.choice ?? "none";
  const spokenSite = detectSpokenSite(transcript);

  // Intent overrides from explicit verbs/keywords in transcript:
  if (transcript) {
    if (/^\s*(?:open\s+(?:a\s+)?new\s+tab|new\s+tab)\b/i.test(transcript)) {
      intentName = "open_new_tab";
    } else if (/^\s*(?:close\s+(?:this\s+)?tab|close\s+tab)\b/i.test(transcript)) {
      intentName = "close_tab";
    } else if (/^\s*(?:switch\s+tab|next\s+tab|previous\s+tab)\b/i.test(transcript)) {
      intentName = "switch_tab";
    } else if (/^\s*(?:reload|refresh(?:\s+the\s+page)?)\b/i.test(transcript)) {
      intentName = "reload";
    } else if (/^\s*(?:search|look\s+up|find|google)\b/i.test(transcript)) {
      intentName = "search_web";
    } else if (/^\s*(?:click|press|tap)\b/i.test(transcript)) {
      if (!spokenSite) {
        intentName = "click_element";
      }
    } else if (spokenSite) {
      intentName = "navigate_url";
    } else if (/^\s*(?:go\s+to|open|visit|take\s+me\s+to)\b/i.test(transcript)) {
      const phrase = extractTargetPhrase(transcript);
      if (phrase && snapshot?.elements && findElementByPhrase(snapshot.elements, phrase)) {
        intentName = "click_element";
      } else if (intentName === "none") {
        intentName = "navigate_url";
      }
    }
  }
  const lastAction = context?.recentActions?.length ? context.recentActions[context.recentActions.length - 1] : null;
  const correction = answers.is_correction?.noul ?? 0;
  // A correction only counts once the phrase is finished (or the user has gone quiet): a one-word
  // partial like "go" right after a scroll must not be read as "undo the scroll".
  const finishedPhrase = (answers.complete?.noul ?? 0) >= T.complete || silentMs >= SILENCE_COMPLETE_MS || isFinal;
  const CORRECTION_PATTERN = /\b(no|wait|wrong|undo|cancel|back|not\s+that|i\s+meant|oops)\b/i;
  const hasCorrectionWord = transcript ? CORRECTION_PATTERN.test(transcript) : true;
  const isCorrection = Boolean(lastAction) && correction >= T.correction && finishedPhrase && hasCorrectionWord;

  // 0. Confirm / cancel handling for a pending destructive action.
  if (pending) {
    const isExplicitConfirm = Boolean(transcript && /^\s*(?:confirm|yes|do\s+it|go\s+ahead|proceed|sure|yep|yeah|ok|okay)\b/i.test(transcript));
    const isExplicitCancel = Boolean(transcript && /^\s*(?:cancel|no|stop|never\s+mind|don't|abort)\b/i.test(transcript));
    if (isExplicitConfirm || (intentName === "confirm" && (intent?.confidence ?? 0) >= T.intentConfidence)) {
      check(reasons, "intent", isExplicitConfirm ? "confirm (spoken word)" : `confirm (${r2(intent.confidence)})`, T.intentConfidence, true, "pending action confirmed");
      return { decision: "act", action: { ...pending, confirmed: true }, reasons, summary: `confirmed: ${describe(pending)}` };
    }
    if (isExplicitCancel || (intentName === "cancel" && (intent?.confidence ?? 0) >= T.intentConfidence)) {
      check(reasons, "intent", isExplicitCancel ? "cancel (spoken word)" : `cancel (${r2(intent.confidence)})`, T.intentConfidence, true, "pending action cancelled");
      return { decision: "cancel", reasons, summary: "cancelled pending action" };
    }
  }

  // 1. Correction of the previous action ("no, not that one", "wrong link, undo"). A bare "no"
  // is a reaction to the browser, not a fresh imperative, so this runs before the is_command gate.
  // When the user rejects what just happened and names no new target, reverse it; when they do
  // name a new target ("no, the other one") fall through and exclude the previous target below.
  if (isCorrection) {
    check(reasons, "is_correction", correction, T.correction, true, `rejects previous action: ${describe(lastAction)}`);
    const confidentIntent = intentName !== "none" && (intent?.confidence ?? 0) >= T.intentConfidence;
    // "the other one" names a new element; "not that one" alone does not (target comes back `none`
    // or the element just acted on) — the latter is a plain reversal.
    const namesNewTarget =
      TARGET_INTENTS.has(intentName) && topChoices(answers.target, 2).some((c) => c.id !== lastAction.targetId && c.p >= T.targetTopProb);
    // A confident closed-set command ("go back", "scroll down", "open youtube") said after a scroll
    // or click is what it says, not a request to reverse the last action: fall through to normal
    // handling. Only an unconfident / target-less correction is treated as "undo that".
    const reverse = lastAction.type !== "go_back" && (!confidentIntent || (TARGET_INTENTS.has(intentName) && !namesNewTarget));
    if (reverse) {
      const reversal = reverseAction(lastAction);
      return { decision: "act", action: reversal, reasons, summary: `correction → ${describe(reversal)}` };
    }
  }

  // 1b. Is the user talking to the browser at all? (Skipped for box-typed commands or explicit search commands)
  const explicitSearch = Boolean(transcript && /^\s*(?:search|look\s+up|find|google)\b/i.test(transcript));
  const isCmd = fromBox || explicitSearch ? 1 : (answers.is_command?.noul ?? 0);
  if (!check(reasons, "is_command", isCmd, T.isCommand, isCmd >= T.isCommand, fromBox ? "typed in command box (gate bypassed)" : explicitSearch ? "explicit search command (gate bypassed)" : "user is addressing the browser")) {
    return { decision: "ignore", reasons, summary: "not a browser command" };
  }

  // 2. Is there a confident intent?
  const explicitTabOrNav = Boolean(transcript && /^\s*(?:open\s+(?:a\s+)?new\s+tab|new\s+tab|close\s+(?:this\s+)?tab|close\s+tab|switch\s+tab|next\s+tab|previous\s+tab|reload|refresh)\b/i.test(transcript));
  const conf = intent?.confidence ?? 0;
  const intentOk = explicitSearch || explicitTabOrNav || (intentName !== "none" && conf >= T.intentConfidence);
  check(reasons, "intent", `${intentName} (${r2(explicitTabOrNav ? 1 : conf)})`, T.intentConfidence, intentOk, "confident, non-none intent");
  if (!intentOk) return { decision: "wait", reasons, summary: intentName === "none" ? "no recognizable command yet" : "intent not confident yet" };

  // 3. Has the user finished the command? (silence or a final result also counts)
  const complete = answers.complete?.noul ?? 0;
  const silent = silentMs >= SILENCE_COMPLETE_MS || isFinal;
  const completeOk = complete >= T.complete || silent;
  check(
    reasons,
    "complete",
    complete,
    T.complete,
    completeOk,
    silent ? (isFinal ? "recognizer marked utterance final" : `silent for ${silentMs}ms`) : "command has verb + object",
  );
  if (!completeOk) return { decision: "wait", reasons, summary: "waiting for the rest of the command" };

  // 3b. Free-text payloads must be finished before they are copied verbatim.
  if (PAYLOAD_INTENTS.has(intentName)) {
    const payloadOk = isFinal || silentMs >= PAYLOAD_SILENCE_MS;
    check(
      reasons,
      "payload_final",
      isFinal ? "final" : `${silentMs}ms silence`,
      `final or ${PAYLOAD_SILENCE_MS}ms`,
      payloadOk,
      "free text (query / typed text) must be finished before it is copied",
    );
    if (!payloadOk) {
      return { decision: "wait", reasons, summary: "waiting for the end of the phrase (free text)", retryInMs: Math.max(50, PAYLOAD_SILENCE_MS - silentMs) };
    }
  }

  // 4. Build the concrete action (code owns URLs, templates and text; Jev only picked options).
  // On a correction that names a new target, the previous target is not an option ("the other one").
  const excludeTargetId = isCorrection && TARGET_INTENTS.has(intentName) ? lastAction.targetId ?? null : null;
  const built = buildAction({ intentName, answers, candidates, snapshot, reasons, excludeTargetId, transcript });
  if (built.decision !== "act") return { ...built, reasons };
  const action = built.action;

  // 5. Destructive? Only side-effecting element actions can be.
  const destructive = answers.destructive?.noul ?? 0;
  const canBeDestructive = ["click_element", "press_enter", "select_option"].includes(action.type);
  if (canBeDestructive) {
    const isDestructive = isDestructiveAction(action, snapshot?.elements ?? [], transcript, destructive);
    const safe = !isDestructive;
    check(reasons, "destructive", destructive, T.destructive, safe, safe ? "reversible action" : "needs spoken confirmation");
    if (!safe) {
      return { decision: "confirm", action, reasons, summary: `say "confirm" to ${describe(action)}` };
    }
  }

  return { decision: "act", action, reasons, summary: describe(action) };
}

/** The action that undoes `action` as far as a browser can: navigations/clicks → back; typing → clear; tabs → close/switch. */
export function reverseAction(action) {
  switch (action?.type) {
    case "type_into_field":
      return { type: "type_into_field", targetId: action.targetId, text: "", submit: false, label: `clear ${action.label || action.targetId}` };
    case "open_new_tab":
      return { type: "close_tab", label: "close the new tab" };
    case "close_tab":
      return { type: "go_back", label: "back (tab already closed)" };
    case "switch_tab":
      return { type: "switch_tab", direction: action.direction === "previous" ? "next" : "previous", label: "switch back" };
    case "scroll_down":
      return { type: "scroll_up", amount: action.amount || "page", label: "scroll back up" };
    case "scroll_up":
      return { type: "scroll_down", amount: action.amount || "page", label: "scroll back down" };
    default:
      return { type: "go_back", label: `undo ${describe(action)}` };
  }
}

function buildAction({ intentName, answers, candidates, snapshot, reasons, excludeTargetId = null, transcript = null }) {
  const site = answers.site?.choice ?? "none";
  const elements = snapshot?.elements ?? [];

  switch (intentName) {
    case "navigate_url": {
      const urlPick = pickSpan(answers.url_span, T.spanConfidence, candidates.url?.[0]);
      if (urlPick) {
        check(reasons, "url_span", urlPick, T.spanConfidence, true, "domain spoken verbatim");
        return { decision: "act", action: { type: "navigate_url", url: toHttpUrl(urlPick), label: urlPick } };
      }
      // Direct spoken site match from transcript (e.g., "go to youtube", "open wikipedia")
      const spokenSite = detectSpokenSite(transcript);
      if (spokenSite && SITE_HOME[spokenSite]) {
        check(reasons, "site", `${spokenSite} (from transcript)`, "-", true, "spoken site name");
        return { decision: "act", action: { type: "navigate_url", url: SITE_HOME[spokenSite], label: spokenSite } };
      }
      // If model's site choice is the current page (e.g. duckduckgo) but user didn't speak it, do not re-open the same page
      if (transcript && site && site === snapshot?.site && !spokenSite) {
        return { decision: "wait", summary: "where to? (no destination site recognised)" };
      }
      // If site is example_com but no URL candidate mentions example and text candidates exist, avoid blind example.com loop
      if (site === "example_com" && candidates.text?.length && !candidates.url?.some((u) => /example/i.test(u))) {
        return { decision: "wait", summary: "where to? (no site or domain recognised)" };
      }
      if (SITE_HOME[site]) {
        check(reasons, "site", `${site} (${r2(answers.site.confidence)})`, "-", true, "known site");
        return { decision: "act", action: { type: "navigate_url", url: SITE_HOME[site], label: site } };
      }
      check(reasons, "site", site, "known site or spoken domain", false, "no destination yet");
      return { decision: "wait", summary: "where to? (no site or domain recognised)" };
    }

    case "search_web": {
      let query = pickSpan(answers.text_span, T.spanConfidence, candidates.text?.[0]) || (transcript && candidates.text?.[0] ? candidates.text[0] : null);
      if (query) {
        query = query.replace(/^(?:search(?:\s+\w+)?\s+for|look\s+up|google)\s+/i, "").trim();
      }
      if (!query) {
        check(reasons, "text_span", "none", T.spanConfidence, false, "no query text yet");
        return { decision: "wait", summary: "search for what?" };
      }
      check(reasons, "text_span", query, T.spanConfidence, true, "query copied verbatim");
      const spokenSearchSite = detectSpokenSite(transcript);
      const targetSite = spokenSearchSite || (site !== "duckduckgo" && site !== "example_com" && site !== "none" ? site : null);
      if (targetSite && SITE_SEARCH[targetSite]) {
        return { decision: "act", action: { type: "navigate_url", url: fillTemplate(SITE_SEARCH[targetSite], query), label: `search ${targetSite}: ${query}`, query } };
      }
      if (snapshot?.searchBoxId && snapshot.site !== "blank" && snapshot.site !== "example") {
        return { decision: "act", action: { type: "type_into_field", targetId: snapshot.searchBoxId, text: query, submit: true, label: `search this site: ${query}` } };
      }
      return { decision: "act", action: { type: "navigate_url", url: fillTemplate(SITE_SEARCH[DEFAULT_SEARCH_ENGINE], query), label: `search: ${query}`, query } };
    }

    case "click_element":
    case "select_option":
    case "type_into_field": {
      const target = answers.target;
      let top = topChoices(target, T.candidateCount + 1);
      let chosen = target?.choice;
      let chosenP = target?.probabilities?.[chosen] ?? 0;
      if (excludeTargetId && chosen === excludeTargetId) {
        // "no, the other one": the element just acted on is ruled out; take the runner-up.
        top = top.filter((c) => c.id !== excludeTargetId);
        chosen = top[0]?.id ?? "none";
        chosenP = top[0]?.p ?? 0;
        check(reasons, "exclude_target", excludeTargetId, "-", true, `previous target excluded → ${chosen}`);
      }
      top = top.slice(0, T.candidateCount);
      let targetOk =
        chosen && chosen !== "none" && target.confidence >= T.targetConfidence && chosenP >= T.targetTopProb;
      const text = intentName === "click_element" ? null : pickSpan(answers.text_span, T.spanConfidence, candidates.text?.[0]);

      if (intentName !== "click_element" && !text) {
        check(reasons, "text_span", "none", T.spanConfidence, false, "no text to type yet");
        return { decision: "wait", summary: "type what?" };
      }

      // Check for direct spoken target phrase matching elements on screen
      if (elements.length && transcript) {
        const targetPhrase = extractTargetPhrase(transcript);
        if (targetPhrase) {
          const directMatch = findElementByPhrase(elements, targetPhrase);
          const chosenEl = elements.find((e) => e.id === chosen);
          const chosenMatches = chosenEl && elementMatchesPhrase(chosenEl, targetPhrase);
          if (directMatch && (!chosenMatches || !targetOk) && (!excludeTargetId || directMatch.id !== excludeTargetId)) {
            chosen = directMatch.id;
            targetOk = true;
            check(reasons, "target", `${chosen} (direct text match "${targetPhrase}")`, "-", true, elementLabel(elements, chosen));
            return { decision: "act", action: { type: intentName, targetId: chosen, text, label: elementLabel(elements, chosen) } };
          }
        }
      }

      if (targetOk) {
        check(reasons, "target", `${chosen} (${r2(target.confidence)})`, T.targetConfidence, true, elementLabel(elements, chosen));
        return { decision: "act", action: { type: intentName, targetId: chosen, text, label: elementLabel(elements, chosen) } };
      }

      // Typing with no confident target: fall back to the page's search box.
      if (intentName === "type_into_field" && snapshot?.searchBoxId) {
        check(reasons, "target", `${chosen} (${r2(target?.confidence ?? 0)})`, T.targetConfidence, false, "falling back to search box");
        return { decision: "act", action: { type: intentName, targetId: snapshot.searchBoxId, text, label: "search box" } };
      }

      check(reasons, "target", `${chosen ?? "none"} (${r2(target?.confidence ?? 0)})`, T.targetConfidence, false, "ambiguous target");
      // Show the plausible candidates (at least two when anything is plausible at all).
      let viable = top.filter((c) => c.p >= 0.08);
      if (viable.length === 1) viable = top.filter((c) => c.p >= 0.02).slice(0, 2);
      if (viable.length === 0) return { decision: "wait", summary: "no matching element on this page" };
      return {
        decision: "disambiguate",
        candidates: viable.map((c) => ({ ...c, label: elementLabel(elements, c.id) })),
        pendingIntent: { type: intentName, text },
        summary: `which one? ${viable.map((c, i) => `${i + 1}: ${elementLabel(elements, c.id)}`).join(" | ")}`,
      };
    }

    case "scroll_down":
    case "scroll_up": {
      const lvl = Math.round(answers.scroll_amount?.score ?? 1);
      const amount = ["little", "page", "end"][Math.min(2, Math.max(0, lvl))];
      check(reasons, "scroll_amount", answers.scroll_amount?.score ?? 1, "round", true, amount);
      return { decision: "act", action: { type: intentName, amount, label: `${intentName.replace("_", " ")} (${amount})` } };
    }

    case "switch_tab": {
      const dir = answers.tab_direction?.choice && answers.tab_direction.choice !== "none" ? answers.tab_direction.choice : "next";
      return { decision: "act", action: { type: "switch_tab", direction: dir, label: `switch tab (${dir})` } };
    }

    case "confirm":
    case "cancel":
      return { decision: "wait", summary: `nothing pending to ${intentName}` };

    default:
      return { decision: "act", action: { type: intentName, label: intentName.replace(/_/g, " ") } };
  }
}

export function elementLabel(elements, id) {
  const el = elements.find((e) => e.id === id);
  return el ? `${el.role} "${el.text || el.placeholder || ""}"` : id;
}

export function describe(action) {
  if (!action) return "";
  switch (action.type) {
    case "navigate_url":
      return `open ${action.label || action.url}`;
    case "type_into_field":
      return `type "${action.text}" into ${action.label || action.targetId}${action.submit ? " + enter" : ""}`;
    case "click_element":
      return `click ${action.label || action.targetId}`;
    case "select_option":
      return `select "${action.text}" in ${action.label || action.targetId}`;
    default:
      return action.label || action.type.replace(/_/g, " ");
  }
}

export { TARGET_INTENTS };
