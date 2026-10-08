/* test_demo_proxy §13–14: the admission queue, the rate-limit key, redirects. Run via the
 * entry file. */
import {
  FULL, ORIGIN, P, assertClean, call, chat, deep,
  eq, fresh, limits, ok, req, sent, speech, upstreamCalls, wire2,
} from "./harness.mjs";

/* 13. THE ADMISSION QUEUE (§4.1, §4.5): `admit()` waits briefly, FIFO, behind
 * DEMO_MAX_CONCURRENT_CHAT instead of refusing at once — without raising the ceiling.
 * Assertions read recorded facts (`__state()`), except where "it waited" IS the claim, which
 * is proved by racing a timer, never by reading the wall clock. */
{
  const QENV = { ...FULL, DEMO_QUEUE_MAX_WAIT_MS: "300", DEMO_QUEUE_MAX_DEPTH: "4",
                 DEMO_CHAT_PER_MIN: "100", DEMO_CHAT_PER_HOUR: "1000", DEMO_CHAT_PER_DAY: "1000" };
  const qcfg = wire2.readConfig(QENV);
  const admitChat = (cfg, ip) =>
    limits.admit({ request: req("/api/chat", { text: "x" }, { "CF-Connecting-IP": ip }), cfg, route: "chat" });
  /** Fill the ceiling from ONE ip, so a queued visitor's own window is untouched. */
  const fillCeiling = async (cfg) => {
    const held = [];
    for (let i = 0; i < 4; i++) held.push(await admitChat(cfg, "203.0.113.4"));
    return held;
  };
  /** The promise's value, or "still-waiting" if it has not settled within `ms`. */
  const within = (p, ms) => Promise.race([p, new Promise((r) => setTimeout(() => r("still-waiting"), ms))]);
  const st = () => limits.__state();

  const dflt = wire2.readConfig(FULL);
  deep([dflt.queueMaxWaitMs, dflt.queueMaxDepth], [2500, 8], "the queue defaults: 2500 ms, depth 8");
  ok(dflt.queueMaxWaitMs < dflt.chatTimeoutMs, "the maximum wait stays under DEMO_CHAT_TIMEOUT_MS");
  eq(wire2.readConfig({ ...FULL, DEMO_QUEUE_MAX_WAIT_MS: "999999" }).queueMaxWaitMs, 2500,
     "an out-of-range wait falls back to the default, never a bigger cap");
  deep(Object.keys(wire2.publicLimits(dflt)), [...wire2.PUBLIC_LIMIT_KEYS], "the queue is server-side only");

  // 13a. FIFO, and a late arrival cannot overtake: release() HANDS the slot to A rather than
  // freeing it, so D — asking in the very next statement — joins the BACK.
  fresh();
  const hold = await fillCeiling(qcfg);
  const order = [];
  const track = (tag) => admitChat(qcfg, "198.51.100." + tag.charCodeAt(0)).then((r) => {
    order.push(tag + ":" + (r.ok ? "granted" : r.reason));
    return r;
  });
  const waiters = [track("A"), track("B"), track("C")];
  deep([st().waiting.chat, st().inflight.chat, st().stats.queue.joined, upstreamCalls()], [3, 4, 3, 0],
       "three colliding requests WAIT (not refused, not in flight, nothing called)");
  hold[0].release();
  waiters.push(track("D"));
  deep([st().waiting.chat, st().inflight.chat], [3, 4], "a request arriving the instant a slot frees queues BEHIND B and C");
  for (const h of hold.slice(1)) h.release();
  const rescued = await Promise.all(waiters);
  deep(order, ["A:granted", "B:granted", "C:granted", "D:granted"], "FIFO: each freed slot goes to the longest waiter");
  eq(st().stats.queue.granted, 4, "…four hand-overs recorded");
  for (const r of rescued) r.release();
  deep([st().inflight.chat, st().waiting.chat], [0, 0], "every slot comes back and the FIFO is empty");

  // 13b. Past the depth cap the refusal is IMMEDIATE and is the existing at_capacity.
  fresh();
  const cfg2 = wire2.readConfig({ ...QENV, DEMO_QUEUE_MAX_DEPTH: "2", DEMO_QUEUE_MAX_WAIT_MS: "5000" });
  const hold2 = await fillCeiling(cfg2);
  const q = [admitChat(cfg2, "198.51.100.11"), admitChat(cfg2, "198.51.100.12")];
  const raced = await within(admitChat(cfg2, "198.51.100.13"), 100);
  eq(raced.reason, "at_capacity", "past DEMO_QUEUE_MAX_DEPTH the 3rd waiter is refused at_capacity INSTANTLY");
  deep([st().stats.queue.refusedFull, st().stats.queue.joined, upstreamCalls()], [1, 2, 0],
       "…recorded as a depth refusal that never joined, with ZERO upstream calls");
  hold2[0].release(); hold2[1].release();
  const drained = await Promise.all(q);
  ok(drained.every((r) => r.ok), "the two that DID fit are served");
  for (const r of [...drained, hold2[2], hold2[3]]) r.release();

  // 13c. Either variable at 0 is the escape hatch: refuse instantly, as before the queue.
  for (const off of [{ DEMO_QUEUE_MAX_DEPTH: "0" }, { DEMO_QUEUE_MAX_WAIT_MS: "0" }]) {
    fresh();
    const cfgOff = wire2.readConfig({ ...QENV, ...off });
    const heldOff = await fillCeiling(cfgOff);
    const r = await within(admitChat(cfgOff, "198.51.100.14"), 100);
    eq(`${r.reason} ${st().stats.queue.joined}`, "at_capacity 0", `${Object.keys(off)[0]}=0 refuses instantly, queueing nothing`);
    for (const h of heldOff) h.release();
  }

  // 13d. The wait expires, through the real route: still a 503 at_capacity with §4.5's
  // Retry-After: 15, and still free.
  fresh();
  const SHORT = { ...QENV, DEMO_QUEUE_MAX_WAIT_MS: "60" };
  const hold3 = await fillCeiling(wire2.readConfig(SHORT));
  const pending = call(chat, "/api/chat", { text: "hi" }, { "CF-Connecting-IP": "198.51.100.20" }, SHORT);
  eq(await within(pending.then(() => "answered"), 20), "still-waiting", "at the ceiling the request WAITS instead of being refused");
  eq(st().waiting.chat, 1, "…visibly in the FIFO");
  const timedOut = await pending;
  eq(`${timedOut.res.status} ${timedOut.body.reason} ${timedOut.res.headers.get("Retry-After")} ${timedOut.body.load.level}`,
     "503 at_capacity 15 full", "a wait that expires is the EXISTING 503 at_capacity, Retry-After 15, level full");
  deep([upstreamCalls(), st().stats.queue.expired, st().waiting.chat], [0, 1, 0],
       "…with ZERO upstream calls, recorded as an expiry, the waiter gone from the FIFO");
  for (const h of hold3) h.release();
  eq(st().inflight.chat, 0, "no slot leaked by the expiry");

  // 13e. admit() charges BEFORE the slot, so a timed-out waiter is REFUNDED (limits.js::refundCharges).
  fresh();
  const cfgRef = wire2.readConfig(SHORT);
  const holdRef = await fillCeiling(cfgRef);
  const budgetBefore = { ...st().budget };
  const stranded = await admitChat(cfgRef, "198.51.100.30");
  eq(stranded.ok, false, "a request that waits out the clock is refused");
  deep(st().budget, budgetBefore, "THE UNIT BUDGET IS REFUNDED: a timed-out waiter spends no units");
  eq(stranded.rateLimit.remaining, cfgRef.chatPerMin, "…and its per-IP window too, so its X-RateLimit headers are TRUE");
  for (const h of holdRef) h.release();
  // Felt by a visitor: five turns a minute means five, even after one was queued and refused.
  fresh();
  const FIVE = { ...FULL, DEMO_QUEUE_MAX_WAIT_MS: "40", DEMO_QUEUE_MAX_DEPTH: "4" };
  const holdFive = await fillCeiling(wire2.readConfig(FIVE));
  eq((await call(chat, "/api/chat", { text: "hi" }, { "CF-Connecting-IP": "198.51.100.40" }, FIVE)).body.reason,
     "at_capacity", "the visitor waited and was refused");
  for (const h of holdFive) h.release();
  const five = [];
  for (let i = 0; i < 5; i++) five.push((await call(chat, "/api/chat", { text: "hi" }, { "CF-Connecting-IP": "198.51.100.40" }, FIVE)).res.status);
  deep(five, [200, 200, 200, 200, 200], "…and still has all 5 of their minute");

  // 13f. The ORDER that refund preserves: the per-IP window refuses FIRST, for free, so an
  // over-window request never occupies a queue slot a legitimate visitor needed.
  fresh();
  const cfgRl = wire2.readConfig({ ...SHORT, DEMO_CHAT_PER_MIN: "3" });
  for (let i = 0; i < 3; i++) (await admitChat(cfgRl, "198.51.100.50")).release();
  const holdRl = await fillCeiling(cfgRl);
  const overWindow = await within(admitChat(cfgRl, "198.51.100.50"), 100);
  eq(overWindow.reason, "rate_limited", "the per-IP window still refuses FIRST, instantly, even at the ceiling");
  deep([st().waiting.chat, st().stats.queue.joined], [0, 0], "…and a rate-limited request never reaches the queue");
  for (const h of holdRl) h.release();

  // 13g. A slot released from a THROWN path is handed to the next waiter (every route's
  // release() is in a `finally`) — here through the route, on an upstream timeout.
  fresh();
  P.plan = { chat: { throw: "TimeoutError" } };
  const holdT = [];
  for (let i = 0; i < 3; i++) holdT.push(await admitChat(qcfg, "203.0.113.4"));
  const routeP = chat.onRequestPost({ request: req("/api/chat", { text: "boom" }, { "CF-Connecting-IP": "198.51.100.70" }), env: QENV });
  const behind = admitChat(qcfg, "198.51.100.71");
  deep([st().inflight.chat, st().waiting.chat], [4, 1], "the failing turn holds the 4th slot, a visitor queued behind it");
  const failed = await routeP;
  await assertClean(failed, "/api/chat a failing turn with someone queued behind it");
  eq(failed.status, 504, "the failing turn answers 504 timeout");
  const nextUp = await behind;
  eq(nextUp.ok, true, "…and its slot goes straight to the queued visitor");
  for (const h of [nextUp, ...holdT]) h.release();
  deep([st().inflight.chat, st().waiting.chat], [0, 0], "no slot survives the failure and no waiter is stranded");
}

