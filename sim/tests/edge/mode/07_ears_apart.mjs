/* test_mode §9: THE EARS, APART. `sim/web/mode.js` as source on injected time: what the ears
 * report (`note({route: "ears", …})`, `noteTransportError("ears")`, as mic.js does) never moves
 * the brain's state, strikes or window; a transcribe 429 opens the EARS' window
 * (`canUseEars`, `earsRetryAfterS`) and nothing else; the brain's rules of §4 and §8 are
 * unchanged for what `/api/chat` reports. Before: one suppression window served every route,
 * so an STT 429 with Retry-After 60 paused typed chat for 60 s, and three refused uploads
 * degraded the brain.
 */
import { readFileSync, join, here, ok, eq, deep, FULL, probe } from "./harness.mjs";

const MODE_SRC = readFileSync(join(here, "web", "mode.js"), "utf8");
const HEALTH_LIVE = (await probe(FULL)).text;
const flush = async (n = 8) => { for (let i = 0; i < n; i++) await new Promise((r) => setImmediate(r)); };

/** mode.js under a stubbed window with a transport, booted LIVE, on injected time. */
async function bootLive() {
  const timers = [];
  let nextId = 1;
  let clock = 1_700_000_000_000;
  globalThis.location = { protocol: "https:", origin: "https://sim.test", hostname: "sim.test" };
  globalThis.document = { hidden: false, addEventListener() {}, getElementById: () => null, querySelector: () => null, body: null };
  globalThis.window = { moxieCloudTransport: true };
  globalThis.setTimeout = (fn, ms) => { const id = nextId++; timers.push({ id, fn, ms }); return id; };
  globalThis.clearTimeout = (id) => { const i = timers.findIndex((t) => t.id === id); if (i !== -1) timers.splice(i, 1); };
  Date.now = () => clock;
  globalThis.fetch = () => Promise.resolve({ status: 200, text: () => Promise.resolve(HEALTH_LIVE) });
  (0, eval)(MODE_SRC);
  await flush();
  const m = globalThis.window.moxieMode;
  eq(m.state(), "live", "booted live");
  return { m, timers, advance: (ms) => { clock += ms; } };
}
const ears = (h, reason, retry) => h.m.note({ route: "ears", reason, retry_after_s: retry || 0 });
const chat = (h, reason, retry) => h.m.note({ reason, retry_after_s: retry || 0 });
const brain = (h) => [h.m.state(), h.m.reason(), h.m.badge(), h.m.canSpendLiveTurn()];
/** The ears' members; null on a base without them (a counted red, not a crash). */
const earsOf = (h) => [typeof h.m.canUseEars === "function" ? h.m.canUseEars() : null,
                       typeof h.m.earsRetryAfterS === "function" ? h.m.earsRetryAfterS() : null,
                       typeof h.m.earsReason === "function" ? h.m.earsReason() : null];

// 9a. An STT 429 with Retry-After 60 (the route's answer for the gateway's STT cooldown):
//     the ears' window, not the brain's.
{
  const h = await bootLive();
  ears(h, "rate_limited", 60);
  deep(brain(h), ["live", null, "MOXIE ONLINE", true], "9a: an ears 429 leaves typed turns SPENDABLE, with no chip (it paused chat for 60 s)");
  deep(earsOf(h), [false, 60, "rate_limited"], "9a: …and opens the ears' own 60 s window");
  deep([h.m.snapshot().earsRetryAfterS, h.m.snapshot().earsReason, h.m.snapshot().liveTurns], [60, "rate_limited", true], "9a: the snapshot carries the ears' wait beside the brain's");
  h.advance(30_000);
  deep(earsOf(h).slice(0, 2), [false, 30], "9a: the ears' wait counts down");
  h.advance(30_001);
  deep(earsOf(h).slice(0, 2), [true, 0], "9a: …and lifts on time");
  ears(h, null, 0);
  eq(earsOf(h)[2], null, "9a: a clean transcript clears the ears' reason");
}

// 9b. A CHAT 429 keeps §4f/§8e's rule — and leaves the ears alone.
{
  const h = await bootLive();
  chat(h, "rate_limited", 60);
  deep([h.m.state(), h.m.canSpendLiveTurn(), h.m.retryAfterS(), h.m.message()], ["live", false, 60, "One at a time! Give Moxie a few seconds."],
       "9b: a chat 429 within the minute window still suppresses chat with the chip, unchanged");
  deep(earsOf(h).slice(0, 2), [true, 0], "9b: …and the ears may still take a clip");
  const r = await bootLive();
  chat(r, "rate_limited", 2390);
  deep([r.m.state(), r.m.badge(), earsOf(r)[0]], ["degraded", "HOSTED DEMO · RESTING", true], "9b: the chat hour cap RESTS the brain, as §8d pins, and does not hold the ears");
}

