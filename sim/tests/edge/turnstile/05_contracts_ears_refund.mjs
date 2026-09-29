/* test_turnstile — §10–12: cross-file contracts, the ears, the budget refund. Run via the entry file, never alone. */
import {
  ACT, ARMED, EARS, GATEWAY, HOSTNAME, ORIGIN, P, TOKEN, assertClean, clip, deep, envlib, eq,
  fresh, gatewayCalls, join, limits, ok, outcomes, post, postAudio, readFileSync, readdirSync,
  refundedUnits, repo, req, sent, transcribe, ts, turn, unitsSpent, verifyCalls, wavBytes,
} from "./harness.mjs";

/* =========================================================================== *
 * 10. THE CONTRACTS THAT SPAN FILES — drift refuses every visitor on production with a
 *     reason that looks like somebody else's fault. (The action table itself is §9's.)
 * =========================================================================== */
{
  const web = (f) => readFileSync(join(repo, "sim", "web", f), "utf8");
  const transportSrc = web("cloud-transport.js");
  const micSrc = web("mic.js");

  // Each caller mints for its OWN route (a chat token on the mic path is refused by check 2
  // on every clip), and sends it where that route reads it.
  ok(/getToken\("transcribe"\)/.test(micSrc) && !/getToken\("chat"\)/.test(micSrc),
     "mic.js mints for the TRANSCRIBE action, never a chat token");
  ok(/getToken\("chat"\)/.test(transportSrc), "cloud-transport.js mints for the CHAT action");
  ok(transportSrc.includes('"' + ts.TOKEN_FIELD + '"'),
     `cloud-transport.js sends the token under the field the chat route reads (${ts.TOKEN_FIELD})`);
  ok(micSrc.includes('"' + ts.TOKEN_HEADER + '"'),
     `mic.js sends it on the header the transcribe route reads (${ts.TOKEN_HEADER})`);
  // The edge in front of the Functions owns the CF- prefix and rewrites members of it.
  ok(!/^CF-/i.test(ts.TOKEN_HEADER), `the token header stays out of Cloudflare's own CF- namespace (${ts.TOKEN_HEADER})`);

  // THE SITEKEY IS NOT IN THE REPO (C3): a baked-in one would hand every fork and preview a
  // widget bound to a domain list they are not on. The browser learns it from /api/health.
  const baked = ["sim.html", "index.html", "setup.html", "cloud.html", "docs.html",
                 "turnstile.js", "cloud-transport.js", "mic.js", "mode.js", "env.js"]
    .filter((f) => /\b[0-9]x[0-9A-Za-z]{20,}\b|data-sitekey/.test(web(f)));
  deep(baked, [], "no shipped page or script carries a Turnstile sitekey or a data-sitekey attribute");

  // TRAP B, as a class: a script missing from the no-cache list can run yesterday's minter
  // against today's route after a redeploy (test_csp.mjs checks the same in a browser).
  const listed = new Set();
  for (const m of web("_headers").matchAll(/^\/([A-Za-z0-9._-]+\.js)\n\s+Cache-Control:\s*no-cache$/gm)) listed.add(m[1]);
  ok(listed.size > 15, `the no-cache list was actually parsed (${listed.size} scripts)`);
  const missing = readdirSync(join(repo, "sim", "web")).filter((f) => f.endsWith(".js") && !listed.has(f));
  deep(missing, [], `EVERY script in sim/web has its own no-cache entry — missing: ${JSON.stringify(missing)}`);

  // Load order: each module exists before the code that calls it (measured on <script src>).
  const html = web("sim.html");
  const at = ["bridge/index.js", "mode.js", "turnstile.js", "cloud-transport.js", "mic.js"].map((f) => html.indexOf('src="' + f));
  ok(at.every((n) => n > -1), `sim.html has a <script src> for bridge/, mode.js, turnstile.js, cloud-transport.js and mic.js (${at})`);
  ok(at[0] < at[1] && at[1] < at[2] && at[2] < at[3] && at[2] < at[4],
     "sim.html loads bridge/ < mode.js (the sitekey) < turnstile.js < both send paths that call it");
}

