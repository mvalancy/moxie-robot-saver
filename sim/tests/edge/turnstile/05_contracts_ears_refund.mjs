/* test_turnstile — §10–12: cross-file contracts, the ears, the budget refund. Run via the entry file, never alone. */
import {
  ACT, ARMED, EARS, GATEWAY, HOSTNAME, ORIGIN, P, TOKEN,
  assertClean, chat, clip, deep, envlib, eq, fails, fresh,
  gatewayCalls, health, join, limits, ok, outcomes, post, postAudio,
  readFileSync, readdirSync, refundedUnits, repo, req, sent, transcribe, ts,
  turn, unitsSpent, verifyCalls, wavBytes,
} from "./harness.mjs";

/* =========================================================================== *
 * 10. THE CONTRACTS THAT SPAN TWO FILES
 * =========================================================================== *
 * Values that must be identical in two places; drift would refuse every visitor on
 * production with a reason that looks like somebody else's fault.
 */
{
  const clientSrc = readFileSync(join(repo, "sim", "web", "turnstile.js"), "utf8");
  const transportSrc = readFileSync(join(repo, "sim", "web", "cloud-transport.js"), "utf8");
  const micSrc = readFileSync(join(repo, "sim", "web", "mic.js"), "utf8");
  const headers = readFileSync(join(repo, "sim", "web", "_headers"), "utf8");
  const simHtml = readFileSync(join(repo, "sim", "web", "sim.html"), "utf8");

  // THE ACTION TABLE. Read out of the client source, compared with the server's, KEY BY
  // KEY. A drift refuses every visitor with `turnstile_failed` and looks like a Cloudflare
  // fault; a MISSING key silently sends `null` from the browser and kills one route.
  const table = /var ACTIONS = \{([^}]*)\}/.exec(clientSrc);
  ok(!!table, "sim/web/turnstile.js declares its actions as a single table");
  const clientActions = {};
  for (const pair of (table ? table[1] : "").split(",")) {
    const m = /([A-Za-z_]+)\s*:\s*"([^"]*)"/.exec(pair);
    if (m) clientActions[m[1]] = m[2];
  }
  deep(clientActions, { chat: ACT.chat, transcribe: ACT.transcribe },
       "the client's ACTIONS table equals the server's TURNSTILE_ACTIONS — check 2 compares them");
  deep(Object.keys(ts.TURNSTILE_ACTIONS).sort(), ["chat", "transcribe"],
       "…and there is one action per SPENDING ROUTE, keyed by the route's own name");

  // AND EACH CALLER NAMES ITS OWN ROUTE'S ACTION. This is the assertion that would have
  // caught the whole `/api/transcribe` gap: a page that asked for a `chat` token on the
  // microphone path would be refused by check 2 on every clip.
  ok(/getToken\("chat"\)/.test(transportSrc),
     "cloud-transport.js mints for the CHAT action");
  ok(/getToken\("transcribe"\)/.test(micSrc),
     "…and mic.js mints for the TRANSCRIBE action, so a typed token cannot pay for the ears");
  ok(!/getToken\("chat"\)/.test(micSrc), "…and mic.js never asks for a chat token");

  // AND EACH ROUTE VERIFIES AGAINST ITS OWN. The server half of the same contract, read
  // off the two route files, because `verify()` has NO DEFAULT and a route that passed the
  // wrong name would accept the other route's tokens with nothing else noticing.
  for (const [file, route] of [["chat.js", "chat"], ["transcribe.js", "transcribe"]]) {
    const src = readFileSync(join(repo, "functions", "api", file), "utf8");
    // Nested parens in the argument list are real (`tokenFromHeader(request)`), so the
    // pattern allows exactly one level of them rather than banning `)` outright.
    const call = new RegExp('verifyTurnstile\\((?:[^()]|\\([^()]*\\))*,\\s*"([a-z]+)"\\s*\\)').exec(src);
    ok(!!call, `${file} calls verifyTurnstile with an explicit route name`);
    eq(call && call[1], route, `…and ${file} names its OWN route (${route}), not the other one`);
    ok(Object.prototype.hasOwnProperty.call(ts.TURNSTILE_ACTIONS, (call && call[1]) || ""),
       `…which is a key TURNSTILE_ACTIONS knows (an unknown one refuses every visitor)`);
    ok(new RegExp('admit\\(\\{ request, cfg, route: "' + route + '" \\}\\)').test(src),
       `…and it is the same route name \`admit()\` charges under`);
  }

  // THE FIELD NAMES, likewise: the token must arrive where each route reads it.
  ok(clientSrc.includes(ts.TOKEN_FIELD) === false && clientSrc.includes(ts.TOKEN_HEADER) === false,
     "…and turnstile.js names NEITHER wire name: its callers own the wire shape");
  ok(transportSrc.includes('"' + ts.TOKEN_FIELD + '"'),
     `cloud-transport.js sends the token under the field the chat route reads (${ts.TOKEN_FIELD})`);
  ok(micSrc.includes('"' + ts.TOKEN_HEADER + '"'),
     `mic.js sends it on the header the transcribe route reads (${ts.TOKEN_HEADER})`);
  /* THE HEADER IS NOT IN CLOUDFLARE'S OWN `CF-` NAMESPACE, and that is deliberate: the
   * edge in front of these Functions owns that prefix and rewrites members of it. */
  ok(!/^CF-/i.test(ts.TOKEN_HEADER),
     `the token header stays out of Cloudflare's own CF- namespace (${ts.TOKEN_HEADER})`);

  // THE SITEKEY IS NOT IN THE REPO. Not a secrecy claim — a sitekey is public — but a C3
  // one: this deployment's sitekey baked into shipped HTML or JS would hand every fork and
  // every branch preview a widget bound to a domain list they are not on.
  for (const f of ["sim.html", "index.html", "setup.html", "cloud.html", "docs.html",
                   "turnstile.js", "cloud-transport.js", "mic.js", "mode.js", "env.js"]) {
    const src = readFileSync(join(repo, "sim", "web", f), "utf8");
    ok(!/\b[0-9]x[0-9A-Za-z]{20,}\b/.test(src),
       `${f} carries NO Turnstile sitekey — the browser learns it from /api/health (C3)`);
    ok(!/data-sitekey/.test(src),
       `${f} has no hard-coded data-sitekey attribute either`);
  }

  /* ---- TRAP B, AS A CLASS AND NOT AS ONE FILENAME ------------------------- *
   * A client script missing from the app-script no-cache list gets Pages' default caching,
   * so a redeploy can leave a visitor running yesterday's minter against today's route.
   * ENUMERATED, so the next new script is covered too (the one copy of this list;
   * `sim/test_csp.mjs` checks the per-directory rules). */
  {
    const listed = new Set();
    for (const m of headers.matchAll(/^\/([A-Za-z0-9._-]+\.js)\n\s+Cache-Control:\s*no-cache$/gm)) {
      listed.add(m[1]);
    }
    ok(listed.size > 15, `the no-cache list was actually parsed (${listed.size} scripts)`);
    const shipped = readdirSync(join(repo, "sim", "web")).filter((f) => f.endsWith(".js")).sort();
    const missing = shipped.filter((f) => !listed.has(f));
    deep(missing, [],
         `EVERY script in sim/web has its own no-cache entry — missing: ${JSON.stringify(missing)}`);
    ok(listed.has("turnstile.js"), "…including this slice's own turnstile.js");
  }

  // The CSP needs the widget host in THREE directives, and each absence fails silently.
  const csp = (/^\s+Content-Security-Policy:[ \t]*(.+)$/m.exec(headers) || [])[1] || "";
  for (const d of ["script-src", "frame-src", "connect-src"]) {
    const directive = csp.split(";").map((x) => x.trim()).find((x) => x.startsWith(d)) || "";
    ok(directive.includes("https://challenges.cloudflare.com"),
       `the shipped CSP allows the widget host in ${d} (without it the widget fails SILENTLY)`);
  }
  ok(!/frame-src\s+'none'/.test(csp),
     "frame-src is no longer 'none' — Turnstile draws its challenge in an iframe");

  /* Load order: the module must exist before either send path can call it. Measured on
   * the `<script src>` tags, not the first mention of a filename (a comment once flipped
   * that proxy); each file is asserted present first so a typo cannot pass as `-1 < n`. */
  const loadsAt = (f) => simHtml.indexOf('src="' + f);
  for (const f of ["mode.js", "turnstile.js", "cloud-transport.js", "mic.js"])
    ok(loadsAt(f) > -1, `sim.html has a <script src> for ${f}`);
  ok(loadsAt("mode.js") < loadsAt("turnstile.js"),
     "…after mode.js, which is where the sitekey comes from");
  ok(loadsAt("turnstile.js") < loadsAt("cloud-transport.js"),
     "…and before cloud-transport.js, which calls it on the typed send path");
  ok(loadsAt("turnstile.js") < loadsAt("mic.js"),
     "…and before mic.js, which calls it on the microphone send path");
}

