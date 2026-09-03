/* test_mode.mjs — the mode machine and the honest indicator, end to end, under bare node.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §3.2 (the envelope), §4.2 (what the
 * browser may know), §4.5 (the status table), §5 (the configuration table), §6.3 (the
 * state machine), §7 (capacity signalling and the copy).
 *
 * Four things are under test here, in one file, because they are one contract:
 *
 *   1. functions/api/_lib/env.js   — every DEMO_* default, clamp and required value.
 *   2. functions/api/_lib/envelope.js — the one response shape and its status mapping.
 *   3. functions/api/health.js     — the probe. Pages Functions are ES modules that take
 *      a plain object as `context.env`, so they are imported and CALLED here: NO
 *      Cloudflare account is needed, and none may ever be required by a test.
 *   4. sim/web/mode.js and sim/web/env.js — loaded as SOURCE under a stubbed
 *      window/document/fetch/setTimeout, the trick sim/test_bridge.mjs:31-51 established.
 *
 * Everything is asserted on RECORDED state — `moxieMode.stats()`, the transition log, the
 * scheduled delays, the text a fake DOM ended up holding — never on a live sample of a
 * timer or a network call (playbook rule 11: a poll that already happened is a fact, one
 * that is about to happen is a bet). Time and timers are injected, so the whole 5-minute
 * backoff ladder is exercised in milliseconds.
 *
 *   node sim/test_mode.mjs
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..");
const MODE_SRC = readFileSync(join(here, "web", "mode.js"), "utf8");
const ENV_SRC = readFileSync(join(here, "web", "env.js"), "utf8");

const fails = [];
const ok = (c, m) => { if (!c) fails.push(m); };
const eq = (a, b, m) => ok(a === b, `${m} — got ${JSON.stringify(a)}, want ${JSON.stringify(b)}`);
const deep = (a, b, m) => eq(JSON.stringify(a), JSON.stringify(b), m);

const lib = await import(join(repo, "functions", "api", "_lib", "env.js"));
const env2 = await import(join(repo, "functions", "api", "_lib", "envelope.js"));
const health = await import(join(repo, "functions", "api", "health.js"));

/** A fully-configured deployment. These strings exist only inside this test, use
 *  `.invalid.test` (RFC 6761 reserved, unresolvable) and are deliberately shaped so the
 *  repo's own pre-commit secret grep cannot mistake them for a real key. */
const FULL = {
  DEMO_GATEWAY_BASE_URL: "https://gw.invalid.test/v1",
  DEMO_GATEWAY_API_KEY: "sk-testonly-abcdefghijklmnop",
  DEMO_CHAT_MODEL: "test-brain-model",
  DEMO_TTS_MODEL: "test-voice-model",
  DEMO_STT_MODEL: "test-ears-model",
};

