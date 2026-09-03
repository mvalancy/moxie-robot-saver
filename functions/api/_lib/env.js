/* functions/api/_lib/env.js — read and validate the DEMO_* configuration surface.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §5 (the variable table) and §4.2
 * (what the browser is allowed to know). This module is the ONLY place a DEMO_*
 * variable is read, so every default, clamp and required-value rule lives once.
 *
 * The constraints that bite here, restated because they are easy to break later:
 *
 *   C1 — THE REPO IS PUBLIC. Nothing in this file may carry a key, a token, an
 *        account id or a deployment hostname. Every secret arrives at runtime as a
 *        Cloudflare environment binding on `context.env` and is read here and nowhere
 *        else. `wrangler.toml` gets no `[vars]` block: it is committed and
 *        world-readable.
 *   C3 — NOTHING HARD-CODED TO OUR GATEWAY OR OUR DOMAIN. `DEMO_GATEWAY_BASE_URL` has
 *        NO default on purpose. `mqtt/config.py` does carry a Python default for the
 *        local stack; copying it here would make an unconfigured fork silently call
 *        *our* gateway. Unset means degraded, never "guess ours".
 *   C5 — FAIL-SAFE DEFAULT. With no variables set at all, `configured` is false and the
 *        mode is `gateway_not_configured`. A branch preview with no secrets is therefore
 *        automatically the plain scripted demo.
 *
 * Structural guard against C1: `baseUrl`, `apiKey` and `ticketSecret` are defined
 * NON-ENUMERABLE on the returned config, so `JSON.stringify(cfg)` — the shape of every
 * accidental leak — cannot contain them. `sim/test_mode.mjs` asserts that.
 */

/** §5's table, as code. A value absent from here has no default and is required. */
export const DEFAULTS = Object.freeze({
  DEMO_ENABLED: "1",
  DEMO_TTS_FORMAT: "wav",
  DEMO_TTS_SAMPLE_RATE: 22050,
  DEMO_DEVICE_ID: "d_sim",
  DEMO_MAX_TOKENS: 160,
  DEMO_MAX_INPUT_CHARS: 500,
  DEMO_MAX_TTS_CHARS: 300,
  DEMO_MAX_CONTEXT_CHARS: 1500,
  DEMO_MAX_HISTORY_TURNS: 4,
  DEMO_MAX_AUDIO_BYTES: 500000,
  DEMO_MIN_AUDIO_BYTES: 2000,
  DEMO_MAX_RECORD_MS: 15000,
  DEMO_CHAT_PER_MIN: 5,
  DEMO_CHAT_PER_HOUR: 40,
  DEMO_CHAT_PER_DAY: 150,
  DEMO_SPEECH_PER_MIN: 10,
  DEMO_SPEECH_PER_HOUR: 80,
  DEMO_STT_PER_MIN: 10,
  DEMO_STT_PER_HOUR: 60,
  DEMO_MAX_CONCURRENT_CHAT: 4,
  DEMO_MAX_CONCURRENT_SPEECH: 8,
  DEMO_UNIT_BUDGET_HOUR: 600,
  DEMO_UNIT_BUDGET_DAY: 4000,
  DEMO_CHAT_TIMEOUT_MS: 20000,
  DEMO_SPEECH_TIMEOUT_MS: 12000,
  DEMO_STT_TIMEOUT_MS: 12000,
  DEMO_TICKET_TTL_S: 60,
});

/** The two optional Cloudflare Access service-token variables.
 *
 *  WHY THEY EXIST. The owner's gateway is expected to sit behind a **Cloudflare Tunnel**.
 *  A plain public tunnel hostname needs nothing extra — it is just a base URL. But a
 *  tunnel protected by **Cloudflare Access** answers an unauthenticated server-side
 *  `fetch` with an **HTML login page**, with a 200 status. That is the worst possible
 *  failure shape: it looks exactly like a broken gateway, and from a bare 502 it is
 *  maddening to diagnose. So a service token can be configured, and when it is, both
 *  routes send it on every upstream call as `CF-Access-Client-Id` /
 *  `CF-Access-Client-Secret`.
 *
 *  BOTH OR NEITHER. Exactly one of the pair is a MISCONFIGURATION, not a partial
 *  credential: calling upstream half-credentialled would produce that same HTML login
 *  page while looking configured. `readConfig` therefore reports it in `missing` so every
 *  route answers `gateway_not_configured` and makes no upstream call at all.
 *
 *  The secret half is a **secret** binding like the API key, and is non-enumerable on the
 *  returned config for the same reason (see the header). */
