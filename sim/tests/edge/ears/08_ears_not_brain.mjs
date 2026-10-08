/* Part C §B14–B16: THE EARS ARE NOT THE BRAIN. On the real `mode.js`, `mic.js` and
 * `cloud-transport.js` (harness Part C), with `Date.now` on the virtual clock: an ears-only
 * failure — a local upload the hosted CSP refuses, a transcribe refusal or failure — stays in
 * the ears' own status and never strikes the brain's mode; a real chat outage still degrades
 * as #318 pins. A transcribe 429 holds the MICROPHONE for as long as it said, and says how
 * long, while typed turns go on.
 *
 * Measured before (W2-S8's reviewer, on this rig): on a chat-only deployment (`DEMO_STT_MODEL`
 * unset, documented as optional) the third Listen tap, at any pace, degraded the whole page to
 * HOSTED DEMO · SCRIPTED with "Moxie's brain is unreachable right now"; one STT 429 with
 * Retry-After 60 paused typed chat for 60 s, and said "give Moxie a few seconds".
 */
import { bootPage, deep, eq, fails, ok, page } from "./harness.mjs";

const { advance, envelope, now } = page;
const M = () => globalThis.window.moxieMode;
const T = () => globalThis.window.moxieBridge.transportStats();
const chats = (w) => w.spy.fetches.filter(([p]) => p === "/api/chat").map(([, b]) => b.text);
/** The ears' members of mode.js; null where a base has none (a counted red, not a crash). */
const earsReason = () => (typeof M().earsReason === "function" ? M().earsReason() : null);
const canUseEars = () => (typeof M().canUseEars === "function" ? M().canUseEars() : null);
const earsWait = () => (typeof M().earsRetryAfterS === "function" ? M().earsRetryAfterS() : null);
const foldTransportFails = () => { for (const f of page.fails.splice(0)) fails.push("transport harness: " + f); };
/** mode.js and voice/ read the wall clock; here it is the virtual one. */
const T0 = 1_700_000_000_000;
Date.now = () => T0 + now();
const SIDECAR = /:8082\/stt$/;
const refusal = (reason, status, retry) => ({ status, json: envelope({ ok: false, degraded: true, reason, retry_after_s: retry || 0, mode: "live" }) });

/* =========================================================================== *
 * B14. A CHAT-ONLY DEPLOYMENT (health: ears false): a Listen tap uploads to the local
 *      sidecar's URL, which the hosted page cannot reach (the CSP refuses it: the fetch
 *      rejects). Four taps at a 45 s pace, and four at 90 s: the page STAYS LIVE. A real chat
 *      outage on the same page still degrades on the third transport error.
 * =========================================================================== */
for (const gapS of [45, 90]) {
  const p = await bootPage({ answer: (path) => {
    if (path === "/api/health") return { status: 200, json: envelope({ ears: false }) };
    if (SIDECAR.test(path)) return { reject: true };          // the CSP's refusal, as fetch reports it
    if (path === "/api/chat") return { reject: true };         // (B14b) the brain really unreachable
    return { status: 404, text: "" };
  } });
  const w = p.world;
  eq(p.mic.sttTarget().kind, "local", `B14 (${gapS} s): with no hosted ears the clip is bound for the local sidecar, as before`);
  eq(M().state(), "live", `B14 (${gapS} s): the page booted live`);
  for (let i = 0; i < 4; i++) {
    await p.speak();
    await advance(gapS * 1000);
  }
  const st = p.mic.stats();
  deep([st.posts, st.fallbacks, w.spy.fetches.filter(([pth]) => SIDECAR.test(pth)).length], [4, 4, 4],
       `B14 (${gapS} s): four uploads were attempted and refused, four scripted lines consoled the visitor`);
  deep([M().state(), M().badge(), M().canSpendLiveTurn()], ["live", "MOXIE ONLINE", true],
       `B14 (${gapS} s): FOUR REFUSED UPLOADS LEAVE THE BRAIN LIVE (the third tap used to read HOSTED DEMO · SCRIPTED, "brain unreachable")`);
  deep([M().stats().transportErrors, M().stats().earsErrors, M().stats().transitions], [0, 4, ["boot->live"]],
       `B14 (${gapS} s): recorded as four ears errors, no strike, no transition`);
  eq(earsReason(), "transport_error", `B14 (${gapS} s): the ears' own status says what happened`);

  // B14b. The brain really unreachable, on the same page: #318's three strikes, unchanged.
  for (const line of ["one", "two", "three"]) {
    globalThis.window.moxieTypedTurn.send(line);
    await advance(2000);
  }
  deep([chats(w).length, M().state(), M().reason(), M().badge()], [3, "degraded", "upstream_down", "HOSTED DEMO · SCRIPTED"],
       `B14b (${gapS} s): three chat transport errors still degrade the brain, as before`);
  eq(M().stats().transportErrors, 3, `B14b (${gapS} s): …counted apart from the ears' errors`);
  foldTransportFails();
}

/* =========================================================================== *
 * B15. TRANSCRIBE REFUSALS AND FAILURES ON A HOSTED PAGE stay the ears' own: the brain's
 *      state does not move, the next typed line goes out, and the ears' reason is recorded.
 * =========================================================================== */
