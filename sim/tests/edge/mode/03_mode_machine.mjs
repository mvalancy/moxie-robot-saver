/* test_mode §4: `sim/web/mode.js` loaded as source under a stubbed window/document/fetch, on
 * injected time and timers — assertions are on recorded state, never a live timer. */
import {
  readFileSync, join, here, ok, eq, deep, env2, lib, limits, FULL, probe,
} from "./harness.mjs";

const MODE_SRC = readFileSync(join(here, "web", "mode.js"), "utf8");

const HEALTH_BARE = (await probe({})).text;
const HEALTH_LIVE = (await probe(FULL)).text;
const envelopeText = (over) => JSON.stringify(env2.envelope(over));

const flush = async (n = 8) => { for (let i = 0; i < n; i++) await new Promise((r) => setImmediate(r)); };

/** Load mode.js under a stubbed browser; `replies` are consumed one per fetch, the last repeating. */
function boot(opts) {
  const o = opts || {};
  const timers = [];
  const listeners = {};
  let nextId = 1;
  let clock = 1_700_000_000_000;
  const fetches = [];
  const replies = (o.replies || []).slice();
  let last = replies.length ? replies[replies.length - 1] : { status: 404, body: "" };

  globalThis.location = { protocol: o.protocol || "http:", origin: "http://sim.test", hostname: "sim.test" };
  globalThis.document = {
    hidden: !!o.hidden,
    addEventListener: (ev, cb) => { (listeners[ev] = listeners[ev] || []).push(cb); },
    getElementById: () => null,
    querySelector: () => null,
    body: null,
  };
  globalThis.window = {};
  if (o.transport) globalThis.window.moxieCloudTransport = true;
  globalThis.setTimeout = (fn, ms) => { const id = nextId++; timers.push({ id, fn, ms }); return id; };
  globalThis.clearTimeout = (id) => {
    const i = timers.findIndex((t) => t.id === id);
    if (i !== -1) timers.splice(i, 1);
  };
  Date.now = () => clock;
  globalThis.fetch = (url, opt) => {
    fetches.push(String(url));
    const r = replies.length ? replies.shift() : last;
    if (r.throws) return Promise.reject(new Error("network"));
    return Promise.resolve({ status: r.status, text: () => Promise.resolve(r.body) });
  };
  (0, eval)(MODE_SRC);
  const m = globalThis.window.moxieMode;
  return {
    m,
    fetches,
    timers,
    url: () => fetches[fetches.length - 1] || "",
    /** Fire the one pending timer, the way a real 30 s wait would. */
    async fire() {
      const t = timers.shift();
      if (!t) return false;
      t.fn();
      await flush();
      return true;
    },
    advance(ms) { clock += ms; },
    setHidden(v) { globalThis.document.hidden = v; },
    async visibility() {
      for (const cb of listeners.visibilitychange || []) cb();
      await flush();
    },
  };
}

/** A page with a transport that booted LIVE — the starting point of most transitions. */
async function bootLive() {
  const h = boot({ transport: true, replies: [{ status: 200, body: HEALTH_LIVE }] });
  await flush();
  return h;
}

// 4a. boot -> degraded with nothing configured, and then NOTHING further happens.
{
  const h = boot({ replies: [{ status: 200, body: HEALTH_BARE }] });
  await flush();
  eq(h.m.state(), "degraded", "an unconfigured deployment reads as degraded, not offline");
  eq(h.m.reason(), "gateway_not_configured", "...for the honest reason");
  eq(h.m.badge(), "HOSTED DEMO", "§7: the not-configured row keeps today's badge, unchanged");
  eq(h.m.message(), "", "...and today's copy, which env.js owns");
  eq(h.m.stats().polls, 1, "exactly ONE request is fired");
  eq(h.timers.length, 0, "§4.5: gateway_not_configured is sticky for the session — no poll storm");
  ok(/^http:\/\/sim\.test\/api\/health$/.test(h.url()), `the probe is same-origin (got ${h.url()})`);
  eq(h.m.canSpendLiveTurn(), false, "nothing may be spent when nothing is configured");
  eq(h.m.voice(), false, "no voice");
  eq(h.m.ears(), false, "no ears");
  deep(h.m.limits(), { max_input_chars: 500, max_tts_chars: 300, max_tokens: 160, chat_per_min: 5,
                       max_record_ms: 15000, max_audio_bytes: 500000, min_audio_bytes: 2000 },
       "the caps the server sent are kept");
}

