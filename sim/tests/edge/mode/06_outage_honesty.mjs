/* test_mode §8: honest degraded states, replayed on a CLOCK. `sim/web/mode.js` (and, for what
 * the visitor sees, `sim/web/env.js`) as source; every envelope is one production can send:
 * the REAL `/api/health`, which never calls the gateway and so answers `live` all through an
 * outage, and the REAL refusal the spending routes answer with (`_lib/upstream.js`). Timers
 * fire at their due times, so a poll lands BETWEEN two turns — §4's "fire the next timer"
 * harness cannot put one there, which is how the false recoveries below went unseen.
 */
import { api } from "../common.mjs";
import {
  readFileSync, join, here, ok, eq, deep, lib, limits, FULL, probe, fakeEl,
} from "./harness.mjs";

const MODE_SRC = readFileSync(join(here, "web", "mode.js"), "utf8");
const ENV_SRC = readFileSync(join(here, "web", "env.js"), "utf8");
const upstream = await api("_lib", "upstream.js");
const cfg = lib.readConfig(FULL);
const HEALTH_LIVE = (await probe(FULL)).text;

/** Exactly what a spending route answers for `reason` (status and body). */
async function refused(reason, extra) {
  const res = upstream.refusal(cfg, "chat", reason, extra);
  return { status: res.status, body: JSON.parse(await res.text()) };
}
const UPSTREAM_DOWN = await refused("upstream_down");
const TIMEOUT = await refused("timeout");
const BOT_FAILED = await refused("turnstile_failed");
const CLEAN = { status: 200, body: { reason: null, retry_after_s: 0 } };

/** The page's half of a turn, reported as cloud-transport.js does: `note(reason, retry)`. */
const turn = (h, r) => h.m.note({ reason: r.body.reason || null, retry_after_s: r.body.retry_after_s || 0 });

const flush = async (n = 8) => { for (let i = 0; i < n; i++) await new Promise((r) => setImmediate(r)); };

/**
 * The real mode.js on a virtual clock, answered by the real live `/api/health`. `page: true`
 * also mounts the real env.js on a fake DOM, painted from mode.js's own snapshot;
 * `transport: false` leaves cloud-transport.js out; `before(m)` runs between the two, where
 * sim.html loads the scripts that subscribe ahead of env.js (turnstile.js, ambient.js).
 */
