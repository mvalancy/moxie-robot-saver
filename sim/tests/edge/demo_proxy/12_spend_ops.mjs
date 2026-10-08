/* test_demo_proxy §24: spend protection and ops. The voice's and the ears' per-IP DAY, the
 * /56 visitor and what one prefix can spend in a UTC day, the measured chat timeout, the
 * transcribe route's 60 s default retry, the DEMO_SERVE_HOSTS allowlist, and the one log line
 * per refusal. Run via the entry file.
 *
 * Every window here is driven with an EXPLICIT clock (`nowS` into `admit()`). Where a real
 * route reads its own clock, the assertion holds at every minute and hour of the day. */
import {
  FULL, FORBIDDEN, KEY, ORIGIN, P, assertClean, call, chat, deep, eq, fresh, limits, ok,
  speech, upstreamCalls, wire2, env0,
} from "./harness.mjs";
import { api, jsonOf, leakSweep, wavBytes } from "../common.mjs";

const transcribe = await api("transcribe.js");
const health = await api("health.js");
const upstream = await api("_lib", "upstream.js");
const st = () => limits.__state();

const EARS = { ...FULL, DEMO_STT_MODEL: "test-ears-model" };
const SECRETS = [...FORBIDDEN, "test-ears-model"];
/** A UTC day boundary (day bucket 20 400), spelled out so a wrong bucket is a wrong NUMBER. */
const DAY = 20400 * 86400;

/** A same-origin POST to `origin + path` — `common.mjs::post` is pinned to ORIGIN, and an
 *  audio body must not be JSON-encoded. */
function at(origin, path, body, headers, contentType) {
  const raw = typeof body === "string" || body instanceof Uint8Array ? body : JSON.stringify(body);
  return new Request(origin + path, {
    method: "POST",
    headers: Object.assign({
      "Content-Type": contentType || "application/json",
      Origin: origin,
      "Sec-Fetch-Site": "same-origin",
      "CF-Connecting-IP": "203.0.113.9",
    }, headers || {}),
    body: raw,
  });
}
const upload = (origin, headers) => at(origin || ORIGIN, "/api/transcribe", wavBytes(1000), headers, "audio/wav");
/** A transcription the stub returns as JSON (the harness answers non-chat URLs from `plan.speech`). */
const HEARD = { status: 200, body: JSON.stringify({ text: "hello moxie" }), headers: { "Content-Type": "application/json" } };
const sweep = (res, label) => leakSweep(ok, res, SECRETS, label, { stripTopic: true });
/** One admission straight at `admit()`, no cache (bare node has no `caches`). */
const admitAt = (cfg, request, route, nowS) => limits.admit({ request, cfg, route, nowS });