// 4b. boot -> offline: the route is absent. Byte-identical to today, and never polls again.
for (const status of [404, 405, 501]) {
  const h = boot({ replies: [{ status, body: "not found" }] });
  await flush();
  eq(h.m.state(), "offline", `a ${status} means the ROUTE IS ABSENT => offline`);
  eq(h.m.badge(), "HOSTED DEMO", `a ${status} keeps today's badge`);
  eq(h.m.message(), "", `a ${status} keeps today's copy`);
  eq(h.timers.length, 0, `a ${status} schedules nothing — offline never polls again this session`);
}
{
  const h = boot({ replies: [{ throws: true }] });
  await flush();
  eq(h.m.state(), "offline", "a network error at boot => offline");
  eq(h.timers.length, 0, "...and no polling");
}
// A malformed or wrong-shaped 200 must leave the page SAFE, not throw and not be believed.
for (const body of ["<!doctype html><html>index</html>", "", "null", "[1,2,3]",
                    '{"ok":true}', '{"mode":"banana"}']) {
  const h = boot({ replies: [{ status: 200, body }] });
  await flush();
  eq(h.m.state(), "offline", `a 200 with ${JSON.stringify(body.slice(0, 24))} must not be believed`);
  eq(h.m.badge(), "HOSTED DEMO", "...and the page stays exactly today's");
}
// A 5xx carrying a real envelope IS believed — the route exists and answered honestly.
{
  const h = boot({ replies: [{ status: 503, body: envelopeText({ reason: "upstream_down", mode: "degraded" }) }] });
  await flush();
  eq(h.m.state(), "degraded", "a 503 with a real envelope is a degraded deployment, not an absent route");
  eq(h.m.reason(), "upstream_down", "...and the reason is carried");
}
// file:// — there cannot be a same-origin API, so do not even try.
{
  const h = boot({ protocol: "file:", replies: [{ status: 200, body: HEALTH_BARE }] });
  await flush();
  eq(h.m.state(), "offline", "file:// => offline");
  eq(h.m.apiBase(), null, "file:// has no API base");
  eq(h.m.stats().polls, 0, "file:// fires no request at all");
}

// 4c. boot -> live, and the honesty guard about the transport that P0-b brings.
{
  const h = boot({ replies: [{ status: 200, body: HEALTH_LIVE }] });
  await flush();
  eq(h.m.state(), "live", "a configured deployment reads as live");
  eq(h.m.reason(), null, "live carries no reason");
  eq(h.m.voice(), true, "the live probe reported a voice");
  eq(h.m.ears(), true, "the live probe reported ears");
  eq(h.m.hasTransport(), false, "P0-a ships no live transport");
  eq(h.m.badge(), "HOSTED DEMO · SCRIPTED",
     "a live mode with no transport must NOT paint LIVE — that is the dishonesty being removed");
  ok(/no live transport/.test(h.m.message()), `...and it says why (got "${h.m.message()}")`);
  eq(h.m.canSpendLiveTurn(), false, "no transport => nothing is spendable");
  deep(h.m.stats().scheduled, [30000], "§6.3: the poll floor is 30 s");
}
{
  const h = await bootLive();
  eq(h.m.badge(), "MOXIE ONLINE", "with a transport loaded, live paints LIVE");
  eq(h.m.message(), "", "§7: the ok/live row has no copy");
  eq(h.m.canSpendLiveTurn(), true, "live + transport => turns are spendable");
}

// 4d. §7's capacity signal, from the numbers the server sent.
for (const [inflight, capacity, badge, snippet] of [
  [0, 4, "MOXIE ONLINE", ""],
  [3, 4, "HOSTED DEMO · BUSY", "a few other people"],
  [4, 4, "HOSTED DEMO · BUSY", "hands full"],
]) {
  const body = envelopeText({ mode: "live", load: { inflight, capacity } });
  const h = boot({ transport: true, replies: [{ status: 200, body }] });
  await flush();
  eq(h.m.badge(), badge, `${inflight}/${capacity} must read ${badge}`);
  eq(h.m.load().inflight, inflight, "inflight is reported as a plain number");
  eq(h.m.load().capacity, capacity, "capacity is reported as a plain number");
  if (snippet) ok(h.m.message().includes(snippet),
                  `${inflight}/${capacity} copy should mention "${snippet}" (got "${h.m.message()}")`);
  else eq(h.m.message(), "", "an idle live deployment says nothing");
}