export const ACCESS_VARS = Object.freeze([
  "DEMO_GATEWAY_ACCESS_CLIENT_ID",
  "DEMO_GATEWAY_ACCESS_CLIENT_SECRET",
]);

/** The three values that must be present for `mode: "live"` (§3.2). */
export const REQUIRED_FOR_LIVE = Object.freeze([
  "DEMO_GATEWAY_BASE_URL",
  "DEMO_GATEWAY_API_KEY",
  "DEMO_CHAT_MODEL",
]);

/**
 * The `voice` field to send for a model name — `piper-amy` → `amy`.
 *
 * **THE GATEWAY REQUIRES THIS FIELD AND IGNORES ITS VALUE. Omitting it is an HTTP 500.**
 * Transcribed from `mqtt/moxie_sdk/tts.py::voice_for_model` (:80-90), whose docstring says
 * exactly that and cites `docs/guides/litellm-tts-setup.md` ("Live since 2026-09-02"): the
 * MODEL NAME selects the Piper voice, so the field only has to be present and sane.
 *
 * This function exists because of a bug the four-call gateway probe caught. The spec's §5
 * reads `mqtt/config.py`:91-92 as "our gateway encodes the voice in the model id, so empty
 * is correct there" and concludes the field can be omitted. That is a misreading:
 * `MOXIE_TTS_VOICE=""` means "do not set the env var", and `tts.py` then DERIVES the value
 * — it never sends nothing. A `/api/speech` that omitted the field answered 500 on every
 * single call, which would have shipped a hosted demo with a permanently silent voice and
 * an `upstream_down` badge nobody could explain. `sim/tools/probe_demo_gateway.mjs` is
 * what found it, and it is the whole reason that probe exists.
 *
 * A model whose suffix is not a word — `tts-1` → `1` — falls back to OpenAI's own default
 * voice, which is what an OpenAI-shaped endpoint would want anyway.
 */
export function voiceForModel(model) {
  const tail = String(model || "").split("-").pop().trim();
  return /^[A-Za-z]+$/.test(tail) ? tail : "alloy";
}

/** The only audio formats `audio.js` can decode (§5, mirroring mqtt/config.py:101). */
export const TTS_FORMATS = Object.freeze(["wav", "pcm"]);

/** Exactly the cap names the browser may be told (§4.2). Model ids and URLs are absent
 *  from this list on purpose and `publicLimits` cannot grow them by accident. */
export const PUBLIC_LIMIT_KEYS = Object.freeze([
  "max_input_chars",
  "max_tts_chars",
  "max_tokens",
  "chat_per_min",
  // The three the MICROPHONE needs (P1). `max_record_ms` is the one that actually bounds
  // the cost of the ears, and it can only be enforced in the browser: §4.1 is explicit
  // that `DEMO_MAX_AUDIO_BYTES` is NOT a duration cap for a compressed container —
  // 500 KB of Opus is minutes, not seconds — so the byte cap alone is not an honest
  // ceiling and the recorder has to stop itself. The two byte caps let `mic.js` skip an
  // upload that is already doomed (too small to be speech, too large to be accepted),
  // which is a request that never happens rather than one that is refused.
  "max_record_ms",
  "max_audio_bytes",
  "min_audio_bytes",
]);

/** The built-in persona. Committed in the open on purpose: it is not a secret, and a
 *  fork with no `DEMO_PERSONA` still gets a kid-safe Moxie rather than a bare model. */
export const DEFAULT_PERSONA =
  "You are Moxie, a warm, curious, kid-safe robot companion talking with a child. " +
  "Keep replies to one or two short spoken sentences. Be encouraging and playful, " +
  "never scary, never sarcastic. Never ask for or repeat personal details. " +
  "If a topic is not for children, say so kindly and offer something else.";