// --------------------------------------------------------------------------- //
// 1. functions/api/_lib/env.js — §5's table, the clamps, and the fail-safe default
// --------------------------------------------------------------------------- //
{
  // C5: with NO variables at all the answer is "not configured". This is the single most
  // important assertion in the file: it is what makes a keyless branch preview safe.
  const bare = lib.readConfig({});
  eq(bare.configured, false, "no variables at all must not be `configured`");
  eq(lib.modeOf(bare, null).mode, "degraded", "no variables => degraded");
  eq(lib.modeOf(bare, null).reason, "gateway_not_configured", "no variables => gateway_not_configured");
  eq(bare.voice, false, "no variables => no voice");
  eq(bare.ears, false, "no variables => no ears");
  deep(bare.missing, ["DEMO_GATEWAY_BASE_URL", "DEMO_GATEWAY_API_KEY", "DEMO_CHAT_MODEL"],
       "the three required values must be named");

  // C3: no default may exist for the gateway. `mqtt/config.py` carries a Python default
  // for the local stack; copying it here would make an unconfigured fork call OUR gateway.
  for (const name of lib.REQUIRED_FOR_LIVE)
    ok(!(name in lib.DEFAULTS), `${name} must have NO default (unset means degraded, never "guess ours")`);

  const full = lib.readConfig(FULL);
  eq(full.configured, true, "the three required values make it configured");
  eq(lib.modeOf(full, null).mode, "live", "configured => live");
  eq(lib.modeOf(full, null).reason, null, "live carries no reason");
  eq(full.voice, true, "a TTS model makes voice true");
  eq(full.ears, true, "an STT model makes ears true");
  eq(lib.readConfig({ ...FULL, DEMO_TTS_MODEL: "" }).voice, false, "no TTS model => no voice");
  eq(lib.readConfig({ ...FULL, DEMO_STT_MODEL: "" }).ears, false, "no STT model => no ears");

  // A TTS model with no gateway to call is not a voice.
  const voiceOnly = lib.readConfig({ DEMO_TTS_MODEL: "test-voice-model" });
  eq(voiceOnly.voice, false, "a TTS model without a gateway must not claim a voice");

  // The kill switch: degraded WITHOUT deleting the secret (§4.1, the fastest incident
  // response there is).
  for (const off of ["0", "false", "no", "off", "OFF"])
    eq(lib.modeOf(lib.readConfig({ ...FULL, DEMO_ENABLED: off }), null).reason,
       "gateway_not_configured", `DEMO_ENABLED=${off} must force the degraded answer`);
  eq(lib.modeOf(lib.readConfig({ ...FULL, DEMO_ENABLED: "1" }), null).mode, "live",
     "DEMO_ENABLED=1 leaves it live");

  // Each required value alone is not enough.
  for (const drop of lib.REQUIRED_FOR_LIVE) {
    const e = { ...FULL }; delete e[drop];
    eq(lib.modeOf(lib.readConfig(e), null).reason, "gateway_not_configured",
       `missing ${drop} => gateway_not_configured`);
  }

  // §5's defaults, exactly.
  const d = lib.readConfig({});
  eq(d.maxTokens, 160, "DEMO_MAX_TOKENS default");
  eq(d.maxInputChars, 500, "DEMO_MAX_INPUT_CHARS default");
  eq(d.maxTtsChars, 300, "DEMO_MAX_TTS_CHARS default");
  eq(d.maxContextChars, 1500, "DEMO_MAX_CONTEXT_CHARS default");
  eq(d.maxHistoryTurns, 4, "DEMO_MAX_HISTORY_TURNS default");
  eq(d.maxAudioBytes, 500000, "DEMO_MAX_AUDIO_BYTES default");
  eq(d.minAudioBytes, 2000, "DEMO_MIN_AUDIO_BYTES default");
  eq(d.chatPerMin, 5, "DEMO_CHAT_PER_MIN default");
  eq(d.chatPerHour, 40, "DEMO_CHAT_PER_HOUR default");
  eq(d.chatPerDay, 150, "DEMO_CHAT_PER_DAY default");
  eq(d.speechPerMin, 10, "DEMO_SPEECH_PER_MIN default");
  eq(d.speechPerHour, 80, "DEMO_SPEECH_PER_HOUR default");
  eq(d.sttPerMin, 10, "DEMO_STT_PER_MIN default");
  eq(d.sttPerHour, 60, "DEMO_STT_PER_HOUR default");
  eq(d.maxConcurrentChat, 4, "DEMO_MAX_CONCURRENT_CHAT default");
  eq(d.maxConcurrentSpeech, 8, "DEMO_MAX_CONCURRENT_SPEECH default");
  eq(d.unitBudgetHour, 600, "DEMO_UNIT_BUDGET_HOUR default");
  eq(d.unitBudgetDay, 4000, "DEMO_UNIT_BUDGET_DAY default");
  eq(d.chatTimeoutMs, 20000, "DEMO_CHAT_TIMEOUT_MS default");
  eq(d.speechTimeoutMs, 12000, "DEMO_SPEECH_TIMEOUT_MS default");
  eq(d.sttTimeoutMs, 12000, "DEMO_STT_TIMEOUT_MS default");
  eq(d.ticketTtlS, 60, "DEMO_TICKET_TTL_S default");
  eq(d.ttsFormat, "wav", "DEMO_TTS_FORMAT default");
  eq(d.ttsSampleRate, 22050, "DEMO_TTS_SAMPLE_RATE default");
  eq(d.deviceId, "d_sim", "DEMO_DEVICE_ID default (matches bridge.js:453)");
  ok(d.persona.length > 40, "a built-in persona must ship, so a fork is not a bare model");

  // The allowlist idiom (cloud_config.py:435-475): coerce, clamp, and NEVER let a bad
  // value become a bigger cap than the default.
  eq(lib.readConfig({ DEMO_MAX_INPUT_CHARS: "banana" }).maxInputChars, 500, "garbage falls back");
  eq(lib.readConfig({ DEMO_MAX_INPUT_CHARS: "1e9" }).maxInputChars, 500, "out of range falls back");
  eq(lib.readConfig({ DEMO_MAX_INPUT_CHARS: "-5" }).maxInputChars, 500, "negative falls back");
  eq(lib.readConfig({ DEMO_MAX_INPUT_CHARS: "12.5" }).maxInputChars, 500, "non-integer falls back");
  eq(lib.readConfig({ DEMO_MAX_INPUT_CHARS: " 250 " }).maxInputChars, 250, "a good value is taken");
  eq(lib.readConfig({ DEMO_MAX_TOKENS: "999999" }).maxTokens, 160, "an absurd token cap falls back");
  // Only wav/pcm are decodable by audio.js (§5, mirroring mqtt/config.py:101).
  eq(lib.readConfig({ DEMO_TTS_FORMAT: "mp3" }).ttsFormat, "wav", "an undecodable format falls back to wav");
  eq(lib.readConfig({ DEMO_TTS_FORMAT: "PCM" }).ttsFormat, "pcm", "pcm is accepted, case-insensitively");
  // audio.js:617-618 clamps the rate; a configured rate the decoder would refuse is not
  // allowed to reach it.
  eq(lib.readConfig({ DEMO_TTS_SAMPLE_RATE: "1000" }).ttsSampleRate, 22050, "a sub-3 kHz rate falls back");
  eq(lib.readConfig({ DEMO_TTS_SAMPLE_RATE: "16000" }).ttsSampleRate, 16000, "a good rate is taken");

  // §5: empty DEMO_ALLOWED_ORIGINS means "the request's own origin only" (C3).
  deep(lib.readConfig({}).allowedOrigins, [], "no extra origins by default");
  deep(lib.readConfig({ DEMO_ALLOWED_ORIGINS: " https://a.test/x , https://b.test " }).allowedOrigins,
       ["https://a.test", "https://b.test"], "extra origins are normalized to origins");

  // Budget is a COUNTER state, so it is passed in rather than guessed.
  eq(lib.modeOf(full, { exhausted: true }).reason, "budget_exhausted", "an exhausted budget degrades");

  // C1, structurally: JSON.stringify(cfg) is the shape of every accidental leak, and the
  // three values worth stealing are non-enumerable.
  const text = JSON.stringify(full);
  ok(!text.includes(FULL.DEMO_GATEWAY_API_KEY), "the gateway key must not survive JSON.stringify(config)");
  ok(!text.includes(FULL.DEMO_GATEWAY_BASE_URL), "the gateway base URL must not survive JSON.stringify(config)");
  eq(full.apiKey, FULL.DEMO_GATEWAY_API_KEY, "...while still being readable as a property");
  eq(full.baseUrl, FULL.DEMO_GATEWAY_BASE_URL, "...same for the base URL");

  // §4.2: the caps the browser may know, and nothing else.
  deep(Object.keys(lib.publicLimits(full)), [...lib.PUBLIC_LIMIT_KEYS],
       "publicLimits must expose exactly PUBLIC_LIMIT_KEYS");
  const limitText = JSON.stringify(lib.publicLimits(full));
  ok(!/model/i.test(limitText), "no model id may appear in `limits`");
  ok(!/http/i.test(limitText), "no URL may appear in `limits`");
}