// 9c. Every reason that degrades the brain when `/api/chat` says it is only the ears' when
//     `/api/transcribe` says it: no transition, polling untouched, the reason recorded.
for (const reason of ["upstream_down", "timeout", "gateway_unreachable_or_gated", "budget_exhausted",
                      "turnstile_misconfigured", "gateway_not_configured", "forbidden_origin", "at_capacity"]) {
  const h = await bootLive();
  const polls = h.timers.length;
  ears(h, reason, reason === "at_capacity" ? 15 : 0);
  deep([brain(h), h.m.stats().transitions, h.timers.length, earsOf(h)[2]], [["live", null, "MOXIE ONLINE", true], ["boot->live"], polls, reason],
       `9c (${reason}): from the ears it is the ears' own: live, no transition, the poll untouched, the reason kept apart`);
  if (reason === "at_capacity") deep([h.m.load().level, earsOf(h)[1]], ["ok", 15], "9c (at_capacity): the ears wait 15 s; the brain's load is not painted BUSY");
  // …and from the brain, exactly what it did before.
  const b = await bootLive();
  chat(b, reason, reason === "at_capacity" ? 15 : 0);
  const want = reason === "forbidden_origin" ? "offline" : reason === "at_capacity" ? "live" : "degraded";
  eq(b.m.state(), want, `9c (${reason}): from /api/chat the brain still reads ${want}, unchanged`);
}

// 9d. Transport errors: the ears' are counted, never a strike; the brain's three still degrade.
{
  const h = await bootLive();
  for (let i = 0; i < 5; i++) h.m.noteTransportError("ears");
  deep([brain(h), h.m.stats().earsErrors, h.m.stats().transportErrors], [["live", null, "MOXIE ONLINE", true], 5, 0],
       "9d: five refused uploads in a row are five ears errors and no strike (three used to degrade the brain)");
  h.m.noteTransportError();
  h.m.noteTransportError();
  eq(h.m.state(), "live", "9d: two chat transport errors are still not a broken deployment");
  h.m.noteTransportError();
  deep([h.m.state(), h.m.reason(), h.m.stats().transportErrors], ["degraded", "upstream_down", 3], "9d: the third chat transport error degrades, as §4f pins");
}

// 9e. A clean TRANSCRIPT never says the brain is back: only a clean chat turn does.
{
  const h = await bootLive();
  chat(h, "upstream_down");
  eq(h.m.state(), "degraded", "9e: a chat turn said the brain is out");
  ears(h, null, 0);
  deep([h.m.state(), h.m.badge()], ["degraded", "HOSTED DEMO · SCRIPTED"], "9e: a clean transcript does NOT bring the brain back (it flipped the badge to MOXIE ONLINE)");
  chat(h, null, 0);
  deep([h.m.state(), h.m.badge()], ["live", "MOXIE ONLINE"], "9e: a clean chat turn does");
}

// 9f. The ears' windows when the route names no wait: a 429 with no Retry-After holds the
//     mic 10 s, `at_capacity` 15 s, any other reason not at all; a clean transcript lifts a
//     running window early. None of it touches the brain.
{
  const h = await bootLive();
  ears(h, "rate_limited", 0);
  deep(earsOf(h), [false, 10, "rate_limited"], "9f: an ears 429 with no Retry-After opens a 10 s window");
  ears(h, null, 0);
  deep(earsOf(h), [true, 0, null], "9f: a clean transcript lifts it (the ears are taking clips again)");
  ears(h, "at_capacity", 0);
  deep(earsOf(h), [false, 15, "at_capacity"], "9f: at_capacity with no wait holds the ears 15 s");
  h.advance(15_001);
  deep(earsOf(h).slice(0, 2), [true, 0], "9f: …and lifts on time");
  ears(h, "upstream_down", 0);
  deep(earsOf(h), [true, 0, "upstream_down"], "9f: a reason carrying no wait opens no window: the next tap may try");
  deep(brain(h), ["live", null, "MOXIE ONLINE", true], "9f: …and none of it touched the brain");
}
ok(true, "9: the ears' seams hold");