/* 24a. THE KNOBS: defaults, clamps, and the arithmetic they were sized by (env.js DEFAULTS
 * has the prose; pinned here as arithmetic so changing one knob alone fails by name). */
{
  const d = wire2.readConfig(FULL);
  deep([d.speechPerDay, d.sttPerDay, d.chatTimeoutMs], [300, 225, 10000],
       "the defaults: DEMO_SPEECH_PER_DAY 300, DEMO_STT_PER_DAY 225, DEMO_CHAT_TIMEOUT_MS 10000");
  deep([wire2.DEFAULTS.DEMO_SPEECH_PER_DAY, wire2.DEFAULTS.DEMO_STT_PER_DAY, wire2.DEFAULTS.DEMO_CHAT_TIMEOUT_MS],
       [300, 225, 10000], "…as rows of the DEFAULTS table");
  for (const [name, field, dflt] of [["DEMO_SPEECH_PER_DAY", "speechPerDay", 300], ["DEMO_STT_PER_DAY", "sttPerDay", 225]]) {
    for (const [v, want, why] of [
      ["0", 0, "0 = no day window for this route (the behaviour before)"],
      ["40", 40, "a lower day is honoured"],
      ["-1", dflt, "a negative falls back to the default"],
      ["2.5", dflt, "a fraction falls back to the default"],
      ["lots", dflt, "a word falls back to the default"],
      ["10000001", dflt, "past the clamp falls back to the default, never a bigger cap"],
    ]) eq(wire2.readConfig({ ...FULL, [name]: v })[field], want, `${name}=${v}: ${why}`);
  }
  deep(Object.keys(wire2.publicLimits(d)), [...wire2.PUBLIC_LIMIT_KEYS], "the day windows are server-side: not published to the browser");

  ok(d.speechPerDay >= (d.chatPerDay * d.speechPerHour) / d.chatPerHour,
     "a visitor inside the speech HOUR's ratio to chat's (2 a reply) meets chat's day before the speech day");
  ok(d.sttPerDay >= (d.chatPerDay * d.sttPerHour) / d.chatPerHour,
     "…and one inside the ears' hour ratio (1.5 uploads a turn) meets chat's day before the ears' day");
  ok(d.speechPerDay >= d.chatPerDay * 1.6, "…above the 1.6 speech calls a reply measured with one ticket per sentence");
  const U = limits.UNITS;
  const prefixDay = U.chat * d.chatPerDay + U.speech * d.speechPerDay + U.transcribe * d.sttPerDay;
  eq(prefixDay, 1500, "ONE prefix at every per-IP day maximum spends 150x3 + 300x2 + 225x2 = 1 500 units");
  ok(prefixDay * 2 <= d.unitBudgetDay, "…under HALF of DEMO_UNIT_BUDGET_DAY: one prefix cannot drain a colo's day");

  // The chat timeout, from the measurement (moxie-brain-dense + single, 169 turns: p99 3 447 ms,
  // max 4 651 ms; the fallback model's p99 4 095 ms over 120 turns).
  const P99 = 3447, MAX = 4651, FALLBACK_P99 = 4095;
  ok(d.chatTimeoutMs >= 2 * P99 && d.chatTimeoutMs >= 8000, "DEMO_CHAT_TIMEOUT_MS is at least twice the measured p99, and at least 8 s");
  ok(d.chatTimeoutMs >= MAX + FALLBACK_P99,
     "…and holds the gateway's fallback: the first model failing as late as its slowest turn, then the fallback's p99");
  ok(d.chatTimeoutMs < 20000, "…and is shorter than the 20 s a hung gateway used to cost a visitor per turn");
  eq(chat.rerollBudgetMs(d, MAX), 10000 - MAX, "the slowest measured first reply still leaves the re-roll 5.3 s");
}