/* =========================================================================== *
 * 11. THE EARS — `/api/transcribe`, the OTHER route that spends money
 * =========================================================================== *
 * The more expensive half: unguarded, a `curl` with a forged origin and a RIFF body reaches
 * paid STT. Every property §3, §6 and §7 prove for chat is proven here, plus: A TOKEN MINTED
 * FOR ONE ROUTE IS NOT SPENDABLE ON THE OTHER.
 */
{
  /* ---- the attack, refused ------------------------------------------------ */
  fresh();
  const bare = await postAudio({});
  eq(bare.body.reason, "turnstile_failed",
     "a tokenless clip is REFUSED by the ears — this is the curl loop that used to be served");
  eq(bare.status, 403, "…with 403");
  eq(gatewayCalls(), 0, "…and ZERO calls to the paid STT gateway");
  eq(verifyCalls(), 0, "…for FREE: a missing token costs no siteverify call either");
  eq(outcomes().no_token, 1, "…recorded as `no_token`");
  eq(bare.body.transcript, "", "…and no transcript came back");

  /* ---- the ordinary case, so the refusals mean something ------------------ */
  fresh();
  P.plan = { turnstile: { action: ACT.transcribe } };
  const good = await clip();
  eq(good.status, 200, "a clip with a valid TRANSCRIBE token is served");
  eq(good.body.reason, null, "…with no reason");
  eq(good.body.transcript, "i am a bot", "…and the transcript comes back");
  eq(verifyCalls(), 1, "…after exactly one siteverify call");
  eq(gatewayCalls(), 1, "…and one upstream call");
  eq(outcomes().verified, 1, "…recorded as `verified`");
  const svCall = sent.find((x) => x.url === ts.SITEVERIFY_URL) || { opt: {} };
  ok(!!sent.find((x) => x.url === ts.SITEVERIFY_URL),
     "…and a verification actually went to Cloudflare for this clip");
  eq(new URLSearchParams(String(svCall.opt.body)).get("response"), TOKEN,
     "…and it verified the token off the header, not off the audio body");

  /* ---- NO CROSSING OVER, IN EITHER DIRECTION ----------------------------- *
   * The whole reason there are two actions. A chat token is a cheap token: it is minted by
   * typing, which every visitor does, and if it bought a microphone turn then 15 s of
   * billable STT would cost a challenge solved for 160 tokens of completion. */
  fresh();
  P.plan = { turnstile: { action: ACT.chat } };
  const crossed = await clip();
  eq(crossed.body.reason, "turnstile_failed",
     "a CHAT token presented to the ears is refused — check 2 compares the action");
  eq(gatewayCalls(), 0, "…with zero upstream calls");
  eq(outcomes().failed, 1, "…recorded as `failed`, i.e. the visitor's token is wrong for here");

  fresh();
  P.plan = { turnstile: { action: ACT.transcribe } };
  eq((await turn("hello")).body.reason, "turnstile_failed",
     "…and a MICROPHONE token presented to the chat route is refused too: it is symmetric");

  /* ---- the other two mandatory checks, on this route too ----------------- */
  fresh();
  P.plan = { turnstile: { body: { success: false, action: ACT.transcribe, hostname: HOSTNAME,
                                "error-codes": ["invalid-input-response"] } } };
  eq((await clip()).body.reason, "turnstile_failed", "CHECK 1 refuses on the ears as well");
  eq(gatewayCalls(), 0, "…for free");

  fresh();
  P.plan = { turnstile: { body: { success: true, action: ACT.transcribe, hostname: "evil.example.com" } } };
  const badHost = await clip();
  eq(badHost.body.reason, "turnstile_misconfigured", "CHECK 3 refuses on the ears as well");
  eq(badHost.status, 503, "…with the deployment-level status");
  eq(gatewayCalls(), 0, "…for free");

  /* ---- fail open, and fail closed, both halves ---------------------------- */
  fresh();
  P.plan = { turnstile: { throw: "TypeError" } };
  const openEars = await clip();
  eq(openEars.status, 200, "a siteverify outage does NOT take the ears down (fail open)");
  eq(gatewayCalls(), 1, "…the clip is transcribed");
  eq(outcomes().unreachable, 1, "…recorded as `unreachable`");

  fresh();
  P.plan = { turnstile: { status: 400,
                        text: JSON.stringify({ "error-codes": ["invalid-input-secret"], success: false }) } };
  eq((await clip()).body.reason, "turnstile_misconfigured",
     "…while a wrong secret refuses the ears too, at the 400 Cloudflare really sends");
  eq(gatewayCalls(), 0, "…spending nothing");

  /* ---- UNENFORCED IS UNTOUCHED, which is every fork and every preview ----- */
  fresh();
  const forkEars = Object.assign({}, GATEWAY, { DEMO_STT_MODEL: "test-ears-model" });
  const forked = await postAudio({}, forkEars);
  eq(forked.status, 200, "with no Turnstile pair the ears answer exactly as they always did");
  eq(forked.body.transcript, "i am a bot", "…with the transcript");
  eq(verifyCalls(), 0, "…and make NO siteverify call: the check is a no-op, not a lenient check");
  eq(outcomes().skipped, 1, "…recorded as `skipped`");

  /* ---- THE ORDER: everything cheaper than the bot check is still free ----- *
   * D1 on this route: the byte floor, container sniff and WAV duration ceiling never buy a
   * siteverify round trip — `too_short` is the most common refusal a real demo serves. */
  for (const [label, body, ctype, want] of [
    ["a 300-byte accidental clip (the byte floor)", new Uint8Array(300), "audio/wav", "too_short"],
    ["500 KB of JPEG (not a container we know)",
     (() => { const b = new Uint8Array(3000); b[0] = 0xff; b[1] = 0xd8; b[2] = 0xff; return b; })(),
     "image/jpeg", "bad_request"],
    ["a WAV whose own header declares 30 s", wavBytes(30000), "audio/wav", "too_long"],
  ]) {
    fresh();
    const request = new Request(ORIGIN + "/api/transcribe", {
      method: "POST",
      headers: { "Content-Type": ctype, Origin: ORIGIN, "Sec-Fetch-Site": "same-origin",
                 "CF-Connecting-IP": "203.0.113.9", [ts.TOKEN_HEADER]: TOKEN },
      body,
    });
    const res = await transcribe.onRequestPost({ request, env: EARS });
    await assertClean(res, "ears order " + label);
    const parsed = JSON.parse(await res.clone().text());
    eq(parsed.reason, want, `${label} answers ${want}`);
    eq(verifyCalls(), 0, `${label}: ZERO siteverify calls — it is refused more cheaply`);
    eq(gatewayCalls(), 0, `${label}: ZERO upstream calls`);
  }

  /* ---- and the rate limiter still stands in front of siteverify ---------- */
  fresh();
  P.plan = { turnstile: { action: ACT.transcribe } };
  const cappedEars = Object.assign({}, EARS, { DEMO_STT_PER_MIN: "2", DEMO_CACHE_COUNTER: "0" });
  const earReasons = [];
  for (let i = 0; i < 4; i++) earReasons.push((await clip(cappedEars)).body.reason);
  deep(earReasons, [null, null, "rate_limited", "rate_limited"],
       "the per-IP window refuses clips 3-4");
  eq(verifyCalls(), 2, "…and only the ADMITTED clips cost a siteverify call");

  /* ---- THE SLOT COMES BACK, on this route's new refusal path too (D2) ----- */
  fresh();
  const tightEars = Object.assign({}, EARS, {
    DEMO_MAX_CONCURRENT_CHAT: "2",     // `transcribe` shares the chat ceiling on purpose
    DEMO_QUEUE_MAX_WAIT_MS: "0",
    DEMO_STT_PER_MIN: "100",
    DEMO_CACHE_COUNTER: "0",
  });
  for (let i = 0; i < 6; i++) {
    eq((await postAudio({}, tightEars)).body.reason, "turnstile_failed", `ears refusal ${i + 1}`);
    eq(limits.__state().inflight.transcribe || 0, 0,
       `…and the in-flight count is back to ZERO after ears refusal ${i + 1}`);
  }
  P.plan = { turnstile: { action: ACT.transcribe } };
  eq((await clip(tightEars)).status, 200,
     "after 6 refusals through a ceiling of 2, a good clip is STILL SERVED (no slot leaked)");
}