function bootClock(opts) {
  const o = opts || {};
  const T0 = 1_700_000_000_000;
  let clock = T0;
  let nextId = 1;
  const timers = [];
  const polls = [];
  const els = {};
  const get = (id) => (els[id] = els[id] || fakeEl(id));
  const linkstate = fakeEl("linkstate");
  const bar = fakeEl("topbar");
  bar.appendChild(linkstate);
  const body = fakeEl("body");
  const IDS = ["tts-test", "speech-btn", "mic-btn", "bus-connect", "mic-status", "bus-status", "tts-status"];
  globalThis.location = { protocol: "https:", origin: "https://sim.example", hostname: "sim.example" };
  globalThis.document = {
    hidden: false, body,
    addEventListener: () => {},
    getElementById: (id) => (o.page && IDS.includes(id) ? get(id) : null),
    querySelector: (sel) => (o.page && sel === "#topbar .linkstate" ? linkstate : null),
    createElement: (tag) => { const e = fakeEl(); e.tagName = String(tag).toUpperCase(); return e; },
  };
  globalThis.window = {
    moxieCloudTransport: o.transport !== false,
    moxieAudio: { setTtsHint: () => {}, hasCloudVoice: () => false, isSpeaking: () => false },
  };
  globalThis.localStorage = { getItem: () => null, setItem: () => {} };
  globalThis.setTimeout = (fn, ms) => { const id = nextId++; timers.push({ id, due: clock + ms, fn }); return id; };
  globalThis.clearTimeout = (id) => {
    const i = timers.findIndex((t) => t.id === id);
    if (i !== -1) timers.splice(i, 1);
  };
  Date.now = () => clock;
  globalThis.fetch = () => {
    polls.push((clock - T0) / 1000);
    return Promise.resolve({ status: 200, text: () => Promise.resolve(HEALTH_LIVE) });
  };
  (0, eval)(MODE_SRC);
  if (o.before) o.before(globalThis.window.moxieMode);
  if (o.page) (0, eval)(ENV_SRC);
  const m = globalThis.window.moxieMode;
  return {
    m, polls,
    /** Seconds since boot. */
    t: () => (clock - T0) / 1000,
    hide(v) { globalThis.document.hidden = v; },
    /** Advance the clock, firing every timer that falls due, in due order. */
    async advance(ms) {
      const end = clock + ms;
      for (;;) {
        timers.sort((a, b) => a.due - b.due || a.id - b.id);
        const next = timers[0];
        if (!next || next.due > end) break;
        timers.shift();
        clock = next.due;
        next.fn();
        await flush();
      }
      clock = end;
      await flush();
    },
    /** What env.js painted: the badge, `body[data-mode]` (a LIVE value hides the banner, in
     *  style.css), and the banner's words and "Run it locally" link. */
    page() {
      const badge = bar.children.find((c) => c.className.includes("env-badge"));
      const banner = body.children.find((c) => c.id === "env-banner");
      return {
        badge: badge ? badge.textContent : null,
        mode: body.getAttribute("data-mode"),
        banner: banner ? banner.querySelector(".eb-text").innerHTML : "",
        runLocally: banner ? !banner.querySelector(".eb-link").hidden : null,
      };
    },
  };
}

// 8. The instrument: every reply /api/health CAN give (the real handler, over each kind of
//    deployment) is a 200 with one of three (mode, reason) pairs. Never `upstream_down` or
//    `timeout`: only a TURN can report those, so only a turn may take them back.
{
  const seen = new Set();
  const look = async (env) => {
    const r = await probe(env);
    seen.add(`${r.res.status} ${r.body.mode} ${r.body.reason}`);
  };
  limits.__reset();
  for (const env of [{}, FULL, { ...FULL, DEMO_ENABLED: "0" }, { ...FULL, DEMO_GATEWAY_API_KEY: "" }]) await look(env);
  limits.__exhaustBudget(cfg);
  await look(FULL);
  limits.__reset();
  deep([...seen].sort(), ["200 degraded budget_exhausted", "200 degraded gateway_not_configured", "200 live null"],
       "/api/health only ever says live, not configured, or budget spent — it cannot see the gateway");
  deep([UPSTREAM_DOWN.status, UPSTREAM_DOWN.body.retry_after_s, TIMEOUT.status, TIMEOUT.body.retry_after_s], [503, 0, 504, 0],
       "a turn into a dead or hung gateway is a 503 / 504 whose BODY carries no wait (the page never reads the header)");
}

// 8a. A FAST-failing outage: a turn's envelope says upstream_down, and the next poll says
//     live, because it reads configuration only. The badge must not believe the poll.
{
  const h = bootClock();
  await flush();
  eq(h.m.badge(), "MOXIE ONLINE", "boot: configured, so live");
  turn(h, UPSTREAM_DOWN);
  eq(h.m.badge(), "HOSTED DEMO · SCRIPTED", "a turn says the brain is out: SCRIPTED at once");
  await h.advance(31_000);
  deep(h.polls, [0, 30], "a poll ran 30 s later and answered live");
  deep([h.m.state(), h.m.reason(), h.m.badge()], ["degraded", "upstream_down", "HOSTED DEMO · SCRIPTED"],
       "…and she is STILL out: a poll cannot see the gateway, so it cannot say she is back (it flipped to MOXIE ONLINE here)");
  deep(h.m.stats().transitions, ["boot->live", "live->degraded:upstream_down"], "no false recovery was recorded");
  eq(h.m.canSpendLiveTurn(), true, "what the poll CAN do is let the next turn try: a trial turn, an ordinary one");
  turn(h, UPSTREAM_DOWN);
  deep([h.m.badge(), h.m.canSpendLiveTurn()], ["HOSTED DEMO · SCRIPTED", false],
       "a trial that fails again keeps her out, and the next one waits for the next poll");
  await h.advance(31_000);
  eq(h.m.canSpendLiveTurn(), true, "…which arms it again");
  turn(h, CLEAN);
  deep([h.m.state(), h.m.reason(), h.m.badge()], ["live", null, "MOXIE ONLINE"],
       "a clean turn is what brings her back, visibly");
}