function str(env, name, fallback) {
  const raw = env && env[name];
  if (raw === undefined || raw === null) return fallback;
  const v = String(raw).trim();
  return v === "" ? fallback : v;
}

/** A falsy switch is "0"/"false"/"no"/"off"/"" — anything else is on (§5 `DEMO_ENABLED`). */
function bool(env, name, fallback) {
  const v = str(env, name, null);
  if (v === null) return fallback;
  return !/^(0|false|no|off)$/i.test(v);
}

/** The repo's allowlist idiom (mqtt/moxie_sdk/cloud_config.py:435-475): coerce, clamp,
 *  and fall back to the default on anything unusable — a bad number must never become a
 *  bigger cap than the default. */
function int(env, name, min, max, notes) {
  const dflt = DEFAULTS[name];
  const v = str(env, name, null);
  if (v === null) return dflt;
  const n = Number(v);
  if (!Number.isFinite(n) || !Number.isInteger(n)) {
    notes.push(name + ": not an integer, using the default");
    return dflt;
  }
  if (n < min || n > max) {
    notes.push(name + ": out of range, using the default");
    return dflt;
  }
  return n;
}

/** `DEMO_ALLOWED_ORIGINS` — comma separated. Empty means "the request's own origin
 *  only", which is what lets a fork on any domain work with zero configuration (C3). */
function origins(env) {
  const raw = str(env, "DEMO_ALLOWED_ORIGINS", "");
  const out = [];
  for (const part of raw.split(",")) {
    const v = part.trim();
    if (!v) continue;
    try { out.push(new URL(v).origin); } catch { out.push(v); }
  }
  return out;
}

/**
 * Read the whole DEMO_* surface off a Pages `context.env`.
 * @param {Record<string,unknown>} env
 * @returns {object} the validated config. `baseUrl`/`apiKey`/`ticketSecret` are
 *   non-enumerable (see the header): readable as properties, invisible to JSON.
 */