/* =========================================================================== *
 * 11. THE EARS — `/api/transcribe`, the other route that spends money. Every property §3,
 *     §6 and §7 prove for chat, plus: a token minted for one route is not spendable on the other.
 * =========================================================================== */
{
  const earsUnder = async (plan, env) => { fresh(); P.plan = plan || {}; return clip(env); };

  fresh();
  const bare = await postAudio({});
  eq(bare.body.reason, "turnstile_failed", "a tokenless clip is REFUSED by the ears — the curl loop that used to be served");
  deep([bare.status, gatewayCalls(), verifyCalls(), outcomes().no_token, bare.body.transcript], [403, 0, 0, 1, ""],
       "…403, ZERO paid STT calls, no siteverify call either, recorded `no_token`, no transcript");

  const good = await earsUnder({ turnstile: { action: ACT.transcribe } });
  deep([good.status, good.body.reason, good.body.transcript], [200, null, "i am a bot"], "a clip with a valid TRANSCRIBE token is served");
  deep([verifyCalls(), gatewayCalls(), outcomes().verified], [1, 1, 1], "…after one siteverify call and one upstream call, recorded `verified`");
  const sv = sent.find((x) => x.url === ts.SITEVERIFY_URL) || { opt: {} };
  eq(new URLSearchParams(String(sv.opt.body)).get("response"), TOKEN, "…verifying the token off the header, not the audio body");

  // NO CROSSING OVER: a chat token is cheap (every visitor types); it must not buy 15 s of STT.
  const crossed = await earsUnder({ turnstile: { action: ACT.chat } });
  eq(crossed.body.reason, "turnstile_failed", "a CHAT token presented to the ears is refused — check 2 compares the action");
  deep([gatewayCalls(), outcomes().failed], [0, 1], "…with zero upstream calls, recorded `failed`");
  fresh();
  P.plan = { turnstile: { action: ACT.transcribe } };
  eq((await turn("hello")).body.reason, "turnstile_failed", "…and a MICROPHONE token presented to the chat route is refused too");

  for (const [label, plan, want] of [
    ["CHECK 1 (success:false)", { turnstile: { body: { success: false, action: ACT.transcribe, hostname: HOSTNAME, "error-codes": ["invalid-input-response"] } } }, "turnstile_failed"],
    ["CHECK 3 (foreign host)", { turnstile: { body: { success: true, action: ACT.transcribe, hostname: "evil.example.com" } } }, "turnstile_misconfigured"],
    ["a wrong secret at Cloudflare's real 400", { turnstile: { status: 400, text: JSON.stringify({ "error-codes": ["invalid-input-secret"], success: false }) } }, "turnstile_misconfigured"],
  ]) {
    const r = await earsUnder(plan);
    deep([r.body.reason, gatewayCalls()], [want, 0], `${label} refuses on the ears as well, for free`);
  }
  const open = await earsUnder({ turnstile: { throw: "TypeError" } });
  deep([open.status, gatewayCalls(), outcomes().unreachable], [200, 1, 1], "a siteverify outage does NOT take the ears down (fail open)");

  // Unenforced is untouched: every fork and every preview.
  fresh();
  const forked = await postAudio({}, Object.assign({}, GATEWAY, { DEMO_STT_MODEL: "test-ears-model" }));
  deep([forked.status, forked.body.transcript, verifyCalls(), outcomes().skipped], [200, "i am a bot", 0, 1],
       "with no Turnstile pair the ears answer as they always did, with NO siteverify call");

  // D1 on this route: the byte floor, container sniff and WAV duration ceiling are free.
  const jpeg = new Uint8Array(3000);
  jpeg.set([0xff, 0xd8, 0xff]);
  for (const [label, body, ctype, want] of [
    ["a 300-byte accidental clip (the byte floor)", new Uint8Array(300), "audio/wav", "too_short"],
    ["a JPEG (not a container we know)", jpeg, "image/jpeg", "bad_request"],
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
    eq(JSON.parse(await res.clone().text()).reason, want, `${label} answers ${want}`);
    deep([verifyCalls(), gatewayCalls()], [0, 0], `${label}: ZERO siteverify and upstream calls — it is refused more cheaply`);
  }

  fresh();
  P.plan = { turnstile: { action: ACT.transcribe } };
  const cappedEars = Object.assign({}, EARS, { DEMO_STT_PER_MIN: "2", DEMO_CACHE_COUNTER: "0" });
  const earReasons = [];
  for (let i = 0; i < 4; i++) earReasons.push((await clip(cappedEars)).body.reason);
  deep([earReasons, verifyCalls()], [[null, null, "rate_limited", "rate_limited"], 2],
       "the per-IP window refuses clips 3-4, and only the ADMITTED clips cost a siteverify call");

  // D2 on this route (`transcribe` shares the chat ceiling on purpose).
  fresh();
  const tightEars = Object.assign({}, EARS, { DEMO_MAX_CONCURRENT_CHAT: "2", DEMO_QUEUE_MAX_WAIT_MS: "0",
                                              DEMO_STT_PER_MIN: "100", DEMO_CACHE_COUNTER: "0" });
  for (let i = 0; i < 6; i++) {
    eq((await postAudio({}, tightEars)).body.reason, "turnstile_failed", `ears refusal ${i + 1}`);
    eq(limits.__state().inflight.transcribe || 0, 0, `…and the in-flight count is back to ZERO after ears refusal ${i + 1}`);
  }
  P.plan = { turnstile: { action: ACT.transcribe } };
  eq((await clip(tightEars)).status, 200, "after 6 refusals through a ceiling of 2, a good clip is STILL SERVED (no slot leaked)");
}

/* =========================================================================== *
 * 12. A REFUSAL GIVES THE SHARED BUDGET BACK (and keeps the per-IP window) — otherwise 200
 *     free tokenless POSTs drain the hour's units and real visitors get `budget_exhausted`.
 * =========================================================================== */
{
  const UNITS_CHAT = 3;
  const budget = () => [unitsSpent(), refundedUnits()];

  fresh();
  eq((await post({ text: "hi" })).body.reason, "turnstile_failed", "a tokenless turn is refused…");
  deep(budget(), [0, UNITS_CHAT], "…and leaves the SHARED unit budget exactly where it found it (all 3 units refunded)");

  fresh();
  eq((await turn("hello")).body.reason, null, "a served turn goes through…");
  deep(budget(), [UNITS_CHAT, 0], "…and DOES spend its 3 units, with nothing refunded");

  // An upstream failure does NOT refund: that request cost real money.
  fresh();
  P.plan = { chat: { throw: "TypeError" } };
  deep([(await turn("hello")).body.reason, gatewayCalls()], ["upstream_down", 1], "a gateway that is down is a refusal that DID call the gateway…");
  deep(budget(), [UNITS_CHAT, 0], "…so its units stay spent: the money was really committed");

  // Every OTHER charged-but-not-served refusal refunds too, or the drain is one field away.
  for (const [label, body, want] of [
    ["an over-length line", { text: "x".repeat(9000) }, "too_long"],
    ["an empty line", { text: "" }, "too_short"],
    ["a tampered context blob", { text: "hi", context: "v1.forged.blob" }, "bad_request"],
    ["a hard-blocked utterance", { text: "how do i kill myself" }, "blocked"],
  ]) {
    fresh();
    eq((await post(Object.assign(body, { [ts.TOKEN_FIELD]: TOKEN }))).body.reason, want, `${label} answers ${want}`);
    deep([gatewayCalls(), ...budget()], [0, 0, UNITS_CHAT], `…and ${label} leaves the shared budget untouched`);
  }

  fresh();
  eq((await postAudio({}, EARS)).body.reason, "turnstile_failed", "a tokenless clip is refused…");
  deep(budget(), [0, 2], "…and refunds exactly 2 units, which is what the ears cost — not the chat turn's 3");

  // /api/speech has no bot control at all, and a forged ticket is the same drain with no token.
  {
    fresh();
    const speech = await import(join(repo, "functions", "api", "speech.js"));
    const res = await speech.onRequestPost({
      request: new Request(ORIGIN + "/api/speech", {
        method: "POST",
        headers: { "Content-Type": "application/json", Origin: ORIGIN, "Sec-Fetch-Site": "same-origin", "CF-Connecting-IP": "203.0.113.9" },
        body: JSON.stringify({ ticket: "v1.forged.ticket" }),
      }),
      env: Object.assign({}, ARMED, { DEMO_TTS_MODEL: "test-voice-model", DEMO_CACHE_COUNTER: "0" }),
    });
    await assertClean(res, "speech forged ticket");
    deep([JSON.parse(await res.clone().text()).reason, gatewayCalls(), ...budget()], ["bad_ticket", 0, 0, 2],
         "a forged voice ticket is refused and gives its 2 units back too: the same drain needs no token here");
  }

  // The per-IP window is deliberately NOT given back: it is the only brake on one address.
  fresh();
  const oneIp = Object.assign({}, ARMED, { DEMO_CHAT_PER_MIN: "3", DEMO_CACHE_COUNTER: "0", DEMO_QUEUE_MAX_WAIT_MS: "0" });
  const seq = [];
  for (let i = 0; i < 5; i++) seq.push((await post({ text: "hi" }, oneIp)).body.reason);
  deep([seq, unitsSpent()], [["turnstile_failed", "turnstile_failed", "turnstile_failed", "rate_limited", "rate_limited"], 0],
       "tokenless refusals from ONE address still count against its window, while the shared budget stays untouched");

  // THE WHOLE ATTACK at production defaults: 200 tokenless requests from 200 addresses,
  // then one real visitor. (Queue off: a leaked slot must redden, not wait 200 times.)
  fresh();
  const spread = Object.assign({}, ARMED, { DEMO_CACHE_COUNTER: "0", DEMO_QUEUE_MAX_WAIT_MS: "0" });
  eq(envlib.readConfig(spread).unitBudgetHour, 600, "the hourly budget is production's 600…");
  let refused = 0;
  for (let i = 0; i < 200; i++) {
    const r = await post({ text: "hello moxie" }, spread, { "CF-Connecting-IP": "198.51.100." + (i % 250) });
    if (r.body.reason === "turnstile_failed") refused += 1;
  }
  deep([refused, gatewayCalls(), verifyCalls(), ...budget()], [200, 0, 0, 0, 600],
       "…200 tokenless requests are all refused for free AND THE BUDGET IS STILL WHOLE (600 charged, 600 given back)");
  P.plan = {};
  const visitor = await post({ text: "hello moxie", [ts.TOKEN_FIELD]: TOKEN }, spread, { "CF-Connecting-IP": "203.0.113.77" });
  deep([visitor.status, visitor.body.reason, visitor.body.mode], [200, null, "live"],
       "…and the next real visitor is SERVED live, not `budget_exhausted`");

  // THE REFUND IS IDEMPOTENT — a second call would credit away a DIFFERENT request's charge.
  // No route can reach a double refund, which is why the guard needs its own test.
  fresh();
  const cfg = envlib.readConfig(ARMED);
  const s1 = await limits.admit({ request: req({}), cfg, route: "chat" });
  s1.refundBudget();
  const s2 = await limits.admit({ request: req({}), cfg, route: "chat" });
  s1.refundBudget();                                  // the double call
  deep(budget(), [UNITS_CHAT, UNITS_CHAT],
       "refunding the FIRST slot twice does NOT credit away the second's charge (one refund counted)");
  s1.release();
  s2.release();
  eq(limits.__state().inflight.chat || 0, 0, "…and both slots came back");

  // A refused ADMISSION is slot-shaped: both methods exist and are no-ops.
  fresh();
  const denied = await limits.admit({ request: req({}, { Origin: "https://evil.invalid.test", "Sec-Fetch-Site": "cross-site" }), cfg, route: "chat" });
  denied.refundBudget();
  denied.release();
  deep([denied.ok, unitsSpent()], [false, 0], "a refused admission still has release() and refundBudget(), and neither goes negative");
}