/* 24b. THE VOICE'S AND THE EARS' DAY (DEMO_SPEECH_PER_DAY / DEMO_STT_PER_DAY): paced inside
 * the minute and the hour, a whole day's worth is served; one more, in a fresh hour and
 * minute, can only be refused by the DAY. */
{
  const d = wire2.readConfig(EARS);
  for (const [route, perDay, perHour, perMin, ip] of [
    ["speech", d.speechPerDay, d.speechPerHour, d.speechPerMin, "198.51.100.61"],
    ["transcribe", d.sttPerDay, d.sttPerHour, d.sttPerMin, "198.51.100.62"],
  ]) {
    fresh();
    const r = at(ORIGIN, "/api/" + route, {}, { "CF-Connecting-IP": ip });
    let admitted = 0;
    for (let n = 0; n < perDay; n++) {
      const h = Math.floor(n / perHour), i = n % perHour;
      const s = await admitAt(d, r, route, DAY + h * 3600 + Math.floor(i / perMin) * 60 + 1);
      if (s.ok) admitted += 1;
      s.release();
    }
    eq(admitted, perDay, `/${route}: ${perDay} calls in one UTC day, paced inside its minute and hour, are all admitted`);
    const late = DAY + 5 * 3600 + 1;   // a fresh hour, a fresh minute
    const over = await admitAt(d, r, route, late);
    eq(`${over.ok} ${over.reason}`, "false rate_limited", `/${route}: call ${perDay + 1}, in a FRESH hour and minute, is refused by the DAY window`);
    eq(over.retryAfterS, DAY + 86400 - late, `/${route}: …until the end of the UTC day, to the second`);
    eq(st().stats.upstreamCalls, 0, `/${route}: …and nothing reached upstream`);
    const tomorrow = await admitAt(d, r, route, DAY + 86400 + 1);
    eq(tomorrow.ok, true, `/${route}: …and tomorrow it is served again`);
    tomorrow.release();

    fresh();
    const noDay = wire2.readConfig({ ...EARS, [route === "speech" ? "DEMO_SPEECH_PER_DAY" : "DEMO_STT_PER_DAY"]: "0" });
    for (let n = 0; n < perDay; n++) {
      const h = Math.floor(n / perHour), i = n % perHour;
      (await admitAt(noDay, r, route, DAY + h * 3600 + Math.floor(i / perMin) * 60 + 1)).release();
    }
    const free = await admitAt(noDay, r, route, late);
    eq(free.ok, true, `/${route}: with its day set to 0 the same call is admitted (no day window, as before)`);
    free.release();
  }

  // Through the real routes: a day of ONE, so the second call of the day is refused, free.
  fresh();
  const ONE_VOICE = { ...FULL, DEMO_SPEECH_PER_DAY: "1" };
  const t1 = await call(chat, "/api/chat", { text: "hello" }, null, ONE_VOICE);
  eq((await call(speech, "/api/speech", { ticket: t1.body.speech[0].ticket }, null, ONE_VOICE)).res.status, 200,
     "DEMO_SPEECH_PER_DAY=1: the day's first voice is served");
  const t2 = await call(chat, "/api/chat", { text: "and again" }, null, ONE_VOICE);
  const before = upstreamCalls();
  const s2 = await call(speech, "/api/speech", { ticket: t2.body.speech[0].ticket }, null, ONE_VOICE);
  deep([s2.res.status, s2.body.reason, upstreamCalls() - before], [429, "rate_limited", 0],
       "…the second, with a fresh valid ticket, is refused rate_limited by /api/speech with ZERO upstream calls");
  fresh();
  const ONE_EAR = { ...EARS, DEMO_STT_PER_DAY: "1" };
  P.plan = { speech: HEARD };
  const e1 = await transcribe.onRequestPost({ request: upload(), env: ONE_EAR });
  await sweep(e1, "/api/transcribe the day's first upload");
  eq(e1.status, 200, "DEMO_STT_PER_DAY=1: the day's first upload is transcribed");
  const e2 = await transcribe.onRequestPost({ request: upload(), env: ONE_EAR });
  await sweep(e2, "/api/transcribe the day's second upload");
  deep([e2.status, (await jsonOf(e2)).reason, upstreamCalls()], [429, "rate_limited", 1],
       "…the second is refused rate_limited by /api/transcribe, the gateway called once in all");
}

/* 24c. ONE /56 IS ONE VISITOR FOR THE WHOLE DAY — the hermetic grief simulation's attacker,
 * cut down to one isolate: three /64s of one /56 hammer every route at their per-minute
 * maxima from 00:00 to 23:59 UTC. Keyed by the /64 with no day for the voice or the ears,
 * this spent the colo's whole 4 000-unit day before evening. */
{
  fresh();
  const d = wire2.readConfig(EARS);
  const ips = ["2001:db8:aaaa:101::1", "2001:db8:aaaa:102::1", "2001:db8:aaaa:1ff::9"];
  deep(ips.map(limits.ipKey), ["2001:db8:aaaa:100::/56", "2001:db8:aaaa:100::/56", "2001:db8:aaaa:100::/56"],
       "three /64s inside one /56 are ONE rate-limit key");
  const plan = [["chat", d.chatPerMin], ["speech", d.speechPerMin], ["transcribe", d.sttPerMin]];
  const reqs = {};
  for (const ip of ips) for (const [route] of plan) reqs[ip + route] = at(ORIGIN, "/api/" + route, {}, { "CF-Connecting-IP": ip });
  const units = { chat: 0, speech: 0, transcribe: 0 };
  const refused = {};
  for (let m = 0; m < 1440; m++) {
    for (const ip of ips) {
      for (const [route, n] of plan) {
        for (let i = 0; i < n; i++) {
          const s = await admitAt(d, reqs[ip + route], route, DAY + m * 60 + 1);
          if (s.ok) { units[route] += limits.UNITS[route]; s.release(); }
          else refused[s.reason] = (refused[s.reason] || 0) + 1;
        }
      }
    }
  }
  deep(units, { chat: 450, speech: 600, transcribe: 450 },
       "a /56 at every route's per-minute maximum all day is held to chat 150, speech 300, transcribe 225");
  eq(units.chat + units.speech + units.transcribe, 1500, "…1 500 units: under half of the colo's 4 000");
  deep(Object.keys(refused), ["rate_limited"],
       "…and every refusal it met was its OWN per-IP window: it never once reached the colo's unit budget");
  const visitor = await admitAt(d, at(ORIGIN, "/api/chat", {}, { "CF-Connecting-IP": "192.0.2.201" }), "chat", DAY + 86399);
  eq(visitor.ok, true, "a fresh visitor at 23:59:59 UTC is still served: one prefix did not spend the colo's day");
  visitor.release();
}

