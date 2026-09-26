/* test_demo_proxy — §13–14: the admission queue, the rate-limit key, redirects. Run via the entry file, never alone. */
import {
  C, FULL, ORIGIN, P, assertClean, call, chat, deep,
  eq, fresh, limits, ok, req, sent, speech, upstreamCalls,
  wire2,
} from "./harness.mjs";

/* =========================================================================== *
 * 13. THE ADMISSION QUEUE — a bounded FIFO behind the concurrency ceiling
 * =========================================================================== *
 * Spec: live-sim-demo.md §4.1 (the queue variables), §4.5 (`at_capacity` keeps its 503 and
 * `Retry-After: 15`), §4.6, §7.
 *
 * `admit()` waits, briefly and in arrival order, behind `DEMO_MAX_CONCURRENT_CHAT` instead
 * of refusing at once — WITHOUT raising the ceiling, which matches an upstream key shared
 * with a neighbour service. Assertions are on recorded facts (`__state().waiting`,
 * `stats.queue`, envelopes), except the wait-expired case, where "time passed" IS the
 * claim. Waits are tens of milliseconds.
 */
{
  /** The queue's own deployment: a short wait, a small depth, and per-IP windows wide
   *  enough that the WINDOWS are never what refuses us — except in the one test where
   *  that is the point. */
  const QENV = {
    ...FULL,
    DEMO_QUEUE_MAX_WAIT_MS: "300",
    DEMO_QUEUE_MAX_DEPTH: "4",
    DEMO_CHAT_PER_MIN: "100",
    DEMO_CHAT_PER_HOUR: "1000",
    DEMO_CHAT_PER_DAY: "1000",
  };
  const qcfg = wire2.readConfig(QENV);
  const admitChat = (cfg, ip) =>
    limits.admit({ request: req("/api/chat", { text: "x" }, { "CF-Connecting-IP": ip }), cfg, route: "chat" });
  /** Fill the ceiling from ONE ip, so a queued visitor's own window is untouched. */
  const fillCeiling = async (cfg) => {
    const held = [];
    for (let i = 0; i < 4; i++) held.push(await admitChat(cfg, "203.0.113.4"));
    return held;
  };

  // ---- The defaults are the ones env.js reasons about, not accidents ------- //
  const dflt = wire2.readConfig(FULL);
  eq(dflt.queueMaxWaitMs, 2500, "DEMO_QUEUE_MAX_WAIT_MS defaults to 2500 ms — two turn-times at the ceiling");
  eq(dflt.queueMaxDepth, 8, "DEMO_QUEUE_MAX_DEPTH defaults to 8 — what 2500 ms of waiting can actually drain");
  ok(dflt.queueMaxWaitMs < dflt.chatTimeoutMs,
     "the maximum wait must stay well under DEMO_CHAT_TIMEOUT_MS, or a queued turn out-waits its own call");
  eq(wire2.readConfig({ ...FULL, DEMO_QUEUE_MAX_WAIT_MS: "999999" }).queueMaxWaitMs, 2500,
     "an out-of-range wait falls back to the default (a bad number must never become a bigger cap)");
  deep(Object.keys(wire2.publicLimits(dflt)), [...wire2.PUBLIC_LIMIT_KEYS],
       "the queue is server-side only: neither variable joins publicLimits");

  // ---- 13a. FIFO under contention, and no overtaking ---------------------- //
  fresh();
  const hold = await fillCeiling(qcfg);
  eq(limits.__state().inflight.chat, 4, "the ceiling is full");

  const order = [];
  const track = (tag) =>
    admitChat(qcfg, "198.51.100." + tag.charCodeAt(0)).then((r) => {
      order.push(tag + ":" + (r.ok ? "granted" : r.reason));
      return r;
    });
  const wA = track("A"), wB = track("B"), wC = track("C");
  eq(limits.__state().waiting.chat, 3, "three colliding requests WAIT — they are not refused");
  eq(limits.__state().inflight.chat, 4, "…and waiting is not in flight: the ceiling is still 4");
  eq(limits.__state().stats.queue.joined, 3, "…recorded as three joins");
  eq(upstreamCalls(), 0, "…and a queued request has called nothing yet");

  // A LATE ARRIVAL MUST NOT OVERTAKE. `release()` hands the slot straight to A rather than
  // freeing it, so D — which asks in the very next statement — finds no free slot and
  // joins the BACK of the queue. This is the assertion that would catch a queue that is
  // fair only by scheduling luck.
  hold[0].release();
  const wD = track("D");
  eq(limits.__state().waiting.chat, 3, "a request arriving the instant a slot frees queues BEHIND B and C");
  eq(limits.__state().inflight.chat, 4, "…because the released slot was handed over, never freed");

  hold[1].release();
  hold[2].release();
  hold[3].release();
  const rescued = await Promise.all([wA, wB, wC, wD]);
  deep(order, ["A:granted", "B:granted", "C:granted", "D:granted"],
       "FIFO: the longest-waiting request takes each freed slot, in arrival order");
  eq(limits.__state().stats.queue.granted, 4, "…four hand-overs recorded");
  eq(limits.__state().inflight.chat, 4, "the four granted requests hold the four slots");
  for (const r of rescued) r.release();
  eq(limits.__state().inflight.chat, 0, "…and every one of them comes back");
  eq(limits.__state().waiting.chat, 0, "the FIFO is empty and carries no tombstones");

  // ---- 13b. The depth cap refuses IMMEDIATELY ----------------------------- //
  // A queue with no depth cap is just a slower way to fall over. Past the cap the answer
  // is the same `at_capacity` this route has always given — and it must arrive at once,
  // not after a wait, which is why this is raced against a timer.
  fresh();
  const DEPTH2 = { ...QENV, DEMO_QUEUE_MAX_DEPTH: "2", DEMO_QUEUE_MAX_WAIT_MS: "5000" };
  const cfg2 = wire2.readConfig(DEPTH2);
  const hold2 = await fillCeiling(cfg2);
  const q1 = admitChat(cfg2, "198.51.100.11"), q2 = admitChat(cfg2, "198.51.100.12");
  eq(limits.__state().waiting.chat, 2, "the queue is at its depth cap of 2");
  const raced = await Promise.race([
    admitChat(cfg2, "198.51.100.13"),
    new Promise((r) => setTimeout(() => r("still-waiting"), 100)),
  ]);
  ok(raced !== "still-waiting", "past DEMO_QUEUE_MAX_DEPTH the refusal is IMMEDIATE — the 3rd waiter never waits");
  eq(raced.reason, "at_capacity", "…and it is the existing at_capacity reason, not a new one");
  eq(limits.__state().stats.queue.refusedFull, 1, "…recorded as a depth-cap refusal");
  eq(limits.__state().stats.queue.joined, 2, "…and it never joined the queue");
  eq(upstreamCalls(), 0, "a depth-capped refusal makes ZERO upstream calls");
  hold2[0].release(); hold2[1].release();
  const drained = await Promise.all([q1, q2]);
  ok(drained[0].ok && drained[1].ok, "the two that DID fit are served");
  for (const r of drained) r.release();
  hold2[2].release(); hold2[3].release();

  // ---- 13c. Switching the queue off restores the pre-2026-09-03 behaviour -- //
  for (const off of [{ DEMO_QUEUE_MAX_DEPTH: "0" }, { DEMO_QUEUE_MAX_WAIT_MS: "0" }]) {
    fresh();
    const cfgOff = wire2.readConfig({ ...QENV, ...off });
    const heldOff = await fillCeiling(cfgOff);
    const r = await Promise.race([
      admitChat(cfgOff, "198.51.100.14"),
      new Promise((x) => setTimeout(() => x("still-waiting"), 100)),
    ]);
    ok(r !== "still-waiting" && r.reason === "at_capacity",
       `${Object.keys(off)[0]}=0 is the escape hatch: refuse instantly, exactly as before the queue`);
    eq(limits.__state().stats.queue.joined, 0, "…nothing was ever queued");
    for (const h of heldOff) h.release();
  }

  // ---- 13d. The wait expires, through the real route ---------------------- //
  fresh();
  const SHORT = { ...QENV, DEMO_QUEUE_MAX_WAIT_MS: "60" };
  const cfgShort = wire2.readConfig(SHORT);
  const hold3 = await fillCeiling(cfgShort);
  // "It waited" is asserted by RACING it, not by reading the wall clock: a duration
  // measured off the wall clock is a flaky assertion on a loaded runner *and* the thing
  // `sim/tests/test_clock_dependence.py` exists to keep out of this tree. A request that
  // is still unanswered at 20 ms of a 60 ms budget cannot have been refused on the spot.
  const pending = call(chat, "/api/chat", { text: "hi" }, { "CF-Connecting-IP": "198.51.100.20" }, SHORT);
  const atTwenty = await Promise.race([
    pending.then(() => "answered"),
    new Promise((r) => setTimeout(() => r("still-waiting"), 20)),
  ]);
  eq(atTwenty, "still-waiting", "at the ceiling the request WAITS instead of being refused on the spot");
  eq(limits.__state().waiting.chat, 1, "…and is visibly in the FIFO while it does");
  const timedOut = await pending;
  eq(timedOut.res.status, 503, "a wait that expires is still a 503");
  eq(timedOut.body.reason, "at_capacity", "…with the EXISTING at_capacity reason (§4.5, unchanged)");
  eq(timedOut.res.headers.get("Retry-After"), "15",
     "…and §4.5's Retry-After: 15 survives the queue — a saturated ceiling is not a 60 ms problem");
  eq(timedOut.body.load.level, "full", "…and §7's `full` level is still what the page is told");
  eq(upstreamCalls(), 0, "a queued-then-expired turn makes ZERO upstream calls");
  eq(limits.__state().stats.queue.expired, 1, "…recorded as an expiry, not a depth refusal");
  eq(limits.__state().waiting.chat, 0, "…and the expired waiter removed itself from the FIFO");
  for (const h of hold3) h.release();
  eq(limits.__state().inflight.chat, 0, "no slot leaked by the expiry");

  // ---- 13e. THE CHARGE/REFUND DECISION ------------------------------------ //
  // `admit()` charges the window and the budget BEFORE the slot, so a visitor who waits
  // and times out would pay for a turn never received. The chosen fix is a REFUND
  // (`_lib/limits.js::refundCharges`), not a reordering.
  fresh();
  const cfgRef = wire2.readConfig(SHORT);
  const holdRef = await fillCeiling(cfgRef);
  const budgetBefore = { ...limits.__state().budget };
  const stranded = await admitChat(cfgRef, "198.51.100.30");
  eq(stranded.ok, false, "a request that waits out the clock is refused");
  deep(limits.__state().budget, budgetBefore,
       "THE UNIT BUDGET IS REFUNDED: a timed-out waiter must not spend units on a turn it never got");
  eq(stranded.rateLimit.remaining, cfgRef.chatPerMin,
     "…and its per-IP window is refunded too, so the X-RateLimit headers it is sent are TRUE after the refund");
  for (const h of holdRef) h.release();

  // The same thing where a visitor can actually feel it: five turns a minute means five
  // turns a minute, even when one of them was queued and refused. Without the refund the
  // fifth of these would be `rate_limited`.
  fresh();
  const FIVE = { ...FULL, DEMO_QUEUE_MAX_WAIT_MS: "40", DEMO_QUEUE_MAX_DEPTH: "4" };  // chat_per_min = 5
  const cfgFive = wire2.readConfig(FIVE);
  const holdFive = await fillCeiling(cfgFive);
  const denied = await call(chat, "/api/chat", { text: "hi" }, { "CF-Connecting-IP": "198.51.100.40" }, FIVE);
  eq(denied.body.reason, "at_capacity", "the visitor waited and was refused");
  for (const h of holdFive) h.release();
  for (let i = 1; i <= 5; i++) {
    const turn = await call(chat, "/api/chat", { text: "hi" }, { "CF-Connecting-IP": "198.51.100.40" }, FIVE);
    eq(turn.res.status, 200, `…and still has all ${cfgFive.chatPerMin} of their minute: turn ${i} is served`);
  }

  // ---- 13f. …AND THE ORDERING IT PRESERVES -------------------------------- //
  // Why the wait was not moved before the charge: a request refused for free must stay
  // free and never occupy a queue slot, displacing a legitimate visitor.
  fresh();
  const cfgRl = wire2.readConfig({ ...SHORT, DEMO_CHAT_PER_MIN: "3" });
  const FLOOD_IP = "198.51.100.50";
  // Spend this IP's whole minute while there is still capacity, so these are ordinary
  // charged admissions and not queued ones.
  for (let i = 0; i < cfgRl.chatPerMin; i++) {
    const s = await admitChat(cfgRl, FLOOD_IP);
    ok(s.ok, `the flooding IP's turn ${i + 1} of ${cfgRl.chatPerMin} is served normally`);
    s.release();
  }
  const holdRl = await fillCeiling(cfgRl);
  const overWindow = await Promise.race([
    admitChat(cfgRl, FLOOD_IP),
    new Promise((r) => setTimeout(() => r("still-waiting"), 100)),
  ]);
  ok(overWindow !== "still-waiting", "an over-window request is refused INSTANTLY even at the ceiling");
  eq(overWindow.reason, "rate_limited",
     "the per-IP window still refuses FIRST — a rate-limited request never reaches the queue");
  eq(limits.__state().waiting.chat, 0, "…and never occupies a queue slot it has not earned");
  eq(limits.__state().stats.queue.joined, 0, "…nothing was queued at all on this path");
  for (const h of holdRl) h.release();

  // ---- 13g. A THROWN path hands its slot on, and leaks nothing ------------ //
  // `chat.js`:171, `speech.js`:202 and `transcribe.js`:179 all put `release()` in a
  // `finally`. With a queue behind the ceiling that `finally` is no longer only about this
  // request's tidiness — it is what the next person in the queue is waiting for.
  fresh();
  const holdThrow = await fillCeiling(qcfg);
  const waitingOnThrow = admitChat(qcfg, "198.51.100.60");
  eq(limits.__state().waiting.chat, 1, "someone is waiting behind the four in flight");
  let threw = false;
  try {
    try {
      throw new Error("upstream blew up mid-turn");
    } finally {
      holdThrow[0].release(); // exactly the shape of every route's `finally`
    }
  } catch {
    threw = true;
  }
  ok(threw, "the modelled route really did throw");
  const handedOn = await waitingOnThrow;
  eq(handedOn.ok, true, "a slot released from a THROWN path is HANDED to the longest-waiting request");
  handedOn.release();
  holdThrow[1].release(); holdThrow[2].release(); holdThrow[3].release();
  eq(limits.__state().inflight.chat, 0, "…and nothing is leaked: every slot is back");

  // The route-level equivalent, on the failure the stub can actually produce: an upstream
  // timeout, with someone queued behind it.
  fresh();
  P.plan = { chat: { throw: "TimeoutError" } };
  const holdT = [];
  for (let i = 0; i < 3; i++) holdT.push(await admitChat(qcfg, "203.0.113.4"));
  const routeP = chat.onRequestPost({
    request: req("/api/chat", { text: "boom" }, { "CF-Connecting-IP": "198.51.100.70" }), env: QENV });
  eq(limits.__state().inflight.chat, 4, "the failing turn holds the 4th slot");
  const behind = admitChat(qcfg, "198.51.100.71");
  eq(limits.__state().waiting.chat, 1, "…and a visitor is queued behind it");
  const failed = await routeP;
  await assertClean(failed, "/api/chat a failing turn with someone queued behind it");
  eq(failed.status, 504, "the failing turn answers 504 timeout");
  const nextUp = await behind;
  eq(nextUp.ok, true, "…and its slot goes straight to the queued visitor");
  nextUp.release();
  for (const h of holdT) h.release();
  eq(limits.__state().inflight.chat, 0, "no slot survives the failure");
  eq(limits.__state().waiting.chat, 0, "and no waiter is stranded");
}