// 4e. §7's degrade rows.
for (const [reason, badge, snippet] of [
  // No wait in this envelope: the plain line, which no longer says "today's" (W4-S6).
  ["budget_exhausted", "HOSTED DEMO · SCRIPTED", "out of demo budget for now"],
  ["upstream_down", "HOSTED DEMO · SCRIPTED", "unreachable"],
  ["timeout", "HOSTED DEMO · SCRIPTED", "unreachable"],
  ["gateway_not_configured", "HOSTED DEMO", ""],
]) {
  const h = boot({ transport: true, replies: [{ status: 503, body: envelopeText({ reason, mode: "degraded" }) }] });
  await flush();
  eq(h.m.state(), "degraded", `${reason} => degraded`);
  eq(h.m.badge(), badge, `${reason} badge`);
  if (snippet) ok(h.m.message().includes(snippet), `${reason} copy (got "${h.m.message()}")`);
  else eq(h.m.message(), "", `${reason} keeps today's copy`);
}

// 4e'. W4-S6. A spent budget says when she is back, from ITS retry_after_s: the unit budget
//      can be the hour's, and "today's demo budget" promised a day. Hours, never "tomorrow"
//      (the day resets at midnight UTC, this afternoon for a visitor west of it).
for (const [retry, want] of [[1020, "back in about 17 minutes"], [60, "back in about a minute"],
                             [5 * 3600, "back in about 5 hours"]]) {
  const body = envelopeText({ reason: "budget_exhausted", mode: "degraded", retry_after_s: retry });
  const h = boot({ transport: true, replies: [{ status: 503, body }] });
  await flush();
  ok(h.m.message().includes(want), `budget spent, retry_after_s ${retry}: says "${want}" (got "${h.m.message()}")`);
  ok(!/today|tomorrow/.test(h.m.message()), `…and promises no day (got "${h.m.message()}")`);
}
{
  const h = await bootLive();
  h.m.note({ status: 503, reason: "budget_exhausted", retry_after_s: 1020 });
  ok(h.m.message().includes("back in about 17 minutes"),
     `a TURN's budget refusal carries its wait the same way (got "${h.m.message()}")`);
}
// THE PARITY PIN (W4-S6): the two closed reason lists are ONE list. envelope.js coerces a
// reason it does not know to bad_request; mode.js coerces one to null, and a refused turn
// with a null reason reads as a clean one (note(): strikes cleared, the page live). So a
// reason added on one side only is a refusal the page calls healthy.
{
  const m = MODE_SRC.match(/var REASONS = \[([\s\S]*?)\];/);
  const client = m ? [...m[1].matchAll(/"(\w+)"/g)].map((x) => x[1]).sort() : [];
  ok(client.length > 0, "mode.js REASONS parsed (the pin must not pass on an empty list)");
  deep(client, [...env2.REASONS].sort(), "mode.js REASONS is exactly envelope.js REASONS");
}