export function readConfig(env) {
  const notes = [];
  const e = env || {};

  const enabled = bool(e, "DEMO_ENABLED", true);
  const baseUrl = str(e, "DEMO_GATEWAY_BASE_URL", "");
  const apiKey = str(e, "DEMO_GATEWAY_API_KEY", "");
  const chatModel = str(e, "DEMO_CHAT_MODEL", "");
  const ttsModel = str(e, "DEMO_TTS_MODEL", "");
  const sttModel = str(e, "DEMO_STT_MODEL", "");

  let ttsFormat = String(str(e, "DEMO_TTS_FORMAT", DEFAULTS.DEMO_TTS_FORMAT)).toLowerCase();
  if (!TTS_FORMATS.includes(ttsFormat)) {
    notes.push("DEMO_TTS_FORMAT: only " + TTS_FORMATS.join("/") + " are decodable, using wav");
    ttsFormat = DEFAULTS.DEMO_TTS_FORMAT;
  }

  // Cloudflare Access service token — optional, but BOTH OR NEITHER (see ACCESS_VARS).
  const accessId = str(e, "DEMO_GATEWAY_ACCESS_CLIENT_ID", "");
  const accessSecret = str(e, "DEMO_GATEWAY_ACCESS_CLIENT_SECRET", "");

  const missing = [];
  if (!baseUrl) missing.push("DEMO_GATEWAY_BASE_URL");
  if (!apiKey) missing.push("DEMO_GATEWAY_API_KEY");
  if (!chatModel) missing.push("DEMO_CHAT_MODEL");
  // Half a service token is worse than none: it looks configured and answers an HTML
  // login page. Named in `missing` so the fail-safe path of C5 handles it, and noted so
  // an operator reading `/api/health` server-side can see WHICH half is absent.
  if (accessId && !accessSecret) {
    missing.push("DEMO_GATEWAY_ACCESS_CLIENT_SECRET");
    notes.push("DEMO_GATEWAY_ACCESS_CLIENT_ID is set without its secret: a Cloudflare " +
               "Access service token needs BOTH halves, so the gateway is treated as unconfigured");
  }
  if (accessSecret && !accessId) {
    missing.push("DEMO_GATEWAY_ACCESS_CLIENT_ID");
    notes.push("DEMO_GATEWAY_ACCESS_CLIENT_SECRET is set without its client id: a Cloudflare " +
               "Access service token needs BOTH halves, so the gateway is treated as unconfigured");
  }

  const cfg = {
    enabled,
    configured: enabled && missing.length === 0,
    missing,
    notes,
    chatModel,
    ttsModel,
    // ALWAYS a non-empty string when a TTS model is configured: `DEMO_TTS_VOICE` when set,
    // otherwise derived from the model name. The field is mandatory upstream (see
    // `voiceForModel`), so the derivation lives HERE rather than at the call site — that
    // way no route can omit it by forgetting to.
    ttsVoice: str(e, "DEMO_TTS_VOICE", "") || (ttsModel ? voiceForModel(ttsModel) : ""),
    ttsFormat,
    // Read ONLY when the format is pcm — a wav reply carries its own rate (§5). The
    // clamp mirrors audio.js:617-618 so a configured rate can never be one the browser
    // decoder would refuse.
    ttsSampleRate: int(e, "DEMO_TTS_SAMPLE_RATE", 3000, 384000, notes),
    sttModel,
    persona: str(e, "DEMO_PERSONA", DEFAULT_PERSONA),
    deviceId: str(e, "DEMO_DEVICE_ID", DEFAULTS.DEMO_DEVICE_ID),
    allowedOrigins: origins(e),
    maxTokens: int(e, "DEMO_MAX_TOKENS", 1, 4096, notes),
    maxInputChars: int(e, "DEMO_MAX_INPUT_CHARS", 1, 20000, notes),
    maxTtsChars: int(e, "DEMO_MAX_TTS_CHARS", 1, 20000, notes),
    maxContextChars: int(e, "DEMO_MAX_CONTEXT_CHARS", 0, 100000, notes),
    maxHistoryTurns: int(e, "DEMO_MAX_HISTORY_TURNS", 0, 64, notes),
    maxAudioBytes: int(e, "DEMO_MAX_AUDIO_BYTES", 1, 50000000, notes),
    minAudioBytes: int(e, "DEMO_MIN_AUDIO_BYTES", 0, 50000000, notes),
    // The CLIENT-SIDE recording cap (§4.1). It is enforced by `sim/web/mic.js`, not by a
    // route — a Function only ever sees the finished upload — so this value's whole job is
    // to be published in `publicLimits` and obeyed by the recorder. It is still read and
    // clamped here so the deployment has ONE place that decides it, and so a fork can
    // shorten it without touching JavaScript that ships to a browser.
    maxRecordMs: int(e, "DEMO_MAX_RECORD_MS", 1000, 600000, notes),
    chatPerMin: int(e, "DEMO_CHAT_PER_MIN", 1, 100000, notes),
    chatPerHour: int(e, "DEMO_CHAT_PER_HOUR", 1, 1000000, notes),
    chatPerDay: int(e, "DEMO_CHAT_PER_DAY", 1, 10000000, notes),
    speechPerMin: int(e, "DEMO_SPEECH_PER_MIN", 1, 100000, notes),
    speechPerHour: int(e, "DEMO_SPEECH_PER_HOUR", 1, 1000000, notes),
    sttPerMin: int(e, "DEMO_STT_PER_MIN", 1, 100000, notes),
    sttPerHour: int(e, "DEMO_STT_PER_HOUR", 1, 1000000, notes),
    maxConcurrentChat: int(e, "DEMO_MAX_CONCURRENT_CHAT", 1, 10000, notes),
    maxConcurrentSpeech: int(e, "DEMO_MAX_CONCURRENT_SPEECH", 1, 10000, notes),
    unitBudgetHour: int(e, "DEMO_UNIT_BUDGET_HOUR", 0, 100000000, notes),
    unitBudgetDay: int(e, "DEMO_UNIT_BUDGET_DAY", 0, 100000000, notes),
    chatTimeoutMs: int(e, "DEMO_CHAT_TIMEOUT_MS", 1000, 120000, notes),
    speechTimeoutMs: int(e, "DEMO_SPEECH_TIMEOUT_MS", 1000, 120000, notes),
    sttTimeoutMs: int(e, "DEMO_STT_TIMEOUT_MS", 1000, 120000, notes),
    ticketTtlS: int(e, "DEMO_TICKET_TTL_S", 5, 3600, notes),
    // WHETHER a service token is in play, never what it is (§4.2). A route needs this to
    // decide whether to add the two headers; nothing else may know.
    accessToken: !!(accessId && accessSecret),
  };

  // Voice and ears are "configured at all" (§3.2), which means the gateway itself is
  // configured too: a TTS model with no gateway to call is not a voice.
  cfg.voice = cfg.configured && !!ttsModel;
  cfg.ears = cfg.configured && !!sttModel;

  // The three values a leak would actually cost us. Non-enumerable, so they are
  // readable by the routes and invisible to JSON.stringify (see the header).
  for (const [name, value] of [
    ["baseUrl", baseUrl],
    ["apiKey", apiKey],
    // A Cloudflare Access service token is a credential in both halves — the client id is
    // as good as a username and is just as much a thing an attacker would like to have —
    // so both are non-enumerable, exactly like the API key.
    ["accessClientId", accessId],
    ["accessClientSecret", accessSecret],
    // §5: derived from the API key when unset, so the minimum config is two values.
    // The derivation itself is P0-b's functions/api/_lib/hmac.js (HKDF); this only
    // carries the configured material.
    ["ticketSecret", str(e, "DEMO_TICKET_SECRET", "")],
  ]) {
    Object.defineProperty(cfg, name, { value, enumerable: false, writable: false, configurable: false });
  }
  return cfg;
}