// 8b. Ten minutes of that outage with a visitor typing every 20 / 45 / 90 s, then the gateway
//     back. Every live turn the page allows is a trial; none may leave the badge lying.
for (const gapS of [20, 45, 90]) {
  const DOWN = 600;
  const h = bootClock();
  await flush();
  let online = 0, backAt = null, next = 5, seen = false;
  for (let s = 0; s < DOWN + 300; s++) {
    if (s === next) {
      next += gapS;
      if (h.m.canSpendLiveTurn()) {
        turn(h, s < DOWN ? UPSTREAM_DOWN : CLEAN);
        seen = true;
      }
    }
    // From the first line on: until a turn fails, nothing on the page can know.
    if (seen && s < DOWN && h.m.badge() === "MOXIE ONLINE") online++;
    if (s >= DOWN && backAt === null && h.m.badge() === "MOXIE ONLINE") backAt = s - DOWN;
    await h.advance(1000);
  }
  eq(online, 0, `a line every ${gapS} s: seconds the badge read MOXIE ONLINE after a turn saw the gateway down`);
  ok(backAt !== null && backAt <= gapS + 30,
     `a line every ${gapS} s: MOXIE ONLINE again within one line and one poll of the gateway coming back (${backAt} s)`);
}

// 8c. Turns that get NO envelope (the route unreachable from this page, or the client's own
//     deadline) degrade on the third in a row (§6.3), even with polls answering in between.
{
  const h = bootClock();
  await flush();
  for (let i = 0; i < 3; i++) {
    if (i) await h.advance(90_000);
    h.m.noteTransportError();
  }
  ok(h.polls.length >= 3, `polls answered live between the failed turns (${h.polls.length} polls)`);
  deep([h.m.state(), h.m.reason(), h.m.badge()], ["degraded", "upstream_down", "HOSTED DEMO · SCRIPTED"],
       "three failed turns 90 s apart degrade (each poll between them used to wipe the count, so they never did)");
  // A degrade with no verdict is the poll's to clear: an answer is what was missing…
  await h.advance(300_000);
  eq(h.m.state(), "live", "a poll that answers clears a degrade nobody gave a reason for");
  // …but the count stands until a TURN comes back, so the next failure degrades at once.
  h.m.noteTransportError();
  eq(h.m.state(), "degraded", "…on probation: one more failed turn and she is out again at once");
  await h.advance(300_000);
  turn(h, CLEAN);
  h.m.noteTransportError();
  eq(h.m.state(), "live", "a clean turn clears the count: one failure is survivable again");
}