// --------------------------------------------------------------------------- //
// 2. functions/api/_lib/envelope.js — one shape, a closed reason set, §4.5's statuses
// --------------------------------------------------------------------------- //
{
  const e = env2.envelope({});
  deep(Object.keys(e), [...env2.PUBLIC_KEYS], "the envelope is exactly PUBLIC_KEYS, in order");

  // The allowlist IS the control: an unknown key cannot ride along, because nothing
  // copies unknown keys.
  const poisoned = env2.envelope({
    base_url: "https://gw.invalid.test/v1",
    api_key: "sk-testonly-abcdefghijklmnop",
    model: "test-brain-model",
    upstream_status: 500,
  });
  deep(Object.keys(poisoned), [...env2.PUBLIC_KEYS], "unknown keys are dropped, not rejected");
  const ptext = JSON.stringify(poisoned);
  ok(!ptext.includes("gw.invalid.test"), "a gateway URL handed in cannot appear in a response");
  ok(!ptext.includes("sk-testonly"), "a key handed in cannot appear in a response");
  ok(!ptext.includes("test-brain-model"), "a model id handed in cannot appear in a response");

  // `message` is the one free-text field, so it is scrubbed as well as allowlisted.
  eq(env2.sanitizeMessage("see https://gw.invalid.test/v1/chat for details"),
     "see [url removed] for details", "a URL in `message` is removed");
  eq(env2.sanitizeMessage("bad key sk-testonly-abcdefghijklmnop rejected"),
     "bad key [key removed] rejected", "a key-shaped token in `message` is removed");
  ok(env2.sanitizeMessage("x".repeat(500)).length <= 200, "`message` is length-capped");

  // The reason set is closed.
  eq(env2.envelope({ reason: "teapot" }).reason, "bad_request", "an unknown reason collapses to bad_request");
  eq(env2.envelope({}).reason, null, "no reason means null, not a string");
  for (const r of env2.REASONS) eq(env2.envelope({ reason: r }).reason, r, `${r} survives`);

  // §4.5's status table.
  const st = (reason) => env2.statusFor(env2.envelope({ reason }), null);
  eq(st(null), 200, "a clean reply is 200");
  eq(st("rate_limited"), 429, "rate_limited is 429");
  eq(st("at_capacity"), 503, "at_capacity is 503");
  eq(st("budget_exhausted"), 503, "budget_exhausted is 503");
  eq(st("upstream_down"), 503, "upstream_down is 503");
  eq(st("gateway_not_configured"), 503, "gateway_not_configured is 503 on a spending route");
  eq(st("timeout"), 504, "timeout is 504");
  eq(st("bad_request"), 400, "bad_request is 400");
  eq(st("too_long"), 400, "too_long is 400");
  eq(st("too_short"), 400, "too_short is 400");
  eq(st("bad_ticket"), 400, "bad_ticket is 400");
  eq(st("forbidden_origin"), 403, "forbidden_origin is 403");
  // §4.1: a blocked turn is not an error — it answers ok/degraded and spends nothing.
  eq(st("blocked"), 200, "a blocked turn is 200");
  eq(env2.envelope({ reason: "blocked" }).ok, true, "a blocked turn is ok:true");
  eq(env2.envelope({ reason: "blocked" }).degraded, true, "...and degraded:true");

  // Retry-After.
  eq(env2.retryAfterFor(env2.envelope({ reason: "at_capacity" })), 15, "at_capacity => Retry-After 15");
  eq(env2.retryAfterFor(env2.envelope({ reason: "upstream_down" })), 60, "upstream_down => Retry-After 60");
  eq(env2.retryAfterFor(env2.envelope({ reason: "timeout" })), 10, "timeout => Retry-After 10");
  eq(env2.retryAfterFor(env2.envelope({ reason: "gateway_not_configured" })), null,
     "gateway_not_configured sends no Retry-After (it is not going to change on a timer)");
  eq(env2.retryAfterFor(env2.envelope({ reason: "rate_limited", retry_after_s: 7 })), 7,
     "a window-derived Retry-After is carried through");
  eq(env2.retryAfterFor(env2.envelope({})), null, "a clean reply sends no Retry-After");

  // §7's capacity levels.
  eq(env2.loadLevel(0, 4), "ok", "0/4 is ok");
  eq(env2.loadLevel(2, 4), "ok", "2/4 (50%) is still ok");
  eq(env2.loadLevel(3, 4), "busy", "3/4 (>=60%) is busy");
  eq(env2.loadLevel(4, 4), "full", "4/4 is full");
  eq(env2.loadLevel(9, 4), "full", "over the ceiling is full");
  eq(env2.loadLevel(1, 0), "ok", "an unknown capacity is not a panic");

  // Headers on every reply, not just the rejections (§4.5).
  const r = env2.respond({ reason: "rate_limited", retry_after_s: 7, mode: "live" },
                         { rateLimit: { limit: 5, remaining: 0, reset: 42 } });
  eq(r.status, 429, "respond() maps the status");
  eq(r.headers.get("retry-after"), "7", "respond() sets Retry-After");
  eq(r.headers.get("cache-control"), "no-store", "every reply is no-store");
  eq(r.headers.get("x-moxie-mode"), "live", "X-Moxie-Mode rides every reply");
  eq(r.headers.get("x-ratelimit-limit"), "5", "X-RateLimit-Limit rides the reply");
  eq(r.headers.get("x-ratelimit-remaining"), "0", "X-RateLimit-Remaining rides the reply");
  eq(r.headers.get("access-control-allow-origin"), null,
     "no Access-Control-Allow-Origin, ever (§4.3 — the wildcard in sim/tts/server.py is the anti-pattern)");
}