/* 14. WHO IS ASKING (§4.1, §4.6): an IPv6 visitor is one /56; a typed header is not an
 * identity; the credential never chases a `Location`. */
{
  // 14a. The address table — every form that reaches a real edge. The IPv4-mapped rows are
  // the silently catastrophic ones: wrong, and the whole v4 internet shares a bucket.
  for (const [raw, want, why] of [
    ["203.0.113.9", "203.0.113.9", "plain IPv4 is untouched"],
    ["  203.0.113.9  ", "203.0.113.9", "whitespace is trimmed"],
    ["1.2.3.4:5678", "1.2.3.4", "IPv4 with a port loses the port"],
    ["2001:db8:1:2:3:4:5:6", "2001:db8:1:0::/56", "a full IPv6 is truncated to its /56"],
    ["2001:db8:1:2:ffff:ffff:ffff:fff", "2001:db8:1:0::/56", "…and so is another host in the SAME /64"],
    ["2001:db8:1:3:3:4:5:6", "2001:db8:1:0::/56", "a DIFFERENT /64 of the same /56 shares the key: one delegation, one bucket"],
    ["2001:db8:1:ff::9", "2001:db8:1:0::/56", "…up to the last of its 256 /64s"],
    ["2001:db8:1:2ff:3:4:5:6", "2001:db8:1:200::/56", "the fourth hextet keeps its high byte"],
    ["2001:db8:1:100::1", "2001:db8:1:100::/56", "a DIFFERENT /56 keeps its own key"],
    ["2001:db8::1", "2001:db8:0:0::/56", "a `::` elision expands before truncation"],
    ["2001:0db8:0000:0000:0000:0000:0000:0001", "2001:db8:0:0::/56", "leading zeros normalise"],
    ["2001:DB8::1", "2001:db8:0:0::/56", "case normalises"],
    ["::1", "0:0:0:0::/56", "loopback parses"],
    ["::", "0:0:0:0::/56", "the unspecified address parses"],
    ["fe80::1%eth0", "fe80:0:0:0::/56", "a zone index names OUR interface, not the sender"],
    ["fe80::1%25eth0", "fe80:0:0:0::/56", "…including the percent-encoded spelling"],
    ["[2001:db8::1]:443", "2001:db8:0:0::/56", "the bracketed authority form loses brackets and port"],
    ["::ffff:1.2.3.4", "1.2.3.4", "IPv4-MAPPED unmaps to the v4 address, NOT to a /64"],
    ["::ffff:5.6.7.8", "5.6.7.8", "…so two mapped v4 clients stay two buckets"],
    ["::ffff:102:304", "1.2.3.4", "…the hex spelling too"],
    ["[::ffff:1.2.3.4]:80", "1.2.3.4", "…and the bracketed form"],
    ["::ffff:255.255.255.255", "255.255.255.255", "…at the top of the range"],
    [":::1", "unknown", "a triple colon is not an address (unknown SHARES a bucket)"],
    ["2001:db8:::1", "unknown", "nor is a doubled elision"],
    ["zz::1", "unknown", "nor is a non-hex group"],
    ["2001:db8:1:2:3:4:5:6:7", "unknown", "nor are nine groups"],
    ["", "unknown", "nor is an empty string"],
  ]) eq(limits.ipKey(raw), want, `ipKey(${JSON.stringify(raw)}): ${why}`);

  // 14b. …through the real windows: five turns from five /64s of ONE /56 spend its minute
  // (keyed by the /64, each got its own bucket — 256 of them per delegation), and a
  // different /56 is unaffected.
  fresh();
  const V6 = (n) => "2001:db8:cafe:" + n.toString(16) + "::1";
  for (let i = 1; i <= 5; i++) {
    eq((await call(chat, "/api/chat", { text: "hi" }, { "CF-Connecting-IP": V6(i) })).res.status, 200,
       `turn ${i} from a fresh /64 of one /56 is served`);
  }
  const sixth = await call(chat, "/api/chat", { text: "hi" }, { "CF-Connecting-IP": V6(0xff) });
  eq(`${sixth.res.status} ${sixth.body.reason}`, "429 rate_limited", "THE BYPASS IS CLOSED: a 6th /64 in the SAME /56 is refused");
  eq((await call(chat, "/api/chat", { text: "hi" }, { "CF-Connecting-IP": "2001:db8:cafe:100::1" })).res.status, 200,
     "a DIFFERENT /56 is a different visitor");

  // 14c. The refund credits the bucket the charge took: a timed-out /64 keeps its whole minute.
  fresh();
  const QQ = { ...FULL, DEMO_QUEUE_MAX_WAIT_MS: "40", DEMO_QUEUE_MAX_DEPTH: "4" };
  const cfgQ = wire2.readConfig(QQ);
  const holdQ = [];
  for (let i = 0; i < cfgQ.maxConcurrentChat; i++) {
    holdQ.push(await limits.admit({ request: req("/api/chat", { text: "x" }, { "CF-Connecting-IP": "203.0.113.4" }), cfg: cfgQ, route: "chat" }));
  }
  const budgetBefore = limits.__state().budget;
  eq((await call(chat, "/api/chat", { text: "hi" }, { "CF-Connecting-IP": "2001:db8:beef:7::a" }, QQ)).body.reason,
     "at_capacity", "the IPv6 visitor waited and was refused");
  deep(limits.__state().budget, budgetBefore, "…and the unit budget was refunded");
  for (const h of holdQ) h.release();
  for (let i = 1; i <= cfgQ.chatPerMin; i++) {
    eq((await call(chat, "/api/chat", { text: "hi" }, { "CF-Connecting-IP": "2001:db8:beef:7::" + i }, QQ)).res.status, 200,
       `…and the refunded /64 still has its minute, from any address in it: turn ${i}`);
  }

  // 14d. X-Forwarded-For is not an identity. Without CF-Connecting-IP every caller shares
  // one `unknown` bucket, throttled together rather than each given a lane.
  fresh();
  const noCf = (xff) => new Request(ORIGIN + "/api/chat", {
    method: "POST", body: JSON.stringify({ text: "hi" }),
    headers: { "Content-Type": "application/json", Origin: ORIGIN, "Sec-Fetch-Site": "same-origin", "X-Forwarded-For": xff },
  });
  const dfltCfg = wire2.readConfig(FULL);
  eq(dfltCfg.trustXff, false, "DEMO_TRUST_XFF is OFF by default");
  deep([limits.clientIp(noCf("9.9.9.9"), dfltCfg), limits.clientIp(noCf("8.8.8.8"), dfltCfg), limits.clientIp(noCf("9.9.9.9"))],
       ["unknown", "unknown", "unknown"], "a forged X-Forwarded-For is IGNORED (rotating it buys nothing; no cfg is conservative)");
  const unknown = [];
  for (let i = 1; i <= 6; i++) unknown.push((await chat.onRequestPost({ request: noCf("10.0.0." + i), env: FULL })).status);
  deep(unknown, [200, 200, 200, 200, 200, 429], "everything unidentifiable is throttled TOGETHER in one bucket");
  const trusting = wire2.readConfig({ ...FULL, DEMO_TRUST_XFF: "1" });
  deep([limits.clientIp(noCf("9.9.9.9, 8.8.8.8"), trusting), limits.clientIp(noCf("2001:db8:9:9:1:2:3:4"), trusting),
        limits.clientIp(req("/api/chat", {}, { "X-Forwarded-For": "9.9.9.9" }), trusting)],
       ["9.9.9.9", "2001:db8:9:0::/56", "203.0.113.9"],
       "DEMO_TRUST_XFF=1 (local dev) reads the first hop, through the /56 rule, and CF-Connecting-IP still OUTRANKS it");

  // 14e. The credential does not follow a redirect, and an unfollowed 3xx is the DOOR
  // (tunnel, Access, http->https), not the brain.
  fresh();
  const t = await call(chat, "/api/chat", { text: "hello" });
  await call(speech, "/api/speech", { ticket: t.body.speech[0].ticket });
  deep(sent.map((s) => s.opt.redirect), ["manual", "manual"], "both routes fetch with redirect:'manual'");
  for (const status of [301, 302, 303, 307, 308]) {
    fresh();
    P.plan = { chat: { status, body: "", headers: { Location: "https://elsewhere.invalid.test/v1/chat/completions" } } };
    const b = await call(chat, "/api/chat", { text: "hello" });
    eq(`${b.res.status} ${b.body.reason} ${sent.length}`, "503 gateway_unreachable_or_gated 1",
       `/api/chat reads an upstream ${status} as a door problem, having made ONE call`);
  }
  fresh();
  const t2 = await call(chat, "/api/chat", { text: "hello" });
  sent.length = 0;
  P.plan = { speech: { status: 302, body: "", headers: { Location: "https://elsewhere.invalid.test/v1/audio/speech" } } };
  const bt = await call(speech, "/api/speech", { ticket: t2.body.speech[0].ticket });
  eq(`${bt.res.status} ${bt.body.reason} ${sent.length}`, "503 gateway_unreachable_or_gated 1", "/api/speech reads a 302 the same way");
}