/* 24d. AN UPSTREAM 429 WITH NO Retry-After: 60 s on /api/transcribe (the speech-to-text
 * group's cooldown answers 429 with no header for ~60 s), 10 s on chat and speech. */
{
  const r429 = (h) => new Response("", { status: 429, headers: h || {} });
  eq(upstream.retryAfterOf(r429()), 10, "retryAfterOf: no Retry-After and no route default is 10 s");
  eq(upstream.retryAfterOf(r429(), 60), 60, "…a route's default is used when the gateway names none");
  eq(upstream.retryAfterOf(r429({ "Retry-After": "7" }), 60), 7, "…and the gateway's own Retry-After always wins");
  eq(upstream.retryAfterOf(r429({ "Retry-After": "9999" }), 60), 300, "…clamped to 300");
  eq(upstream.retryAfterOf(r429(), 9999), 300, "…and so is a route's default");
  for (const bad of [0, -5, NaN, "60", null]) eq(upstream.retryAfterOf(r429(), bad), 10, `…an unusable default (${String(bad)}) is 10`);

  const triple = async (res) => {
    const body = await jsonOf(res);
    return `${res.status} ${body.reason} ${body.retry_after_s} ${res.headers.get("Retry-After")}`;
  };
  fresh();
  P.plan = { speech: { status: 429, body: "" } };
  const t = await transcribe.onRequestPost({ request: upload(), env: EARS });
  await sweep(t, "/api/transcribe upstream 429");
  eq(await triple(t), "429 rate_limited 60 60",
     "/api/transcribe: an upstream 429 with no Retry-After answers Retry-After 60, the STT cooldown");
  fresh();
  P.plan = { speech: { status: 429, body: "", headers: { "Retry-After": "7" } } };
  const t7 = await transcribe.onRequestPost({ request: upload(), env: EARS });
  await sweep(t7, "/api/transcribe upstream 429 with Retry-After");
  eq(await triple(t7), "429 rate_limited 7 7", "/api/transcribe: …a Retry-After the gateway sent is passed on");
  fresh();
  P.plan = { chat: { status: 429, body: "" } };
  eq(await triple((await call(chat, "/api/chat", { text: "hello" })).res), "429 rate_limited 10 10",
     "/api/chat keeps 10 s for an upstream 429 with no Retry-After");
  fresh();
  const turn = await call(chat, "/api/chat", { text: "hello" });
  P.plan = { speech: { status: 429, body: "" } };
  eq(await triple((await call(speech, "/api/speech", { ticket: turn.body.speech[0].ticket })).res), "429 rate_limited 10 10",
     "/api/speech keeps 10 s too");
}

