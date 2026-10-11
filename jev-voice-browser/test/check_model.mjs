import { decide } from "../src/jev.js";
import { evaluatePolicy } from "../src/policy.js";

async function run() {
  const phrases = [
    "go to wikipedia",
    "search for alan turing",
    "scroll down a bit",
    "open example dot com",
    "click the more information link"
  ];

  for (const phrase of phrases) {
    console.log(`\n========================================`);
    console.log(`Testing Phrase: "${phrase}"`);
    console.log(`========================================`);
    const snapshot = {
      url: "https://example.com",
      title: "Example Domain",
      site: "example",
      elements: [
        { id: "e01", role: "link", text: "More information...", href: "iana.org" },
        { id: "e02", role: "combobox", text: "Search", placeholder: "Search here" }
      ]
    };

    const res = await decide({ transcript: phrase, snapshot });
    console.log(`Latency: ${res.latencyMs}ms`);
    console.log(`Raw Answers:`);
    console.log(`  intent: ${res.answers.intent?.choice} (confidence: ${res.answers.intent?.confidence?.toFixed(3)})`);
    console.log(`  is_command: ${res.answers.is_command?.noul?.toFixed(3)}`);
    console.log(`  complete: ${res.answers.complete?.noul?.toFixed(3)}`);
    console.log(`  target: ${res.answers.target?.choice} (confidence: ${res.answers.target?.confidence?.toFixed(3)})`);
    console.log(`  site: ${res.answers.site?.choice} (confidence: ${res.answers.site?.confidence?.toFixed(3)})`);
    if (res.answers.text_span) {
      console.log(`  text_span: ${res.answers.text_span?.choice}`);
    }
    if (res.answers.url_span) {
      console.log(`  url_span: ${res.answers.url_span?.choice}`);
    }

    const policy = evaluatePolicy({
      answers: res.answers,
      candidates: res.candidates,
      snapshot,
      silentMs: 1000,
      isFinal: true
    });

    console.log(`Policy Result:`);
    console.log(`  Decision: ${policy.decision.toUpperCase()}`);
    console.log(`  Summary: ${policy.summary}`);
    console.log(`  Action:`, policy.action ? JSON.stringify(policy.action) : "None");
    console.log(`  Gate Checks:`);
    for (const r of policy.reasons) {
      console.log(`    - [${r.pass ? "PASS" : "FAIL"}] ${r.name}: ${r.value} (threshold: ${r.threshold}) -> ${r.note}`);
    }
  }
}

run().catch(console.error);
