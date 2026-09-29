/* test_mode §7: the paid grounding probe's one attempt counter, with zero network calls. */
import { spawnSync } from "node:child_process";
import { ProbeBudget, ProbeBudgetError } from "../../../tools/probe_budget.mjs";
import { passageEvidence } from "../../../tools/grounding_score.mjs";
import {
  readFileSync, join, here, ok, eq, deep,
} from "./harness.mjs";

const GROUNDING_SRC = readFileSync(join(here, "tools", "grounding_probe.mjs"), "utf8");

// 7. The paid grounding gate has one counter across all logical calls and retries.
//    Everything here is injected or refused before dotenv loading: zero network calls.
{
  deep(passageEvidence(
    "I use MQTT messages.", "I talk online.", "How do you talk to the cloud?",
    "The robot uses MQTT messages for each turn.").fromPassage,
    ["mqtt", "messages"],
    "grounding scorer fires when A adds terms from the supplied passage");
  deep(passageEvidence(
    "I wave brightly.", "I talk online.", "How do you talk to the cloud?",
    "The robot uses MQTT messages for each turn.").fromPassage,
    [],
    "grounding scorer stays clear when A adds nothing from the candidate passage");
  deep(passageEvidence(
    "That is which way I talk.", "I talk online.", "How do you talk to the cloud?",
    "The link which carries each turn is encrypted.").fromPassage,
    ["which"],
    "withheld-passage control can expose the acknowledged common-word false positive");
  ok(/docsA:\s*null[\s\S]*docsB:\s*null[\s\S]*control:\s*true/.test(GROUNDING_SRC),
     "live negative arm withholds the passage from both identical prompts");
  ok(/passageEvidence\([\s\S]*candidateDocs\.excerpt/.test(GROUNDING_SRC),
     "live negative arm still scores against a non-empty withheld production passage");

  eq((GROUNDING_SRC.match(/budget\.requestText\s*\(/g) || []).length, 1,
     "the grounding caller has exactly one outbound seam, inside ProbeBudget");
  ok(!/\bfetch\s*\(/.test(GROUNDING_SRC),
     "the grounding caller has no direct fetch that can bypass attempt accounting");
  ok(!/\bres\.(?:text|json)\s*\(/.test(GROUNDING_SRC),
     "the grounding caller cannot consume a body after ProbeBudget clears its deadline");

  // The executable refuses before loading credentials unless both bounds are explicit.
  for (const [args, expected] of [
    [["--yes"], "--max-attempts must be an integer"],
    [["--yes", "--max-attempts", "6"], "--timeout-ms must be an integer"],
    [["--yes", "--max-attempts", "7", "--timeout-ms", "20000"],
     "--max-attempts must be an integer from 4 to 6"],
  ]) {
    const run = spawnSync(process.execPath, [join(here, "tools", "grounding_probe.mjs"), ...args], {
      encoding: "utf8", env: { PATH: process.env.PATH || "" }, timeout: 2000,
    });
    eq(run.status, 2, `unsafe grounding CLI shape refuses: ${args.join(" ")}`);
    ok(run.stderr.includes(expected), `grounding CLI names its missing/invalid bound: ${expected}`);
  }

  for (const config of [
    { maxAttempts: 0, timeoutMs: 10 },
    { maxAttempts: 2.5, timeoutMs: 10 },
    { maxAttempts: 2, timeoutMs: 0 },
  ]) {
    let refused = false;
    try { new ProbeBudget(config); } catch (err) { refused = err instanceof TypeError; }
    ok(refused, `invalid probe budget refuses before fetch: ${JSON.stringify(config)}`);
  }

  // Four retry-shaped calls reach the boundary; a fifth is refused without calling fetch.
  const budget = new ProbeBudget({ maxAttempts: 4, timeoutMs: 100 });
  let outbound = 0;
  const fakeFetch = async (_url, options) => {
    outbound += 1;
    ok(options.signal instanceof AbortSignal, "every probe attempt receives an abort signal");
    return new Response("busy", { status: 503 });
  };
  for (let i = 0; i < 4; i += 1)
    await budget.requestText("https://gateway.invalid.test", {}, fakeFetch);
  let exhausted = false;
  try { await budget.requestText("https://gateway.invalid.test", {}, fakeFetch); }
  catch (err) { exhausted = err instanceof ProbeBudgetError && /exhausted/.test(err.message); }
  ok(exhausted, "the first attempt beyond the shared ceiling refuses loudly");
  eq(outbound, 4, "retry-shaped calls cannot amplify past the actual-attempt ceiling");
  eq(budget.attempts, 4, "failed HTTP responses still count as actual attempts");
  eq(budget.summary(), "4/4", "the operator summary reports used/allowed attempts");

  // A hung dependency settles by the deadline and consumes exactly one attempt.
  const timed = new ProbeBudget({ maxAttempts: 2, timeoutMs: 20 });
  let hungCalls = 0;
  const hangingFetch = (_url, { signal }) => new Promise((_resolve, reject) => {
    hungCalls += 1;
    signal.addEventListener("abort", () => reject(new Error("aborted by test signal")), { once: true });
  });
  let timedOut = false;
  try { await timed.requestText("https://gateway.invalid.test", {}, hangingFetch); }
  catch (err) { timedOut = err instanceof ProbeBudgetError && /timed out after 20 ms/.test(err.message); }
  ok(timedOut, "a hung grounding attempt aborts with an operator-readable deadline");
  eq(hungCalls, 1, "the timeout path starts one actual attempt");
  eq(timed.attempts, 1, "the timed-out attempt remains charged");

  // Real loopback HTTP in a fresh process (the fixtures above replace fetch/timers): a Fetch
  // double cannot reveal redirect follow-ups or a body that completes after Fetch resolves.
  const loopback = spawnSync(process.execPath,
    [join(here, "tests", "helpers_probe_budget_loopback.mjs")],
    { encoding: "utf8", env: { PATH: process.env.PATH || "" }, timeout: 3000 });
  eq(loopback.status, 0,
     `loopback grounding transport proof exits cleanly — ${loopback.stderr.slice(0, 200)}`);
  let proof = null;
  try { proof = JSON.parse(loopback.stdout); } catch { /* asserted below */ }
  ok(!!proof, "loopback grounding transport proof returns structured evidence");
  if (proof) {
    deep(proof.redirect, { status: 307, requests: 1, count: "1/1" },
         "one counted redirect attempt emits exactly one HTTP request");
    deep(proof.success, { timedOut: true, requests: 1, count: "1/1" },
         "a delayed success body remains inside the attempt deadline");
    deep(proof.error, { timedOut: true, requests: 1, count: "1/1" },
         "a delayed error body remains inside the attempt deadline");
    deep(proof.ordinary, { body: '{"done":true}', requests: 1, count: "1/1" },
         "an ordinary complete response still returns its body and count");
  }
}