/* 24e. DEMO_SERVE_HOSTS: where this deployment may SPEND. Unset is every host (forks and
 * previews need nothing); set, any other host is a deployment with no gateway. */
{
  const CANON = "https://canonical.invalid.test";
  const SERVE = { ...EARS, DEMO_SERVE_HOSTS: "canonical.invalid.test" };
  const hasFn = typeof wire2.servesHost === "function";
  ok(hasFn, "env.js exports servesHost (DEMO_SERVE_HOSTS)");

  eq(wire2.readConfig(FULL).serveHosts, null, "unset: null — every host is served, as before");
  deep(wire2.readConfig({ ...FULL, DEMO_SERVE_HOSTS: " Canonical.Invalid.TEST , https://b.invalid.test/sim, c.invalid.test:443, d.invalid.test., canonical.invalid.test" }).serveHosts,
       ["canonical.invalid.test", "b.invalid.test", "c.invalid.test", "d.invalid.test"],
       "bare lower-cased hostnames: a URL, a port and a trailing dot are reduced, a repeat is dropped");
  const junk = wire2.readConfig({ ...FULL, DEMO_SERVE_HOSTS: "https://, ://" });
  deep(junk.serveHosts, [], "set with nothing usable in it is an EMPTY list, not unset…");
  ok((junk.notes || []).some((n) => /DEMO_SERVE_HOSTS/.test(n)), "…and a note names the variable");

  if (hasFn) {
    const cfg = wire2.readConfig(SERVE);
    for (const [url, want, why] of [
      [CANON + "/api/chat", true, "the listed host is served"],
      ["https://CANONICAL.invalid.test/api/chat", true, "…in any letter case"],
      ["https://canonical.invalid.test./api/chat", true, "…with a trailing dot"],
      ["https://canonical.invalid.test:8443/api/chat", true, "…on any port"],
      [ORIGIN + "/api/chat", false, "an unlisted host is not"],
      ["https://x.canonical.invalid.test/api/chat", false, "nor a SUBDOMAIN of a listed host: the match is exact"],
      ["https://0a1b2c3d.project.pages.dev.test/api/chat", false, "nor a per-deployment platform URL"],
    ]) eq(wire2.servesHost(cfg, new Request(url)), want, `servesHost(${url}): ${why}`);
    eq(wire2.servesHost(junk, new Request(CANON + "/")), false, "a set-but-unusable list serves NO host: a typo does not lift the restriction");
    eq(wire2.servesHost(wire2.readConfig(EARS), new Request(ORIGIN + "/")), true, "an unset list serves every host");
  }

  // readConfig(env, request): the config itself reads unconfigured on an unlisted host.
  const off = wire2.readConfig(SERVE, new Request(ORIGIN + "/api/health"));
  deep([off.configured, off.voice, off.ears, wire2.modeOf(off, null).reason], [false, false, false, "gateway_not_configured"],
       "readConfig(env, request) on an unlisted host: not configured, no voice, no ears, gateway_not_configured");
  deep(off.missing, [], "…without inventing a missing variable: nothing is absent");
  eq(wire2.readConfig(SERVE, new Request(CANON + "/api/health")).configured, true, "the listed host reads configured");
  eq(wire2.readConfig(SERVE).configured, true,
     "without a request the config decides nothing about the host (the spending routes ask admit())");

  // /api/health says so honestly — it is the surface the page boots from.
  const probe = async (origin, env) => {
    const res = health.onRequestGet({ request: new Request(origin + "/api/health"), env });
    await sweep(res, "/api/health at " + origin);
    const b = await jsonOf(res);
    return [res.status, b.mode, b.reason, b.voice, b.ears];
  };
  deep(await probe(ORIGIN, SERVE), [200, "degraded", "gateway_not_configured", false, false],
       "/api/health on an unlisted host: 200, degraded, gateway_not_configured, no voice, no ears");
  deep(await probe(CANON, SERVE), [200, "live", null, true, true], "/api/health on the listed host: live, voice, ears");
  deep(await probe(ORIGIN, EARS), [200, "live", null, true, true], "/api/health with the variable unset: live on any host, as before");

  // Every spending route on an unlisted host: what a deployment with no gateway answers,
  // zero upstream calls, nothing charged. A ticket minted on the listed host buys nothing.
  fresh();
  P.plan = { speech: HEARD };
  const minted = await chat.onRequestPost({ request: at(CANON, "/api/chat", { text: "hello" }), env: SERVE });
  await sweep(minted, "/api/chat on the listed host");
  eq(minted.status, 200, "the listed host is served a live turn");
  const ticket = (await jsonOf(minted)).speech[0].ticket;
  fresh();
  const shape = async (res) => {
    const b = await jsonOf(res);
    return [res.status, b.reason, b.mode, b.voice, b.ears, res.headers.get("Retry-After"), res.headers.get("X-RateLimit-Limit")];
  };
  const bare = await chat.onRequestPost({ request: at(ORIGIN, "/api/chat", { text: "hello" }), env: {} });
  await sweep(bare, "/api/chat with no gateway at all");
  const NONE = await shape(bare);
  deep(NONE, [503, "gateway_not_configured", "degraded", false, false, null, null],
       "(the reference: a deployment with no gateway at all — 503, no voice, no ears, no Retry-After, no X-RateLimit)");
  fresh();
  for (const [path, run] of [
    ["/api/chat", () => chat.onRequestPost({ request: at(ORIGIN, "/api/chat", { text: "hello" }), env: SERVE })],
    ["/api/speech", () => speech.onRequestPost({ request: at(ORIGIN, "/api/speech", { ticket }), env: SERVE })],
    ["/api/transcribe", () => transcribe.onRequestPost({ request: upload(ORIGIN), env: SERVE })],
    ["/api/chat (foreign Origin)", () => chat.onRequestPost({
      request: at(ORIGIN, "/api/chat", { text: "hello" }, { Origin: "https://elsewhere.invalid.test", "Sec-Fetch-Site": "cross-site" }), env: SERVE })],
  ]) {
    const res = await run();
    await sweep(res, path + " on an unlisted host");
    deep(await shape(res), NONE, `${path} on an unlisted host answers exactly what a deployment with no gateway answers`);
  }
  eq(upstreamCalls(), 0, "…with ZERO upstream calls among them: the listed host's ticket bought nothing here");
  deep([JSON.stringify(st().budget), st().windows], ["{}", 0], "…and NOTHING charged: no unit, no per-IP window");
  eq(st().stats.refusals.gateway_not_configured, 4, "…four refusals, all by admission's first check");

  fresh();
  const control = await chat.onRequestPost({ request: at(ORIGIN, "/api/chat", { text: "hello" }), env: EARS });
  await sweep(control, "/api/chat with DEMO_SERVE_HOSTS unset");
  eq(control.status, 200, "CONTROL: with DEMO_SERVE_HOSTS unset, that same host is served (forks and previews unaffected)");
}