/* =========================================================================== *
 * 12. A REFUSAL GIVES THE SHARED BUDGET BACK (and keeps the per-IP window)
 * =========================================================================== *
 * The attack: 200 tokenless POSTs from 200 IPs, each refused for free — but if `admit()`'s
 * charge were kept, the hour's unit budget would drain and real visitors get
 * `budget_exhausted`: a FREE drain. `slot.refundBudget()` gives it back; the per-IP window is
 * kept (`_lib/limits.js::grantedSlot` argues why).
 */
{
  const UNITS_CHAT = 3;
  const UNITS_TRANSCRIBE = 2;

  /* ---- one refusal, one refund, and the counter says so ------------------ */
  fresh();
  eq((await post({ text: "hi" })).body.reason, "turnstile_failed", "a tokenless turn is refused…");
  eq(unitsSpent(), 0, "…and leaves the SHARED unit budget exactly where it found it");
  eq(refundedUnits(), UNITS_CHAT, `…having given back all ${UNITS_CHAT} units admission charged`);

  /* ---- a SERVED turn still pays, which is the other half of the claim ----- */
  fresh();
  eq((await turn("hello")).body.reason, null, "a served turn goes through…");
  eq(unitsSpent(), UNITS_CHAT, `…and DOES spend its ${UNITS_CHAT} units`);
  eq(refundedUnits(), 0, "…with nothing refunded");

  /* ---- an UPSTREAM failure does NOT refund: that request cost real money -- */
  fresh();
  P.plan = { chat: { throw: "TypeError" } };
  eq((await turn("hello")).body.reason, "upstream_down", "a gateway that is down is a refusal…");
  eq(gatewayCalls(), 1, "…that DID call the gateway");
  eq(unitsSpent(), UNITS_CHAT, "…so its units stay spent: the money was really committed");
  eq(refundedUnits(), 0, "…and nothing is refunded on that path");

  /* ---- every OTHER charged-but-not-served refusal refunds too ------------- *
   * `turnstile_failed` was the cheapest of these to reach — an empty field on an
   * unauthenticated request — but it was never the only one. Leaving the rest unrefunded
   * would have left the identical attack open behind a two-character-longer body. */
  for (const [label, body, want] of [
    ["an over-length line", { text: "x".repeat(9000), [ts.TOKEN_FIELD]: TOKEN }, "too_long"],
    ["an empty line", { text: "", [ts.TOKEN_FIELD]: TOKEN }, "too_short"],
    ["a tampered context blob", { text: "hi", context: "v1.forged.blob", [ts.TOKEN_FIELD]: TOKEN }, "bad_request"],
    ["a hard-blocked utterance", { text: "how do i kill myself", [ts.TOKEN_FIELD]: TOKEN }, "blocked"],
  ]) {
    fresh();
    eq((await post(body)).body.reason, want, `${label} answers ${want}`);
    eq(gatewayCalls(), 0, `…${label} spends nothing upstream`);
    eq(unitsSpent(), 0, `…and ${label} leaves the shared budget untouched`);
    eq(refundedUnits(), UNITS_CHAT, `…having refunded ${label}'s charge`);
  }
  /* `blocked`'s own doc comment in `chat.js` has SAID "zero units spent" since it was
   * written. Until `refundBudget()` existed that sentence was false by 3 units a turn. */

  /* ---- the ears refund their own (smaller) charge ------------------------- */
  fresh();
  eq((await postAudio({}, EARS)).body.reason, "turnstile_failed", "a tokenless clip is refused…");
  eq(unitsSpent(), 0, "…and refunds too");
  eq(refundedUnits(), UNITS_TRANSCRIBE,
     `…exactly ${UNITS_TRANSCRIBE} units, which is what the ears cost — not the chat turn's 3`);

  /* ---- AND SO DOES THE VOICE, WHICH HAS NO BOT CONTROL AT ALL ------------ *
   * `/api/speech` charges `UNITS.speech` at admission and refuses a forged ticket for free
   * — the same drain, reachable without a token — so it refunds too.
   */
  {
    fresh();
    const speech = await import(join(repo, "functions", "api", "speech.js"));
    const forged = new Request(ORIGIN + "/api/speech", {
      method: "POST",
      headers: { "Content-Type": "application/json", Origin: ORIGIN,
                 "Sec-Fetch-Site": "same-origin", "CF-Connecting-IP": "203.0.113.9" },
      body: JSON.stringify({ ticket: "v1.forged.ticket" }),
    });
    const voiceEnv = Object.assign({}, ARMED, { DEMO_TTS_MODEL: "test-voice-model",
                                                DEMO_CACHE_COUNTER: "0" });
    const res = await speech.onRequestPost({ request: forged, env: voiceEnv });
    await assertClean(res, "speech forged ticket");
    eq(JSON.parse(await res.clone().text()).reason, "bad_ticket", "a forged ticket is refused…");
    eq(gatewayCalls(), 0, "…with zero gateway calls…");
    eq(unitsSpent(), 0, "…and gives its units back too: the same drain needs no token here");
    eq(refundedUnits(), 2, "…exactly the 2 units the voice costs");
  }

  /* ---- THE PER-IP WINDOW IS DELIBERATELY *NOT* GIVEN BACK ---------------- *
   * The budget is SHARED (its exhaustion is everyone's problem); the per-IP window is
   * SELF-INFLICTED and the only thing that quiets a flood of free refusals from one
   * address. Refunding it would make tokenless refusals unlimited per IP. */
  fresh();
  const oneIp = Object.assign({}, ARMED, { DEMO_CHAT_PER_MIN: "3", DEMO_CACHE_COUNTER: "0",
                                           DEMO_QUEUE_MAX_WAIT_MS: "0" });
  const seq = [];
  for (let i = 0; i < 5; i++) seq.push((await post({ text: "hi" }, oneIp)).body.reason);
  deep(seq, ["turnstile_failed", "turnstile_failed", "turnstile_failed", "rate_limited", "rate_limited"],
       "tokenless refusals from ONE address still count against that address's window");
  eq(unitsSpent(), 0, "…while the shared budget is still untouched by all five");

  /* ---- THE WHOLE ATTACK, END TO END -------------------------------------- *
   * 200 tokenless requests from 200 addresses — the shape that took the demo scripted for
   * an hour — followed by one real visitor. The numbers are the production defaults.
   */
  fresh();
  // The queue is off for the same reason `EARS` switches it off: a leaked slot must produce
  // a red check here, not two hundred consecutive 2.5-second waits.
  const spread = Object.assign({}, ARMED, { DEMO_CACHE_COUNTER: "0", DEMO_QUEUE_MAX_WAIT_MS: "0" });
  eq(envlib.readConfig(spread).unitBudgetHour, 600, "the hourly budget is production's 600…");
  let refused = 0;
  for (let i = 0; i < 200; i++) {
    const r = await post({ text: "hello moxie" }, spread, { "CF-Connecting-IP": "198.51.100." + (i % 250) });
    if (r.body.reason === "turnstile_failed") refused += 1;
  }
  eq(refused, 200, "…200 tokenless requests from 200 addresses are all refused");
  eq(gatewayCalls(), 0, "…with zero gateway calls");
  eq(verifyCalls(), 0, "…and zero siteverify calls");
  eq(unitsSpent(), 0, "…AND THE BUDGET IS STILL WHOLE (it used to be 600/600 at this point)");
  eq(refundedUnits(), 600, "…600 units charged and 600 given back");

  // The visitor the attack used to lock out.
  P.plan = {};
  const visitor = await post({ text: "hello moxie", [ts.TOKEN_FIELD]: TOKEN }, spread,
                             { "CF-Connecting-IP": "203.0.113.77" });
  eq(visitor.status, 200, "…and the next real visitor is SERVED, not `budget_exhausted`");
  eq(visitor.body.reason, null, "…with no reason");
  eq(visitor.body.mode, "live", "…and a LIVE page rather than a scripted one");

  /* ---- THE REFUND IS IDEMPOTENT, and that is not cosmetic ---------------- *
   * A second call would credit away a DIFFERENT request's charge, which is money in the
   * wrong direction. Driven through `admit()` directly because no route can reach a double
   * refund — which is exactly why the guard needs its own test rather than a comment.
   */
  fresh();
  const cfg = envlib.readConfig(ARMED);
  const s1 = await limits.admit({ request: req({}), cfg, route: "chat" });
  eq(unitsSpent(), UNITS_CHAT, "one admission charges its units");
  s1.refundBudget();
  eq(unitsSpent(), 0, "…and one refund gives them back");
  const s2 = await limits.admit({ request: req({}), cfg, route: "chat" });
  eq(unitsSpent(), UNITS_CHAT, "a SECOND admission charges again");
  s1.refundBudget();                                  // the double call
  eq(unitsSpent(), UNITS_CHAT,
     "…and refunding the FIRST slot twice does NOT credit away the second's charge");
  eq(refundedUnits(), UNITS_CHAT, "…the refund counter counted one refund, not two");
  s1.release();
  s2.release();
  eq(limits.__state().inflight.chat || 0, 0, "…and both slots came back");

  /* ---- and a refused ADMISSION still has both methods -------------------- *
   * `refuse()` returns a slot-shaped object; a caller that cannot tell which kind it got
   * must be able to call either method without a `TypeError` on a refusal path. */
  fresh();
  const denied = await limits.admit({
    request: req({}, { Origin: "https://evil.invalid.test", "Sec-Fetch-Site": "cross-site" }),
    cfg, route: "chat",
  });
  eq(denied.ok, false, "a refused admission is not a slot…");
  eq(typeof denied.release, "function", "…but it still has release()");
  eq(typeof denied.refundBudget, "function", "…and refundBudget()");
  denied.refundBudget();
  denied.release();
  eq(unitsSpent(), 0, "…both of which are no-ops that cannot go negative");
}