for (const [label, answer] of [
  ["upstream_down", refusal("upstream_down", 503)],
  ["timeout", refusal("timeout", 504)],
  ["gateway_unreachable_or_gated", refusal("gateway_unreachable_or_gated", 503)],
  ["budget_exhausted", refusal("budget_exhausted", 503, 1800)],
  ["bad_request", refusal("bad_request", 400)],
  ["a network failure", { reject: true }],
  ["a bare 500", { status: 500, text: "boom" }],
]) {
  const p = await bootPage({ answer: (path) => {
    if (path === "/api/health") return { status: 200, json: envelope({ ears: true }) };
    if (path === "/api/transcribe") return answer;
    if (path === "/api/chat") return { status: 200, json: envelope({ messages: [], speech: [] }) };
    return { status: 404, text: "" };
  } });
  const w = p.world;
  await p.speak();
  await advance(1000);
  eq(p.mic.stats().fallbacks, 1, `B15 (${label}): the visitor is consoled with a scripted line`);
  deep([M().state(), M().canSpendLiveTurn(), M().stats().transitions], ["live", true, ["boot->live"]],
       `B15 (${label}): THE BRAIN STAYS LIVE AND SPENDABLE — an ears failure is not a brain failure`);
  globalThis.window.moxieTypedTurn.send("still typing");
  await advance(500);
  deep(chats(w), ["still typing"], `B15 (${label}): …and the next typed line goes to the brain`);
  ok(earsReason() !== null, `B15 (${label}): the ears recorded their own reason (${earsReason()})`);
  foldTransportFails();
}

/* =========================================================================== *
 * B16. A TRANSCRIBE 429 IS THE EARS' WINDOW, NOT THE BRAIN'S: with Retry-After 60 (what the
 *      route answers for the gateway's STT cooldown) typed chat keeps going, the mic waits
 *      60 s and its status says "about a minute" (it said "a few seconds"); the hour cap says
 *      its minutes. A CHAT 429 still pauses chat, as #318 pins, and leaves the ears alone.
 * =========================================================================== */
{
  let wait = 60;
  const p = await bootPage({ answer: (path, body) => {
    if (path === "/api/health") return { status: 200, json: envelope({ ears: true }) };
    if (path === "/api/transcribe") return refusal("rate_limited", 429, wait);
    if (path === "/api/chat") {
      if (body.text === "chat capped") return refusal("rate_limited", 429, 60);
      return { status: 200, json: envelope({ messages: [], speech: [] }) };
    }
    return { status: 404, text: "" };
  } });
  const w = p.world;
  await p.speak();
  await advance(100);
  ok(/^Moxie’s ears need a rest — back in about a minute/.test(p.micStatus()),
     `B16: the status says how long the ears rest (got "${p.micStatus()}")`);
  deep([M().state(), M().canSpendLiveTurn(), M().message(), canUseEars(), earsWait()], ["live", true, "", false, 60],
       "B16: TYPED TURNS ARE NOT PAUSED by the ears' 429 (no chip either); the ears' own window is 60 s");
  globalThis.window.moxieTypedTurn.send("typed while the ears rest");
  await advance(500);
  deep(chats(w), ["typed while the ears rest"], "B16: …a typed line goes out at once (it used to wait 60 s)");

  await advance(10_000);
  await p.mic.toggle();
  await advance(50);
  deep([p.mic.isRecording(), p.mic.stats().starts, p.mic.stats().restedTaps], [false, 1, 1],
       "B16: a tap inside the ears' window opens no microphone (nothing to record only to be refused)");
  ok(/back in about a minute$/.test(p.micStatus()), `B16: …and says how long (got "${p.micStatus()}")`);
  await advance(50_000);                                         // 61 s after the 429
  await p.mic.toggle();
  await advance(50);
  eq(p.mic.isRecording(), true, "B16: once the window lifts, a tap records again");

  // The hour cap's wording (this clip is refused with its wait), and the minute window's.
  wait = 2390;
  p.mic.toggle();
  await advance(100);
  ok(/back in about 40 minutes/.test(p.micStatus()), `B16: the hour cap says its minutes (got "${p.micStatus()}")`);
  eq(earsWait(), 2390, "B16: …and the ears' window is that long");
  await advance(2391_000);
  wait = 7;
  await p.speak();
  await advance(100);
  ok(/^one at a time — give Moxie a few seconds/.test(p.micStatus()), `B16: the minute window is still "a few seconds" (got "${p.micStatus()}")`);
  deep([M().state(), M().canSpendLiveTurn()], ["live", true], "B16: through all of it the brain stayed live and spendable");

  // B16b. A CHAT 429 still pauses chat (#318), and leaves the ears alone.
  await advance(10_000);
  globalThis.window.moxieTypedTurn.send("chat capped");
  await advance(1000);
  deep([M().state(), M().canSpendLiveTurn(), M().message(), canUseEars()],
       ["live", false, "One at a time! Give Moxie a few seconds.", true],
       "B16b: a chat 429 pauses typed turns with the chip, as before — and the ears are not held by it");
  foldTransportFails();
}