// --------------------------------------------------------------------------- //
// 3. functions/api/health.js — the probe
// --------------------------------------------------------------------------- //
async function probe(env) {
  const res = await health.onRequestGet({ env });
  const text = await res.text();
  return { res, text, body: JSON.parse(text) };
}
{
  const bare = await probe({});
  // Always 200, so that a NON-200 unambiguously means "the route is absent".
  eq(bare.res.status, 200, "/api/health is always 200, even when nothing is configured");
  eq(bare.body.mode, "degraded", "no variables => degraded");
  eq(bare.body.reason, "gateway_not_configured", "no variables => gateway_not_configured");
  eq(bare.body.ok, true, "`ok` means the probe answered");
  eq(bare.body.degraded, true, "...and `degraded` says the demo is not live");
  eq(bare.body.voice, false, "no voice when nothing is configured");
  eq(bare.body.ears, false, "no ears when nothing is configured");
  deep(bare.body.messages, [], "a probe carries no messages");
  deep(bare.body.speech, [], "a probe mints no ticket");
  eq(bare.body.context, "", "a probe carries no context blob");
  eq(bare.res.headers.get("cache-control"), "no-store", "the probe is never cached");
  ok(/application\/json/.test(bare.res.headers.get("content-type") || ""), "the probe answers JSON");
  eq(bare.res.headers.get("retry-after"), null, "gateway_not_configured sets no Retry-After");
  deep(Object.keys(bare.body), [...env2.PUBLIC_KEYS], "the probe body is exactly PUBLIC_KEYS");
  deep(bare.body.load, { level: "ok", inflight: 0, capacity: 4 },
       "§7: inflight is 0 because in P0-a it IS 0 — no spending route is deployed");
  deep(bare.body.limits,
       { max_input_chars: 500, max_tts_chars: 300, max_tokens: 160, chat_per_min: 5,
         max_record_ms: 15000, max_audio_bytes: 500000, min_audio_bytes: 2000 },
       "the probe reports §4.1's caps, including the three the microphone needs (P1)");

  const live = await probe(FULL);
  eq(live.res.status, 200, "a configured probe is 200");
  eq(live.body.mode, "live", "the three required values => live");
  eq(live.body.reason, null, "live carries no reason");
  eq(live.body.voice, true, "a configured TTS model => voice");
  eq(live.body.ears, true, "a configured STT model => ears");
  eq(live.res.headers.get("x-moxie-mode"), "live", "X-Moxie-Mode reflects the mode");

  // §4.2, the whole point: NOTHING secret leaves. Asserted over the raw response text of
  // a FULLY configured deployment, which is the only case where there is anything to leak.
  for (const secret of Object.values(FULL))
    ok(!live.text.includes(secret), `the probe body must not contain ${secret.slice(0, 12)}…`);
  ok(!/gw\.invalid\.test/.test(live.text), "no gateway hostname in the probe body");
  ok(!/sk-/.test(live.text), "no key prefix in the probe body");
  let headerCount = 0;
  for (const [name, value] of live.res.headers) {
    headerCount++;
    ok(!Object.values(FULL).some((s) => String(value).includes(s)), `no secret in header ${name}`);
    ok(!/gw\.invalid\.test|sk-/.test(String(value)), `no URL or key prefix in header ${name}`);
  }
  ok(headerCount >= 4, `the header scan must actually run (saw ${headerCount} headers)`);

  const off = await probe({ ...FULL, DEMO_ENABLED: "0" });
  eq(off.res.status, 200, "the kill switch still answers 200");
  eq(off.body.reason, "gateway_not_configured", "DEMO_ENABLED=0 reads as not configured");
  eq(off.body.mode, "degraded", "DEMO_ENABLED=0 => degraded");

  // The probe must not be a spending route by accident: health.js may not call fetch.
  const src = readFileSync(join(repo, "functions", "api", "health.js"), "utf8");
  ok(!/\bfetch\s*\(/.test(src), "health.js must make NO gateway call, ever (a 30 s poll must cost nothing)");
}

// --------------------------------------------------------------------------- //
// 4. sim/web/mode.js — the state machine, on injected time and injected timers
// --------------------------------------------------------------------------- //
const HEALTH_BARE = (await probe({})).text;
const HEALTH_LIVE = (await probe(FULL)).text;
const envelopeText = (over) => JSON.stringify(env2.envelope(over));

const flush = async (n = 8) => { for (let i = 0; i < n; i++) await new Promise((r) => setImmediate(r)); };

/**
 * Load mode.js under a stubbed browser. `replies` is consumed one per fetch; when it runs
 * dry the last one repeats, so a backoff ladder needs only one entry.
 */
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
  // P0-a ships no cloud-transport.js, so nothing can USE a live mode yet.
  eq(h.m.hasTransport(), false, "P0-a ships no live transport");
  eq(h.m.badge(), "HOSTED DEMO · SCRIPTED",
     "a live mode with no transport must NOT paint LIVE — that is the dishonesty being removed");
  ok(/no live transport/.test(h.m.message()), `...and it says why (got "${h.m.message()}")`);
  eq(h.m.canSpendLiveTurn(), false, "no transport => nothing is spendable");
  deep(h.m.stats().scheduled, [30000], "§6.3: the poll floor is 30 s");
}
{
  const h = boot({ transport: true, replies: [{ status: 200, body: HEALTH_LIVE }] });
  await flush();
  eq(h.m.badge(), "HOSTED DEMO · LIVE", "with a transport loaded, live paints LIVE");
  eq(h.m.message(), "", "§7: the ok/live row has no copy");
  eq(h.m.canSpendLiveTurn(), true, "live + transport => turns are spendable");
}