/* =========================================================================== *
 * 14. WHO IS ASKING — the rate-limit KEY, and the redirect the key rides on
 * =========================================================================== *
 * Spec: live-sim-demo.md §4.1, §4.2, §4.6.
 *
 *   A. AN IPv6 VISITOR IS ONE VISITOR: the key is the /64 (a residential allocation), not
 *      the raw address. The table pins every awkward form — get the IPv4-mapped row wrong
 *      and the whole v4 internet shares one bucket.
 *   B. A HEADER THE CALLER TYPES IS NOT AN IDENTITY: `X-Forwarded-For` is only trusted
 *      behind `DEMO_TRUST_XFF`; without `CF-Connecting-IP` callers share one `unknown`
 *      bucket, throttled together rather than each given a lane.
 *   C. THE CREDENTIAL DOES NOT CHASE A `Location`: upstream fetches use
 *      `redirect: "manual"` and read a 3xx as `gateway_unreachable_or_gated`.
 *
 * A is proved twice: as a pure table over `ipKey`, and through the real windows, because
 * only "two addresses actually share a bucket" is the control.
 */
{
  fresh();

  // ---- 14a. The address table. Every row is a form that reaches a real edge --- //
  const KEYS = [
    // [what arrives, what it must key as, why the row is here]
    ["203.0.113.9",                     "203.0.113.9",  "plain IPv4 is untouched"],
    ["  203.0.113.9  ",                 "203.0.113.9",  "whitespace is trimmed"],
    ["1.2.3.4:5678",                    "1.2.3.4",      "IPv4 with a port loses the port"],
    ["2001:db8:1:2:3:4:5:6",            "2001:db8:1:2", "a full IPv6 is truncated to its /64"],
    ["2001:db8:1:2:ffff:ffff:ffff:fff", "2001:db8:1:2", "…and so is another host in the SAME /64"],
    ["2001:db8:1:3:3:4:5:6",            "2001:db8:1:3", "a DIFFERENT /64 keeps its own key"],
    ["2001:db8::1",                     "2001:db8:0:0", "a `::` elision expands before truncation"],
    ["2001:0db8:0000:0000:0000:0000:0000:0001", "2001:db8:0:0", "leading zeros normalise to one key"],
    ["2001:DB8::1",                     "2001:db8:0:0", "case normalises to one key"],
    ["::1",                             "0:0:0:0",      "loopback parses rather than falling through"],
    ["::",                              "0:0:0:0",      "the unspecified address parses too"],
    ["fe80::1%eth0",                    "fe80:0:0:0",   "a zone index names OUR interface, not the sender"],
    ["fe80::1%25eth0",                  "fe80:0:0:0",   "…including the percent-encoded spelling"],
    ["[2001:db8::1]:443",               "2001:db8:0:0", "the bracketed authority form loses brackets and port"],
    // The row that would be silently catastrophic if it were wrong.
    ["::ffff:1.2.3.4",                  "1.2.3.4",      "IPv4-MAPPED unmaps to the v4 address, NOT to a /64"],
    ["::ffff:102:304",                  "1.2.3.4",      "…and so does the same address written in hex"],
    ["[::ffff:1.2.3.4]:80",             "1.2.3.4",      "…and the bracketed form of it"],
    ["::ffff:255.255.255.255",          "255.255.255.255", "…at the top of the range"],
    // Malformed: `unknown`, which SHARES a bucket. Never keyed as itself.
    [":::1",                            "unknown",      "a triple colon is not an address"],
    ["2001:db8:::1",                    "unknown",      "…nor is a doubled elision"],
    ["zz::1",                           "unknown",      "…nor is a non-hex group"],
    ["2001:db8:1:2:3:4:5:6:7",          "unknown",      "…nor are nine groups"],
    ["",                                "unknown",      "an empty string is not an address"],
  ];
  for (const [raw, want, why] of KEYS) eq(limits.ipKey(raw), want, `ipKey(${JSON.stringify(raw)}): ${why}`);

  // Two things the table asserts jointly and that are worth stating as their own claims.
  ok(limits.ipKey("::ffff:1.2.3.4") === limits.ipKey("1.2.3.4"),
     "a v4 client reported as IPv4-mapped keys IDENTICALLY to the same client reported as v4");
  ok(limits.ipKey("::ffff:1.2.3.4") !== limits.ipKey("::ffff:5.6.7.8"),
     "…and two DIFFERENT v4 clients still get two buckets (the row that would collapse the v4 internet)");

  // ---- 14b. The key, through the real windows ----------------------------- //
  // A table is not a control. This is: five turns a minute, spent from five DIFFERENT
  // addresses inside one /64. Before the fix each got its own bucket and all five were
  // served; now the sixth request from the sixth address is refused.
  fresh();
  const V6 = (n) => "2001:db8:cafe:1::" + n.toString(16);
  const cfg6 = wire2.readConfig(FULL);
  eq(cfg6.chatPerMin, 5, "the block is calibrated to the shipped chat_per_min");
  for (let i = 1; i <= cfg6.chatPerMin; i++) {
    const turn = await call(chat, "/api/chat", { text: "hi" }, { "CF-Connecting-IP": V6(i) });
    eq(turn.res.status, 200, `turn ${i} from ${V6(i)} — a fresh address in one /64 — is served`);
  }
  const sixth = await call(chat, "/api/chat", { text: "hi" }, { "CF-Connecting-IP": V6(99) });
  eq(sixth.body.reason, "rate_limited",
     "THE BYPASS IS CLOSED: a 6th unused IPv6 address in the SAME /64 is refused, not served");
  eq(sixth.res.status, 429, "…with the §4.5 status for a rate-limited turn");

  // …and the fix is not a blunt instrument: a genuinely different subscriber is unaffected.
  const neighbour = await call(chat, "/api/chat", { text: "hi" }, { "CF-Connecting-IP": "2001:db8:cafe:2::1" });
  eq(neighbour.res.status, 200, "a DIFFERENT /64 is a different visitor and is served normally");

  // ---- 14c. The refund credits the bucket the charge took ----------------- //
  // Refund keys embed the derived ip, so an IPv6 visitor who times out gets exactly one
  // unit back on their /64 — not one per address used.
  fresh();
  const QQ = { ...FULL, DEMO_QUEUE_MAX_WAIT_MS: "40", DEMO_QUEUE_MAX_DEPTH: "4" };
  const cfgQ = wire2.readConfig(QQ);
  const holdQ = [];
  for (let i = 0; i < cfgQ.maxConcurrentChat; i++) {
    holdQ.push(await limits.admit({
      request: req("/api/chat", { text: "x" }, { "CF-Connecting-IP": "203.0.113.4" }), cfg: cfgQ, route: "chat" }));
  }
  const budgetBefore = limits.__state().budget;
  const timedOut = await call(chat, "/api/chat", { text: "hi" }, { "CF-Connecting-IP": "2001:db8:beef:7::a" }, QQ);
  eq(timedOut.body.reason, "at_capacity", "the IPv6 visitor waited and was refused");
  deep(limits.__state().budget, budgetBefore, "the unit budget is back where it was — the charge was refunded");
  for (const h of holdQ) h.release();
  // Their whole minute survives, and it survives whichever address in the /64 they come
  // back on — which is the point: one subscriber, one bucket, refunded once.
  for (let i = 1; i <= cfgQ.chatPerMin; i++) {
    const turn = await call(chat, "/api/chat", { text: "hi" }, { "CF-Connecting-IP": "2001:db8:beef:7::" + i }, QQ);
    eq(turn.res.status, 200, `…and the refunded /64 still has all ${cfgQ.chatPerMin} of its minute: turn ${i}`);
  }

  // ---- 14d. X-Forwarded-For is not an identity ---------------------------- //
  fresh();
  const noCf = (xff) => new Request(ORIGIN + "/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json", Origin: ORIGIN, "Sec-Fetch-Site": "same-origin",
               "X-Forwarded-For": xff },
    body: JSON.stringify({ text: "hi" }),
  });
  const dfltCfg = wire2.readConfig(FULL);
  eq(dfltCfg.trustXff, false, "DEMO_TRUST_XFF is OFF by default — production must never set it");
  eq(limits.clientIp(noCf("9.9.9.9"), dfltCfg), "unknown",
     "with CF-Connecting-IP absent, a client-supplied X-Forwarded-For is IGNORED");
  eq(limits.clientIp(noCf("8.8.8.8"), dfltCfg), "unknown",
     "…and a DIFFERENT forged value keys the same, so rotating the header buys nothing");
  eq(limits.clientIp(noCf("9.9.9.9")), "unknown",
     "…and a caller that passes no cfg at all gets the conservative answer, not the trusting one");
  // Spending the `unknown` bucket proves the sharing is real and not just string equality.
  for (let i = 1; i <= dfltCfg.chatPerMin; i++) {
    const r = await chat.onRequestPost({ request: noCf("10.0.0." + i), env: FULL });
    eq(r.status, 200, `unidentified turn ${i} is served from the SHARED unknown bucket`);
  }
  const overflow = await chat.onRequestPost({ request: noCf("10.0.0.250"), env: FULL });
  eq(overflow.status, 429,
     "…and the 6th is refused: everything unidentifiable is throttled TOGETHER, which is the intent");

  // The opt-in still works, for `wrangler pages dev` where there is no Cloudflare in front.
  const trusting = wire2.readConfig({ ...FULL, DEMO_TRUST_XFF: "1" });
  eq(trusting.trustXff, true, "DEMO_TRUST_XFF=1 turns the local-dev fallback back on");
  eq(limits.clientIp(noCf("9.9.9.9, 8.8.8.8"), trusting), "9.9.9.9",
     "…and it reads the FIRST hop, as before");
  eq(limits.clientIp(noCf("2001:db8:9:9:1:2:3:4"), trusting), "2001:db8:9:9",
     "…through the same /64 normalisation, so the opt-in cannot re-open the IPv6 hole");
  // CF-Connecting-IP always wins, so the opt-in cannot be used to override a real edge.
  eq(limits.clientIp(req("/api/chat", {}, { "X-Forwarded-For": "9.9.9.9" }), trusting), "203.0.113.9",
     "CF-Connecting-IP OUTRANKS X-Forwarded-For even when the fallback is enabled");

  // ---- 14e. The credential does not follow a redirect --------------------- //
  fresh();
  P.plan = { chat: { status: 200, content: "hi" } };
  await call(chat, "/api/chat", { text: "hello" });
  eq(sent.length, 1, "one upstream call was made");
  eq(sent[0].opt.redirect, "manual",
     "/api/chat sets redirect:'manual' — the Authorization header is never re-sent to a Location");

  fresh();
  const turn = await call(chat, "/api/chat", { text: "hello" });
  sent.length = 0;
  await call(speech, "/api/speech", { ticket: turn.body.speech[0].ticket });
  eq(sent.length, 1, "one upstream call was made");
  eq(sent[0].opt.redirect, "manual", "/api/speech sets redirect:'manual' too");

  // …and an unfollowed 3xx is the DOOR, not the brain. `upstream_down` would send an
  // operator to restart a model server for a fault that is a tunnel, an Access login flow
  // or a base URL that bounces http -> https.
  for (const status of [301, 302, 303, 307, 308]) {
    fresh();
    P.plan = { chat: { status, body: "", headers: { Location: "https://elsewhere.invalid.test/v1/chat/completions" } } };
    const bounced = await call(chat, "/api/chat", { text: "hello" });
    eq(bounced.body.reason, "gateway_unreachable_or_gated",
       `/api/chat reads an upstream ${status} as a door problem, not a brain problem`);
    eq(bounced.res.status, 503, `…and answers 503 for a ${status}`);
    eq(sent.length, 1, `…having made exactly ONE upstream call for a ${status} — the redirect was not chased`);
  }
  fresh();
  const turn2 = await call(chat, "/api/chat", { text: "hello" });
  sent.length = 0;
  P.plan = { speech: { status: 302, body: "", headers: { Location: "https://elsewhere.invalid.test/v1/audio/speech" } } };
  const bouncedTts = await call(speech, "/api/speech", { ticket: turn2.body.speech[0].ticket });
  eq(bouncedTts.body.reason, "gateway_unreachable_or_gated", "/api/speech reads a 302 the same way");
  eq(bouncedTts.res.status, 503, "…and answers 503");
  eq(sent.length, 1, "…and did not chase it either");
}