/**
 * The mode this configuration can support, with no gateway call and no counters.
 * `mode` is `live` only when a base URL, a key and a chat model are all present and the
 * kill switch is on (§3.2). Budget exhaustion is a *counter* state, so it is passed in
 * rather than guessed: P0-a ships no counter and therefore no budget reason.
 */
export function modeOf(cfg, budget) {
  if (!cfg.enabled) return { mode: "degraded", reason: "gateway_not_configured" };
  if (!cfg.configured) return { mode: "degraded", reason: "gateway_not_configured" };
  if (budget && budget.exhausted) return { mode: "degraded", reason: "budget_exhausted" };
  return { mode: "live", reason: null };
}

/**
 * The headers every upstream call carries. ONE function, so `chat.js` and `speech.js`
 * cannot drift apart on the credentials they present.
 *
 * `Authorization` is the gateway key. The two `CF-Access-*` headers are added only when a
 * complete service token is configured (see `ACCESS_VARS`) — the shape Cloudflare Access
 * expects for a non-interactive client. NONE of these values is ever put in a response, a
 * log line or an error string; this object goes into `fetch()` and nowhere else (§4.2).
 *
 * @param {object} cfg
 * @param {string} contentType
 */
export function upstreamHeaders(cfg, contentType) {
  const h = {
    Authorization: "Bearer " + cfg.apiKey,
    "Content-Type": contentType || "application/json",
  };
  if (cfg.accessToken) {
    h["CF-Access-Client-Id"] = cfg.accessClientId;
    h["CF-Access-Client-Secret"] = cfg.accessClientSecret;
  }
  return h;
}

/** The caps the page may know, and nothing else (§4.2 / PUBLIC_LIMIT_KEYS). */
export function publicLimits(cfg) {
  const all = {
    max_input_chars: cfg.maxInputChars,
    max_tts_chars: cfg.maxTtsChars,
    max_tokens: cfg.maxTokens,
    chat_per_min: cfg.chatPerMin,
    max_record_ms: cfg.maxRecordMs,
    max_audio_bytes: cfg.maxAudioBytes,
    min_audio_bytes: cfg.minAudioBytes,
  };
  const out = {};
  for (const k of PUBLIC_LIMIT_KEYS) out[k] = all[k];
  return out;
}