// 4d. §7's capacity signal, from the numbers the server sent.
for (const [inflight, capacity, badge, snippet] of [
  [0, 4, "HOSTED DEMO · LIVE", ""],
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
  ["budget_exhausted", "HOSTED DEMO · SCRIPTED", "today’s demo budget"],
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

// 4f. What the transport reports back (§4.5), and the live -> degraded transitions (§6.3).
{
  const h = boot({ transport: true, replies: [{ status: 200, body: HEALTH_LIVE }] });
  await flush();
  h.m.note({ status: 503, reason: "budget_exhausted", retry_after_s: 90 });
  eq(h.m.state(), "degraded", "budget_exhausted degrades at once");
  eq(h.m.badge(), "HOSTED DEMO · SCRIPTED", "...with §7's scripted badge");
  eq(h.timers.length, 1, "...and keeps polling, so recovery is automatic");
  eq(h.m.stats().lastDelayMs, 90000, "...on the server's own Retry-After");
}
{
  const h = boot({ transport: true, replies: [{ status: 200, body: HEALTH_LIVE }] });
  await flush();
  h.m.note({ status: 503, reason: "upstream_down", retry_after_s: 60 });
  eq(h.m.state(), "degraded", "upstream_down degrades at once");
  eq(h.m.stats().lastDelayMs, 60000, "...and re-polls on Retry-After");
}
// 429 is a SOFT degrade: the mode stays live (a rate-limited visitor is not a broken
// deployment) but nothing is spent until Retry-After has passed.
{
  const h = boot({ transport: true, replies: [{ status: 200, body: HEALTH_LIVE }] });
  await flush();
  h.m.note({ status: 429, reason: "rate_limited", retry_after_s: 7 });
  eq(h.m.state(), "live", "§6.3: a 429 does NOT leave live");
  eq(h.m.canSpendLiveTurn(), false, "...but suppresses live turns");
  eq(h.m.retryAfterS(), 7, "...for the Retry-After the server sent");
  eq(h.m.badge(), "HOSTED DEMO · LIVE", "§7: the badge stays LIVE");
  ok(/One at a time/.test(h.m.message()), `...with the transient chip (got "${h.m.message()}")`);
  h.advance(7001);
  eq(h.m.canSpendLiveTurn(), true, "live turns resume once the window has passed");
  eq(h.m.retryAfterS(), 0, "...and the countdown is spent");
}
// at_capacity is a LOAD signal, not a broken deployment (§7 gives it the BUSY badge in
// the live row, which is how §6.3's blanket "503" and §4.5's at_capacity row reconcile).
{
  const h = boot({ transport: true, replies: [{ status: 200, body: HEALTH_LIVE }] });
  await flush();
  h.m.note({ status: 503, reason: "at_capacity", retry_after_s: 15 });
  eq(h.m.state(), "live", "at_capacity keeps the deployment live");
  eq(h.m.badge(), "HOSTED DEMO · BUSY", "...and shows BUSY");
  ok(/hands full/.test(h.m.message()), `...with §7's copy (got "${h.m.message()}")`);
  eq(h.m.canSpendLiveTurn(), false, "...and spends nothing until a slot opens");
  eq(h.m.stats().lastDelayMs, 15000, "...re-polling after Retry-After");
}
// 403 is treated as offline (§4.5's last row).
{
  const h = boot({ transport: true, replies: [{ status: 200, body: HEALTH_LIVE }] });
  await flush();
  h.m.note({ status: 403, reason: "forbidden_origin" });
  eq(h.m.state(), "offline", "forbidden_origin is treated as offline");
  eq(h.timers.length, 0, "...and stops polling");
}
// The 400 family never changes the mode — it is an input outcome, not a deployment one.
for (const reason of ["bad_request", "too_long", "too_short", "bad_ticket", "blocked"]) {
  const h = boot({ transport: true, replies: [{ status: 200, body: HEALTH_LIVE }] });
  await flush();
  h.m.note({ status: 400, reason });
  eq(h.m.state(), "live", `${reason} must not change the mode`);
  eq(h.m.canSpendLiveTurn(), true, `${reason} must not stop the next turn`);
}
// Three consecutive transport errors, and not two (§6.3).
{
  const h = boot({ transport: true, replies: [{ status: 200, body: HEALTH_LIVE }] });
  await flush();
  h.m.noteTransportError();
  eq(h.m.state(), "live", "one transport error is not a broken deployment");
  h.m.noteTransportError();
  eq(h.m.state(), "live", "two transport errors are still not");
  h.m.noteTransportError();
  eq(h.m.state(), "degraded", "three consecutive transport errors degrade");
  eq(h.m.reason(), "upstream_down", "...as upstream_down");
  eq(h.m.stats().transportErrors, 3, "the strikes are recorded, not inferred");
}
// A 504 timeout counts toward the same three strikes (§4.5).
{
  const h = boot({ transport: true, replies: [{ status: 200, body: HEALTH_LIVE }] });
  await flush();
  h.m.note({ status: 504, reason: "timeout" });
  h.m.note({ status: 504, reason: "timeout" });
  eq(h.m.state(), "live", "two timeouts are survivable");
  h.m.note({ status: 504, reason: "timeout" });
  eq(h.m.state(), "degraded", "the third timeout degrades");
}
// A clean turn after a degrade recovers without waiting for a poll.
{
  const h = boot({ transport: true,
                   replies: [{ status: 503, body: envelopeText({ reason: "upstream_down", mode: "degraded" }) }] });
  await flush();
  eq(h.m.state(), "degraded", "start degraded");
  h.m.note({ status: 200, reason: null });
  eq(h.m.state(), "live", "a clean turn recovers to live");
  eq(h.m.badge(), "HOSTED DEMO · LIVE", "...and the badge flips back (§6.3, recovery is visible)");
}
// ...but a not-configured deployment never "recovers" on a stray note: it is sticky.
{
  const h = boot({ transport: true, replies: [{ status: 200, body: HEALTH_BARE }] });
  await flush();
  h.m.note({ status: 200, reason: null });
  eq(h.m.state(), "degraded", "gateway_not_configured is sticky for the session");
}

// 4g. degraded -> live on a poll, and the 30 s -> 5 min backoff ladder.
{
  const h = boot({
    transport: true,
    replies: [
      { status: 503, body: envelopeText({ reason: "upstream_down", mode: "degraded" }) },
      { status: 200, body: HEALTH_LIVE },
    ],
  });
  await flush();
  eq(h.m.state(), "degraded", "boot lands degraded");
  await h.fire();
  eq(h.m.state(), "live", "§6.3: a health poll returning live recovers, on its own");
  eq(h.m.badge(), "HOSTED DEMO · LIVE", "...visibly");
  eq(h.m.stats().polls, 2, "two polls happened, and that is recorded");
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
  const h = boot({ transport: true, replies: [{ status: 200, body: HEALTH_LIVE }] });
  await flush();
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

// --------------------------------------------------------------------------- //
// 5. sim/web/env.js — the badge, the pill, the banner and the needs-backend marks,
//    driven by the MODE and not by the hostname. Hermetic: a fake DOM, no browser.
// --------------------------------------------------------------------------- //
function fakeEl(id) {
  const el = {
    id: id || "", tagName: "SPAN", textContent: "", innerHTML: "", title: "", hidden: false,
    className: "", children: [], attrs: {},
    classList: {
      add: (c) => { if (!el.className.split(/\s+/).includes(c)) el.className = (el.className + " " + c).trim(); },
      remove: (c) => { el.className = el.className.split(/\s+/).filter((x) => x && x !== c).join(" "); },
      toggle: (c, on) => { on ? el.classList.add(c) : el.classList.remove(c); },
      contains: (c) => el.className.split(/\s+/).includes(c),
    },
    setAttribute: (k, v) => { el.attrs[k] = String(v); },
    getAttribute: (k) => (k in el.attrs ? el.attrs[k] : null),
    addEventListener: () => {},
    appendChild: (c) => { el.children.push(c); c.parentNode = el; return c; },
    insertBefore: (c) => { el.children.push(c); c.parentNode = el; return c; },
    remove: () => {},
    querySelector: (sel) => {
      const cls = sel.replace(/^\./, "");
      for (const c of el.children) if (c.classList.contains(cls)) return c;
      // env.js reads `.eb-text` out of an innerHTML string it just wrote, so the fake
      // element exposes a lazily-created stand-in for it.
      el._sub = el._sub || {};
      return (el._sub[cls] = el._sub[cls] || fakeEl(cls));
    },
  };
  return el;
}

function mountEnv(snapshot) {
  const els = {};
  const get = (id) => (els[id] = els[id] || fakeEl(id));
  const linkstate = fakeEl("linkstate");
  const bar = fakeEl("topbar");
  bar.appendChild(linkstate);
  const body = fakeEl("body");
  let cb = null;
  globalThis.location = { protocol: "https:", hostname: "sim.example", origin: "https://sim.example" };
  globalThis.document = {
    body,
    getElementById: (id) => (["tts-test", "speech-btn", "mic-btn", "bus-connect", "mic-status",
                              "bus-status", "tts-status"].includes(id) ? get(id) : null),
    querySelector: (sel) => (sel === "#topbar .linkstate" ? linkstate : null),
    createElement: (tag) => { const e = fakeEl(); e.tagName = String(tag).toUpperCase(); return e; },
  };
  get("bus-status").textContent = "not connected";
  const hints = [];
  globalThis.window = {
    moxieAudio: { setTtsHint: (h) => hints.push(h), hasCloudVoice: () => false, isSpeaking: () => false },
    moxieMode: snapshot === null ? undefined : {
      snapshot: () => snapshot,
      onChange: (fn) => { cb = fn; fn(snapshot); return () => {}; },
    },
  };
  globalThis.localStorage = { getItem: () => null, setItem: () => {} };
  globalThis.fetch = () => Promise.resolve({ ok: false });
  (0, eval)(ENV_SRC);
  const badge = bar.children.find((c) => c.className.includes("env-badge"));
  const pill = bar.children.find((c) => c.className.startsWith("mode-pill"));
  const banner = body.children.find((c) => c.id === "env-banner");
  return {
    badge, pill, banner, hints, body,
    el: get,
    push(next) { snapshot = next; if (cb) cb(next); },
    get bannerText() { return banner ? banner.querySelector(".eb-text").innerHTML : ""; },
  };
}

const snap = (over) => Object.assign({
  state: "degraded", reason: "gateway_not_configured", badge: "HOSTED DEMO", message: "",
  level: "ok", load: { level: "ok", inflight: 0, capacity: 4 }, limits: {},
  voice: false, ears: false, liveTurns: false, retryAfterS: 0,
}, over);

{
  // The fail-safe rendering: nothing configured => the page is exactly today's.
  const v = mountEnv(snap({}));
  eq(v.badge.textContent, "HOSTED DEMO", "not configured: today's badge");
  eq(v.pill.hidden, true, "not configured: no pill");
  eq(v.body.getAttribute("data-env"), "hosted", "the hostname still decides data-env");
  eq(v.body.getAttribute("data-mode"), "degraded", "...and the MODE is published too");
  ok(v.el("mic-btn").classList.contains("needs-backend"), "no ears => the mic is marked");
  ok(v.el("bus-connect").classList.contains("needs-backend"), "the live-robot link is always marked");
  ok(v.el("tts-test").classList.contains("needs-backend"), "no local Piper => TTS test is marked");
  ok(/only pre&#8209;scripted lines have audio/.test(v.hints.map((h) => h.html).join(" ")),
     "not configured: today's exact TTS wording");
  ok(/scripted child line/.test(v.el("mic-status").innerHTML), "not configured: today's mic wording");
  ok(/need a locally/.test(v.bannerText), "not configured: today's banner");

  // ...then the deployment turns out to be live. Same page object, honest new words.
  v.push(snap({ state: "live", reason: null, badge: "HOSTED DEMO · LIVE", message: "",
                voice: true, ears: true, liveTurns: true }));
  eq(v.badge.textContent, "HOSTED DEMO · LIVE", "live: the badge says so");
  eq(v.body.getAttribute("data-mode"), "live", "live: data-mode follows");
  eq(v.pill.hidden, true, "live and idle: nothing to apologise for");
  ok(!v.el("mic-btn").classList.contains("needs-backend"),
     "live ears => the mic mark is REMOVED (env.js:100 used to assert it unconditionally)");
  ok(!/needs the STT server/i.test(v.el("mic-btn").getAttribute("title") || ""),
     `...and its tooltip stops claiming a local server (got "${v.el("mic-btn").getAttribute("title")}")`);
  ok(v.el("bus-connect").classList.contains("needs-backend"),
     "...but a REAL robot's broker is still not available here, in every mode");
  ok(/own voice is live/.test(v.hints[v.hints.length - 1].html), "live: the voice line is honest");
  ok(/live brain answers on this page/.test(v.bannerText), "live: the banner stops claiming otherwise");
  ok(/forgets this conversation/.test(v.bannerText), "live: and says nothing persists");

  // ...then she gets busy.
  v.push(snap({ state: "live", reason: "at_capacity", badge: "HOSTED DEMO · BUSY",
                message: "Moxie has her hands full right now.", level: "full",
                voice: true, ears: true, liveTurns: false }));
  eq(v.badge.textContent, "HOSTED DEMO · BUSY", "busy: the badge changes");
  eq(v.pill.hidden, false, "busy: the pill appears");
  eq(v.pill.textContent, "Moxie has her hands full right now.", "busy: the pill carries §7's copy");
  eq(v.pill.title, "Moxie has her hands full right now.",
     "...and the title too, because the pill's text is dropped at phone widths");
  ok(v.pill.className.includes("level-full"), "busy: the pill is styled by level");
  ok(v.pill.getAttribute("aria-live") === "polite", "the pill is announced, not just shown");

  // ...then the budget runs out.
  v.push(snap({ state: "degraded", reason: "budget_exhausted", badge: "HOSTED DEMO · SCRIPTED",
                message: "Moxie’s live brain has used up today’s demo budget.", level: "ok" }));
  eq(v.badge.textContent, "HOSTED DEMO · SCRIPTED", "budget spent: the badge says scripted");
  eq(v.pill.hidden, false, "budget spent: the pill explains");
  ok(v.el("mic-btn").classList.contains("needs-backend"), "budget spent: the mic is marked again");
  ok(/need a locally/.test(v.bannerText), "budget spent: the banner goes back to the honest one");
}
{
  // mode.js absent entirely (a fork that did not copy it): the page must be today's.
  const v = mountEnv(null);
  eq(v.badge.textContent, "HOSTED DEMO", "no mode.js: today's badge");
  eq(v.pill.hidden, true, "no mode.js: no pill");
  eq(v.body.getAttribute("data-mode"), "boot", "no mode.js: data-mode says boot");
  ok(v.el("mic-btn").classList.contains("needs-backend"), "no mode.js: today's marks");
  ok(/only pre&#8209;scripted lines have audio/.test(v.hints.map((h) => h.html).join(" ")),
     "no mode.js: today's exact wording");
}

// --------------------------------------------------------------------------- //
// 6. C1, as a repo lint. The repo is PUBLIC: no key, token, account id or deployment
//    hostname may be committed or shipped to the browser. Run over the WHOLE file
//    (comments included) on purpose — a real key in a comment is still a leaked key.
// --------------------------------------------------------------------------- //
{
  const FORBIDDEN = [
    [/mattvalancy/i, "a deployment hostname"],
    [/graphlings/i, "the gateway hostname"],
    [/\b(?:sk|pk|rk)-[A-Za-z0-9_-]{12,}/, "a key-shaped token"],
    [/\b[0-9a-f]{32}\b/, "a Cloudflare account id"],
  ];
  const files = [
    "functions/api/health.js",
    "functions/api/_lib/env.js",
    "functions/api/_lib/envelope.js",
    "sim/web/mode.js",
    "sim/web/env.js",
  ];
  for (const rel of files) {
    const text = readFileSync(join(repo, rel), "utf8");
    for (const [rx, what] of FORBIDDEN)
      ok(!rx.test(text), `${rel} must not contain ${what} (${rx})`);
  }
  // wrangler.toml is committed and world-readable, so it may never carry variables.
  const wrangler = readFileSync(join(repo, "wrangler.toml"), "utf8");
  ok(!/^\s*\[vars\]/m.test(wrangler), "wrangler.toml must have NO [vars] block — it is public");
  ok(!/account_id/.test(wrangler), "wrangler.toml must carry no account id");
}

if (fails.length) {
  console.log("❌ mode tests FAILED:");
  for (const f of fails) console.log("   -", f);
  process.exit(1);
}
console.log("✅ mode tests OK — /api/health answers gateway_not_configured with no variables set "
  + "(one request, no poll storm, page byte-identical to today); the envelope is a fixed key allowlist "
  + "with no URL, key or model id in any body or header; boot→offline on an absent/malformed route; "
  + "live/degraded/busy/budget badges and copy per §7; 429 soft-degrades without leaving live; "
  + "3 strikes → degraded; 30 s→5 min backoff; never polls while hidden; env.js drives the badge, "
  + "pill, banner and needs-backend marks from the MODE, not the hostname");