// 8d. The per-IP HOUR window: the 41st line in an hour from a child typing one every 30 s.
{
  limits.__reset();
  const req = () => new Request("https://sim.example/api/chat", {
    method: "POST",
    headers: { Origin: "https://sim.example", "CF-Connecting-IP": "203.0.113.9", "Sec-Fetch-Site": "same-origin" },
  });
  const H0 = 1_760_000_400 - (1_760_000_400 % 3600) + 10;   // 10 s into a clock hour
  let n = 0, slot = null;
  for (n = 1; n <= 45; n++) {
    slot = await limits.admit({ request: req(), cfg, route: "chat", nowS: H0 + (n - 1) * 30, cache: null });
    if (!slot.ok) break;
    slot.release();
  }
  limits.__reset();
  deep([n, slot.reason, slot.retryAfterS], [41, "rate_limited", 2390],
       "the real limits refuse the 41st line of the hour, 2,390 s before the window resets");
  const R429 = await refused("rate_limited", { retryAfterS: slot.retryAfterS });
  deep([R429.status, R429.body.retry_after_s], [429, 2390], "…as a 429 whose body carries the wait");

  const h = bootClock({ page: true });
  await flush();
  deep([h.page().badge, h.page().mode], ["MOXIE ONLINE", "live"], "the page booted live");
  turn(h, R429);
  deep([h.m.state(), h.m.badge(), h.m.canSpendLiveTurn()], ["degraded", "HOSTED DEMO · RESTING", false],
       "she RESTS: not MOXIE ONLINE over 40 minutes of recorded answers");
  ok(/needs a rest/.test(h.m.message()) && /back in about 40 minutes/.test(h.m.message()) && !/few seconds/.test(h.m.message()),
     `…and says how long, not "a few seconds" (got "${h.m.message()}")`);
  const p = h.page();
  deep([p.badge, p.mode], ["HOSTED DEMO · RESTING", "degraded"],
       "what the visitor sees: the RESTING badge, and a degraded page, so the banner shows");
  ok(/brain is resting/.test(p.banner) && /try again later/.test(p.banner) && !/locally/i.test(p.banner) && p.runLocally === false,
     `…saying her brain is resting, with no "locally-run backend" and no "Run it locally" (${p.banner}, link ${p.runLocally})`);

  await h.advance(31_000);
  deep([h.m.state(), h.m.badge()], ["degraded", "HOSTED DEMO · RESTING"],
       "a poll 30 s later does not end the rest (it used to wipe the chip, leaving MOXIE ONLINE with no word why)");
  turn(h, CLEAN);
  eq(h.m.badge(), "HOSTED DEMO · RESTING", "a transcript that comes back clean does not end the chat window's rest");
  await h.advance(20 * 60_000);
  ok(/back in about 20 minutes/.test(h.m.message()), `the minutes count down with the polls (got "${h.m.message()}")`);

  // The window lifts at 2,390 s. With the tab hidden no poll runs, and still the next line
  // may go live the moment it does.
  h.hide(true);
  await h.advance((2390 - h.t() + 1) * 1000);
  deep([h.m.state(), h.m.canSpendLiveTurn()], ["degraded", true], "the moment the window lifts the next line may go live, poll or no poll");
  h.hide(false);
  await h.m.refresh();
  await flush();
  deep([h.m.state(), h.m.badge(), h.page().badge], ["live", "MOXIE ONLINE", "MOXIE ONLINE"], "…and the next poll says she is back");

  // A build with no live transport has no live brain to come back: the ears' own hour cap
  // there must not promise one in N minutes.
  const bare = bootClock({ transport: false });
  await flush();
  turn(bare, R429);
  deep([bare.m.state(), bare.m.badge()], ["live", "HOSTED DEMO · SCRIPTED"], "no transport: no RESTING promise");
  ok(/no live transport/.test(bare.m.message()), `…it keeps saying why (got "${bare.m.message()}")`);
}

// 8e. The per-MINUTE window keeps §6.3's soft chip, and a poll inside the window keeps it too.
{
  const h = bootClock();
  await flush();
  turn(h, await refused("rate_limited", { retryAfterS: 45 }));
  deep([h.m.state(), h.m.badge(), h.m.message()], ["live", "MOXIE ONLINE", "One at a time! Give Moxie a few seconds."],
       "the sixth line in a minute: still live, with the chip");
  await h.advance(31_000);
  deep([h.polls, h.m.message(), h.m.canSpendLiveTurn()], [[0, 30], "One at a time! Give Moxie a few seconds.", false],
       "a poll inside the window keeps the chip while lines are held back (it used to clear it)");
  await h.advance(15_000);
  eq(h.m.canSpendLiveTurn(), true, "the window lifts on time");
  await h.advance(30_000);
  eq(h.m.message(), "", "…and the next poll clears the chip");
}