// 4f. What the transport reports back (§4.5), and the live -> degraded transitions (§6.3).
{
  const h = await bootLive();
  h.m.note({ status: 503, reason: "budget_exhausted", retry_after_s: 90 });
  eq(h.m.state(), "degraded", "budget_exhausted degrades at once");
  eq(h.m.badge(), "HOSTED DEMO · SCRIPTED", "...with §7's scripted badge");
  eq(h.timers.length, 1, "...and keeps polling, so recovery is automatic");
  eq(h.m.stats().lastDelayMs, 90000, "...on the server's own Retry-After");
}
{
  const h = await bootLive();
  h.m.note({ status: 503, reason: "upstream_down", retry_after_s: 60 });
  eq(h.m.state(), "degraded", "upstream_down degrades at once");
  eq(h.m.stats().lastDelayMs, 60000, "...and re-polls on Retry-After");
}
// 429 is a SOFT degrade: the mode stays live but nothing is spent until Retry-After passes.
{
  const h = await bootLive();
  h.m.note({ status: 429, reason: "rate_limited", retry_after_s: 7 });
  eq(h.m.state(), "live", "§6.3: a 429 does NOT leave live");
  eq(h.m.canSpendLiveTurn(), false, "...but suppresses live turns");
  eq(h.m.retryAfterS(), 7, "...for the Retry-After the server sent");
  eq(h.m.badge(), "MOXIE ONLINE", "§7: the badge stays LIVE");
  ok(/One at a time/.test(h.m.message()), `...with the transient chip (got "${h.m.message()}")`);
  h.advance(7001);
  eq(h.m.canSpendLiveTurn(), true, "live turns resume once the window has passed");
  eq(h.m.retryAfterS(), 0, "...and the countdown is spent");
}
// at_capacity is a LOAD signal, not a broken deployment (§7's BUSY badge in the live row).
{
  const h = await bootLive();
  h.m.note({ status: 503, reason: "at_capacity", retry_after_s: 15 });
  eq(h.m.state(), "live", "at_capacity keeps the deployment live");
  eq(h.m.badge(), "HOSTED DEMO · BUSY", "...and shows BUSY");
  ok(/hands full/.test(h.m.message()), `...with §7's copy (got "${h.m.message()}")`);
  eq(h.m.canSpendLiveTurn(), false, "...and spends nothing until a slot opens");
  eq(h.m.stats().lastDelayMs, 15000, "...re-polling after Retry-After");
}
// 403 is treated as offline (§4.5's last row).
{
  const h = await bootLive();
  h.m.note({ status: 403, reason: "forbidden_origin" });
  eq(h.m.state(), "offline", "forbidden_origin is treated as offline");
  eq(h.timers.length, 0, "...and stops polling");
}
// The 400 family never changes the mode — it is an input outcome, not a deployment one.
for (const reason of ["bad_request", "too_long", "too_short", "bad_ticket", "blocked"]) {
  const h = await bootLive();
  h.m.note({ status: 400, reason });
  eq(h.m.state(), "live", `${reason} must not change the mode`);
  eq(h.m.canSpendLiveTurn(), true, `${reason} must not stop the next turn`);
}
// Three consecutive transport errors, and not two (§6.3).
{
  const h = await bootLive();
  h.m.noteTransportError();
  eq(h.m.state(), "live", "one transport error is not a broken deployment");
  h.m.noteTransportError();
  eq(h.m.state(), "live", "two transport errors are still not");
  h.m.noteTransportError();
  eq(h.m.state(), "degraded", "three consecutive transport errors degrade");
  eq(h.m.reason(), "upstream_down", "...as upstream_down");
  eq(h.m.stats().transportErrors, 3, "the strikes are recorded, not inferred");
}
// A 504 timeout degrades at ONCE (§4.5): a hung gateway makes every turn wait out the server's
// whole deadline, and the 3-strike count it used to join never added up at a human pace,
// because each poll between two turns reset it (§8 replays that on a clock).
{
  const h = await bootLive();
  h.m.note({ status: 504, reason: "timeout" });
  eq(h.m.state(), "degraded", "the FIRST timeout degrades");
  eq(h.m.reason(), "timeout", "...as timeout");
  eq(h.m.badge(), "HOSTED DEMO · SCRIPTED", "...with §7's scripted badge");
  eq(h.m.stats().lastDelayMs, 60000, "...and the poll that may allow a trial turn waits 60 s");
}
// A clean turn after a degrade recovers without waiting for a poll.
{
  const h = await bootLive();
  h.m.note({ status: 503, reason: "upstream_down" });
  eq(h.m.state(), "degraded", "a turn reported the brain out");
  h.m.note({ status: 200, reason: null });
  eq(h.m.state(), "live", "a clean turn recovers to live");
  eq(h.m.badge(), "MOXIE ONLINE", "...and the badge flips back (§6.3, recovery is visible)");
}
// ...but a not-configured deployment never "recovers" on a stray note: it is sticky.
{
  const h = boot({ transport: true, replies: [{ status: 200, body: HEALTH_BARE }] });
  await flush();
  h.m.note({ status: 200, reason: null });
  eq(h.m.state(), "degraded", "gateway_not_configured is sticky for the session");
}