/* 24f. ONE LOG LINE PER REFUSAL, and never the visitor's words. Every console method is
 * captured while a CANARY rides the chat text through a served turn, a blocked turn and
 * every refusal path; exactly one `{"evt":"refusal"}` line per refusal must appear, carrying
 * route, reason, status and colo and NOTHING else, and the canary must appear nowhere. */
{
  const hasLog = typeof env0.logRefusal === "function";
  ok(hasLog, "envelope.js exports logRefusal");
  const CANARY = "zebra-canary-7f3a9c";
  const METHODS = ["log", "info", "warn", "error", "debug"];
  const real = {};
  const out = [];
  const lines = () => out.filter((l) => l.includes('"evt":"refusal"'));
  const colo = (req, code) => { Object.defineProperty(req, "cf", { value: { colo: code } }); return req; };
  // Wide windows, so a dozen turns from one address are refused only where a case means it.
  const E = { ...EARS, DEMO_CHAT_PER_MIN: "1000", DEMO_CHAT_PER_HOUR: "100000", DEMO_CHAT_PER_DAY: "1000000" };
  const ONE_A_DAY = { ...EARS, DEMO_CHAT_PER_MIN: "1", DEMO_CHAT_PER_HOUR: "1", DEMO_CHAT_PER_DAY: "1" };
  let ticket = "";
  let context = "";
  const cases = [
    // [label, run, the refusal line expected (null = none)]
    ["a served turn", async () => {
      P.plan = { chat: { content: "Hi! I like zebras." } };
      const res = await chat.onRequestPost({ request: at(ORIGIN, "/api/chat", { text: "hi " + CANARY }), env: E });
      const b = await jsonOf(res);
      ticket = b.speech[0].ticket;
      context = b.context;
      return res;
    }, null],
    ["a served voice", () => speech.onRequestPost({ request: at(ORIGIN, "/api/speech", { ticket }), env: E }), null],
    ["a served transcription", () => { P.plan = { speech: HEARD }; return transcribe.onRequestPost({ request: upload(), env: E }); }, null],
    ["a blocked turn", () => chat.onRequestPost({ request: at(ORIGIN, "/api/chat", { text: "i want to kill myself " + CANARY }), env: E }), null],
    ["/api/health", () => health.onRequestGet({ request: new Request(ORIGIN + "/api/health"), env: E }), null],
    ["too_long", () => chat.onRequestPost({ request: at(ORIGIN, "/api/chat", { text: CANARY.repeat(40) }), env: E }), ["chat", "too_long", 400]],
    ["too_short", () => chat.onRequestPost({ request: at(ORIGIN, "/api/chat", { text: "   " }), env: E }), ["chat", "too_short", 400]],
    ["a forged context", () => chat.onRequestPost({ request: at(ORIGIN, "/api/chat", { text: CANARY, context: context.slice(0, -4) + "AAAA" }), env: E }), ["chat", "bad_request", 400]],
    ["forbidden_origin", () => chat.onRequestPost({
      request: at(ORIGIN, "/api/chat", { text: CANARY }, { Origin: "https://evil.invalid.test", "Sec-Fetch-Site": "cross-site" }), env: E }), ["chat", "forbidden_origin", 403]],
    ["the day's first turn", () => chat.onRequestPost({ request: at(ORIGIN, "/api/chat", { text: CANARY }, { "CF-Connecting-IP": "198.51.100.77" }), env: ONE_A_DAY }), null],
    ["rate_limited", () => chat.onRequestPost({ request: at(ORIGIN, "/api/chat", { text: CANARY }, { "CF-Connecting-IP": "198.51.100.77" }), env: ONE_A_DAY }), ["chat", "rate_limited", 429]],
    ["upstream_down", () => { P.plan = { chat: { status: 500, body: '{"error":{"message":"' + CANARY + '"}}' } };
      return chat.onRequestPost({ request: at(ORIGIN, "/api/chat", { text: CANARY }), env: E }); }, ["chat", "upstream_down", 503]],
    ["timeout", () => { P.plan = { chat: { throw: "TimeoutError" } };
      return chat.onRequestPost({ request: at(ORIGIN, "/api/chat", { text: CANARY }), env: E }); }, ["chat", "timeout", 504]],
    ["gateway_not_configured", () => chat.onRequestPost({ request: at(ORIGIN, "/api/chat", { text: CANARY }), env: {} }), ["chat", "gateway_not_configured", 503]],
    ["a forged ticket", () => speech.onRequestPost({ request: at(ORIGIN, "/api/speech", { ticket: CANARY + ".x.y" }), env: E }), ["speech", "bad_ticket", 400]],
    ["a replayed ticket", () => speech.onRequestPost({ request: at(ORIGIN, "/api/speech", { ticket }), env: E }), ["speech", "bad_ticket", 400]],
    ["a tiny upload", () => transcribe.onRequestPost({ request: at(ORIGIN, "/api/transcribe", new Uint8Array(64), {}, "audio/wav"), env: E }), ["transcribe", "too_short", 400]],
    ["the ears' upstream 429", () => { P.plan = { speech: { status: 429, body: "" } };
      return transcribe.onRequestPost({ request: upload(), env: E }); }, ["transcribe", "rate_limited", 429]],
  ];
  fresh();
  for (const m of METHODS) {
    real[m] = console[m];
    console[m] = (...a) => { out.push(a.map((x) => (typeof x === "string" ? x : JSON.stringify(x))).join(" ")); };
  }
  const seen = [];
  try {
    for (const [label, run, want] of cases) {
      const before = lines().length;
      const res = await run();
      await sweep(res, "§24f " + label);
      const fresh_ = lines().slice(before).map((l) => JSON.parse(l));
      if (!want) {
        eq(fresh_.length, 0, `${label}: is not a refusal, so it writes NO refusal line`);
        continue;
      }
      eq(fresh_.length, 1, `${label}: writes EXACTLY ONE refusal line`);
      const line = fresh_[0] || {};
      const body = await jsonOf(res);
      deep(Object.keys(line), ["evt", "route", "reason", "status", "colo"], `${label}: the line has exactly five keys`);
      deep([line.evt, line.route, line.reason, line.status], ["refusal", ...want], `${label}: route, reason and status, as answered`);
      deep([line.reason, line.status], [body && body.reason, res.status], `${label}: …and they are the envelope's own`);
      seen.push(line);
    }
    // The colo: what `request.cf.colo` says, when it looks like one.
    const c1 = await chat.onRequestPost({ request: colo(at(ORIGIN, "/api/chat", { text: "" }), "SJC"), env: E });
    const c2 = await chat.onRequestPost({ request: colo(at(ORIGIN, "/api/chat", { text: "" }), "<b>" + CANARY), env: E });
    await sweep(c1, "§24f colo"); await sweep(c2, "§24f bad colo");
    const [l1, l2] = lines().slice(-2).map((l) => JSON.parse(l));
    eq(l1 && l1.colo, "SJC", "the colo admission saw (request.cf.colo) is on the line");
    eq(l2 && l2.colo, "", "…and anything that is not a colo code is blanked, never echoed");
    eq((seen.find((l) => l.reason === "gateway_not_configured") || {}).colo, "",
       "a refusal before admission has no colo to report (it never saw the request)");
    // A console that throws costs nobody their answer.
    console.log = () => { throw new Error("log sink down"); };
    let survived = null;
    try {
      survived = await chat.onRequestPost({ request: at(ORIGIN, "/api/chat", { text: "" }), env: E });
    } catch {
      // The defect this guards: the route threw, and the visitor got no answer at all.
    }
    ok(survived !== null, "a console.log that THROWS does not cost the visitor their answer");
    if (survived) {
      await sweep(survived, "§24f a throwing console");
      eq(`${survived.status} ${(await jsonOf(survived)).reason}`, "400 too_short", "…they get the ordinary refusal");
    }
  } finally {
    for (const m of METHODS) console[m] = real[m];
  }
  eq(seen.length, cases.filter((c) => c[2]).length, "one line for every refusal in the battery, and no more");
  const all = out.join("\n");
  ok(!all.includes(CANARY), "THE CANARY sent as chat text (served, blocked and refused) appears NOWHERE in console output");
  for (const [secret, what] of [[KEY, "the gateway key"], ["gw.invalid.test", "the gateway host"], ["203.0.113.9", "the visitor's address"],
                                ["198.51.100.77", "another visitor's address"], ["evil.invalid.test", "a request header's value"],
                                [ticket || "<no ticket>", "a ticket"], [context || "<no context>", "a context blob"]]) {
    ok(!all.includes(secret), `…nor ${what}`);
  }
  if (hasLog) {
    const direct = [];
    const keep = console.log;
    console.log = (s) => direct.push(JSON.parse(s));
    try {
      env0.logRefusal("admin", "not-a-reason", 4.5, "sjc");
      env0.logRefusal("speech", "at_capacity", 503, "LHR");
    } finally {
      console.log = keep;
    }
    deep(direct, [
      { evt: "refusal", route: "other", reason: "bad_request", status: 0, colo: "" },
      { evt: "refusal", route: "speech", reason: "at_capacity", status: 503, colo: "LHR" },
    ], "logRefusal coerces every field into its closed set: no caller-chosen string reaches a log");
  }
}
