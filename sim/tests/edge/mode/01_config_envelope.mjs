/* test_mode §1–2: `_lib/env.js` (§5 defaults, clamps, fail-safe) and `_lib/envelope.js`. */
import {
  ok, eq, deep, lib, env2, FULL,
} from "./harness.mjs";

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

  // §5's defaults, exactly. (Context and history were raised TOGETHER: more turns without
  // the byte budget would be trimmed oldest-first by `hmac.js` anyway.)
  const d = lib.readConfig({});
  for (const [k, want, label] of [
    ["maxTokens", 160, "DEMO_MAX_TOKENS default"],
    ["maxInputChars", 500, "DEMO_MAX_INPUT_CHARS default"],
    ["maxTtsChars", 300, "DEMO_MAX_TTS_CHARS default"],
    ["maxContextChars", 4000, "DEMO_MAX_CONTEXT_CHARS default — the real bound on history"],
    ["maxHistoryTurns", 12, "DEMO_MAX_HISTORY_TURNS default — twelve, as the robot path uses"],
    ["maxAudioBytes", 500000, "DEMO_MAX_AUDIO_BYTES default"],
    ["minAudioBytes", 2000, "DEMO_MIN_AUDIO_BYTES default"],
    ["chatPerMin", 5, "DEMO_CHAT_PER_MIN default"],
    ["chatPerHour", 40, "DEMO_CHAT_PER_HOUR default"],
    ["chatPerDay", 150, "DEMO_CHAT_PER_DAY default"],
    ["speechPerMin", 10, "DEMO_SPEECH_PER_MIN default"],
    ["speechPerHour", 80, "DEMO_SPEECH_PER_HOUR default"],
    ["sttPerMin", 10, "DEMO_STT_PER_MIN default"],
    ["sttPerHour", 60, "DEMO_STT_PER_HOUR default"],
    ["maxConcurrentChat", 4, "DEMO_MAX_CONCURRENT_CHAT default"],
    ["maxConcurrentSpeech", 8, "DEMO_MAX_CONCURRENT_SPEECH default"],
    ["unitBudgetHour", 600, "DEMO_UNIT_BUDGET_HOUR default"],
    ["unitBudgetDay", 4000, "DEMO_UNIT_BUDGET_DAY default"],
    ["chatTimeoutMs", 20000, "DEMO_CHAT_TIMEOUT_MS default"],
    ["speechTimeoutMs", 12000, "DEMO_SPEECH_TIMEOUT_MS default"],
    ["sttTimeoutMs", 12000, "DEMO_STT_TIMEOUT_MS default"],
    ["ticketTtlS", 60, "DEMO_TICKET_TTL_S default"],
    ["ttsFormat", "wav", "DEMO_TTS_FORMAT default"],
    ["ttsSampleRate", 22050, "DEMO_TTS_SAMPLE_RATE default"],
    ["deviceId", "d_sim", "DEMO_DEVICE_ID default (matches bridge/)"],
  ]) eq(d[k], want, label);
  ok(d.persona.length > 40, "a built-in persona must ship, so a fork is not a bare model");

  // The allowlist idiom (cloud_config.py:435-475): coerce, clamp, and NEVER let a bad
  // value become a bigger cap than the default.
  eq(lib.readConfig({ DEMO_MAX_INPUT_CHARS: "banana" }).maxInputChars, 500, "garbage falls back");
  eq(lib.readConfig({ DEMO_MAX_INPUT_CHARS: "1e9" }).maxInputChars, 500, "out of range falls back");
  eq(lib.readConfig({ DEMO_MAX_INPUT_CHARS: "-5" }).maxInputChars, 500, "negative falls back");
  eq(lib.readConfig({ DEMO_MAX_INPUT_CHARS: "12.5" }).maxInputChars, 500, "non-integer falls back");
  eq(lib.readConfig({ DEMO_MAX_INPUT_CHARS: " 250 " }).maxInputChars, 250, "a good value is taken");
  eq(lib.readConfig({ DEMO_MAX_TOKENS: "999999" }).maxTokens, 160, "an absurd token cap falls back");
  // Only wav/pcm are decodable by voice/ (§5, mirroring mqtt/config.py:101).
  eq(lib.readConfig({ DEMO_TTS_FORMAT: "mp3" }).ttsFormat, "wav", "an undecodable format falls back to wav");
  eq(lib.readConfig({ DEMO_TTS_FORMAT: "PCM" }).ttsFormat, "pcm", "pcm is accepted, case-insensitively");
  // voice/:617-618 clamps the rate; a configured rate the decoder would refuse is not
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
  for (const [reason, code, label] of [
    [null, 200, "a clean reply is 200"],
    ["rate_limited", 429, "rate_limited is 429"],
    ["at_capacity", 503, "at_capacity is 503"],
    ["budget_exhausted", 503, "budget_exhausted is 503"],
    ["upstream_down", 503, "upstream_down is 503"],
    ["gateway_not_configured", 503, "gateway_not_configured is 503 on a spending route"],
    ["timeout", 504, "timeout is 504"],
    ["bad_request", 400, "bad_request is 400"],
    ["too_long", 400, "too_long is 400"],
    ["too_short", 400, "too_short is 400"],
    ["bad_ticket", 400, "bad_ticket is 400"],
    ["forbidden_origin", 403, "forbidden_origin is 403"],
  ]) eq(st(reason), code, label);
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