// 4g. degraded -> live, and the 30 s -> 5 min backoff ladder. The replies are ones
// /api/health CAN send: always 200, and live, not configured or budget spent (§8 checks the
// handler) — it never sees the gateway.
{
  // What the probe saw itself, the probe may clear: a spent budget, then a fresh window.
  limits.__reset();
  limits.__exhaustBudget(lib.readConfig(FULL));
  const spent = (await probe(FULL)).text;
  limits.__reset();
  const h = boot({ transport: true,
                   replies: [{ status: 200, body: spent }, { status: 200, body: HEALTH_LIVE }] });
  await flush();
  deep([h.m.state(), h.m.reason()], ["degraded", "budget_exhausted"], "boot lands degraded: the probe saw the budget spent");
  await h.fire();
  eq(h.m.state(), "live", "§6.3: a health poll returning live recovers, on its own");
  eq(h.m.badge(), "MOXIE ONLINE", "...visibly");
  eq(h.m.stats().polls, 2, "two polls happened, and that is recorded");
}
{
  // What a TURN saw, a poll cannot clear: it lets the next turn try (a trial turn) instead.
  const h = await bootLive();
  h.m.note({ status: 503, reason: "upstream_down" });
  await h.fire();
  eq(h.m.stats().polls, 2, "the poll ran, and answered live");
  deep([h.m.state(), h.m.badge()], ["degraded", "HOSTED DEMO · SCRIPTED"],
       "a poll answering live does NOT undo what a turn saw (it never asks the gateway)");
  eq(h.m.canSpendLiveTurn(), true, "...but the next turn may go live, as a trial");
  h.m.note({ status: 200, reason: null });
  deep([h.m.state(), h.m.badge()], ["live", "MOXIE ONLINE"], "a clean trial turn is what brings her back");
}
{
  // One good boot, then nothing but network failures: 30s, 60s, 120s, 240s, 300s (ceiling).
  const h = boot({ transport: true,
                   replies: [{ status: 200, body: HEALTH_LIVE }, { throws: true }] });
  await flush();
  for (let i = 0; i < 5; i++) await h.fire();
  deep(h.m.stats().scheduled, [30000, 60000, 120000, 240000, 300000, 300000],
       "§6.3: 30 s doubling to a 5-minute ceiling");
  eq(h.m.state(), "degraded", "the ladder degrades on the third strike");
  // ...and any success resets it to the floor.
  globalThis.fetch = () => Promise.resolve({ status: 200, text: () => Promise.resolve(HEALTH_LIVE) });
  await h.fire();
  eq(h.m.state(), "live", "a good poll recovers");
  eq(h.m.stats().lastDelayMs, 30000, "...and resets the backoff to the 30 s floor");
}

// 4h. Never polls while the tab is hidden (the rule ambient.js:77 already follows).
{
  const h = boot({ hidden: true, replies: [{ status: 200, body: HEALTH_LIVE }] });
  await flush();
  eq(h.m.stats().polls, 0, "a page opened in a hidden tab fires NO request");
  eq(h.m.state(), "boot", "...and shows today's page, which `boot` deliberately is");
  eq(h.m.badge(), "HOSTED DEMO", "...including today's badge");
  h.setHidden(false);
  await h.visibility();
  eq(h.m.stats().polls, 1, "it asks the moment the tab is looked at");
  eq(h.m.state(), "live", "...and lands in the real mode");
}
{
  const h = await bootLive();
  h.setHidden(true);
  await h.fire();
  eq(h.m.stats().polls, 1, "a due poll is SKIPPED while hidden");
  eq(h.m.stats().hiddenSkips, 1, "...and the skip is recorded");
  h.setHidden(false);
  await h.visibility();
  eq(h.m.stats().polls, 2, "...and run when the tab comes back");
}
{
  const h = boot({ replies: [{ status: 404, body: "" }] });
  await flush();
  eq(h.m.state(), "offline", "offline");
  await h.visibility();
  eq(h.m.stats().polls, 1, "offline never polls again, not even on a visibility change");
}