// 8f. A HUNG gateway: each live turn waits out the server's deadline (DEMO_CHAT_TIMEOUT_MS,
//     20 s by default), then 504 timeout.
{
  const h = bootClock();
  await flush();
  turn(h, TIMEOUT);
  deep([h.m.state(), h.m.reason(), h.m.badge()], ["degraded", "timeout", "HOSTED DEMO · SCRIPTED"],
       "the FIRST timeout degrades (it took three, and polls kept resetting them)");
  // Every trial turn into a hung gateway costs its visitor the whole wait, so they back off.
  const armedAfter = [];
  for (let i = 0; i < 5; i++) {
    const from = h.t();
    while (!h.m.canSpendLiveTurn()) await h.advance(1000);
    armedAfter.push(h.t() - from);
    await h.advance(20_000);
    turn(h, TIMEOUT);
  }
  deep(armedAfter, [60, 120, 240, 300, 300], "trial turns after a timeout back off: 60 s, doubling to the 5-minute ceiling");
}
for (const [gapS, most, before] of [[10, 40, 60], [45, 60, 120], [90, 60, 120]]) {
  // l7's replay: the visitor types their next line `gapS` after each answer.
  const h = bootClock();
  await flush();
  let waited = 0, outOn = null;
  for (let i = 1; i <= 6; i++) {
    if (h.m.canSpendLiveTurn()) {
      await h.advance(20_000);
      waited += 20;
      turn(h, TIMEOUT);
      if (outOn === null && h.m.state() === "degraded") outOn = i;
    }
    await h.advance(gapS * 1000);
  }
  eq(outOn, 1, `a line every ${gapS} s into a hung gateway: the page degrades on the first`);
  ok(waited <= most, `…and the visitor waits out at most ${most} s of timeouts over six lines (waited ${waited} s; ${before} s before)`);
}

// 8g. A turn refused by the bot check proved nothing about the brain, so an out page keeps
//     saying why it is out (it painted the plain no-brain badge).
{
  const h = bootClock();
  await flush();
  turn(h, UPSTREAM_DOWN);
  turn(h, BOT_FAILED);
  deep([h.m.state(), h.m.reason(), h.m.badge()], ["degraded", "upstream_down", "HOSTED DEMO · SCRIPTED"],
       "a failed bot check on an out page keeps her reason and her badge");
  await h.advance(31_000);
  turn(h, BOT_FAILED);
  deep([h.m.badge(), h.m.canSpendLiveTurn()], ["HOSTED DEMO · SCRIPTED", true],
       "…and a trial it refuses leaves the next line free to try, with a fresh token");
}

// 8h. Every listener hears every change, even when one leaves mid-change. ambient.js subscribes
//     ahead of env.js and unsubscribes itself on the first change after its degraded line is
//     said (ambient.js `watchMode`); splicing the array being walked skipped env.js for that
//     change. In Chrome on origin/dev that left the badge SCRIPTED on a page that was live again.
{
  const ambientLike = (m) => {
    let said = false, off = null;
    off = m.onChange((snap) => {
      if (said) { if (off) { off(); off = null; } return; }
      if (snap && snap.state === "degraded") said = true;
    });
  };
  const h = bootClock({ page: true, before: ambientLike });
  await flush();
  turn(h, UPSTREAM_DOWN);
  eq(h.page().badge, "HOSTED DEMO · SCRIPTED", "a turn says she is out, and env.js paints it");
  turn(h, CLEAN);
  deep([h.m.state(), h.page().badge, h.page().mode], ["live", "MOXIE ONLINE", "live"],
       "she is back, and env.js repaints though the listener ahead of it left during that change");
}
