/* functions/api/_lib/env.js — read and validate the DEMO_* configuration surface.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §5 (the variable table) and §4.2 (what
 * the browser may know). The ONLY place a DEMO_* variable is read.
 *
 *   C1 — THE REPO IS PUBLIC. No key, token, account id or deployment hostname here.
 *        Secrets arrive as Cloudflare bindings on `context.env`; `wrangler.toml` has no
 *        `[vars]` block.
 *   C3 — NOTHING HARD-CODED TO OUR GATEWAY OR DOMAIN. `DEMO_GATEWAY_BASE_URL` has no
 *        default, so an unconfigured fork degrades instead of calling *our* gateway.
 *   C5 — FAIL-SAFE DEFAULT. No variables ⇒ `configured: false` ⇒ `gateway_not_configured`,
 *        so a secretless branch preview is the plain scripted demo.
 *
 * Credentials are defined NON-ENUMERABLE on the returned config, so `JSON.stringify(cfg)`
 * — the shape of every accidental leak — cannot contain them (`sim/test_mode.mjs`).
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
  // The byte cap is the real history bound: `hmac.js` drops oldest-first until it fits,
  // so it must be large enough for DEMO_MAX_HISTORY_TURNS. `body.js::maxJsonBodyBytes`
  // derives the accepted body size from it.
  DEMO_MAX_CONTEXT_CHARS: 4000,
  // Matches the robot path (`mqtt/moxie_sdk/apps/llm_app.py` max_history); the byte cap
  // above, not this count, bounds the prompt.
  DEMO_MAX_HISTORY_TURNS: 12,
  // Repetition pressure (OpenAI vocabulary). Modest: sized to break an affirmation loop,
  // not to forbid a catchphrase. `chat.js` drops them permanently if the gateway rejects
  // them. 0 = not sent.
  DEMO_FREQUENCY_PENALTY: 0.4,
  DEMO_PRESENCE_PENALTY: 0.3,
  // The per-turn shape cue (`_lib/turnshape.js`): one server-built sentence, no extra
  // call. `0` removes it and the upstream body is byte-identical to one without it (§4.10).
  DEMO_TURN_SHAPE: "1",
  // The one repetition lever that SPENDS: a reply identical to an earlier one in this
  // conversation is asked for once more (`chat.js` step 8b). 0 disables it for a
  // deployment on a tight unit budget.
  DEMO_REROLL: "1",
  // Where our instructions sit in the message array (`_lib/prompt.js`, §3.3): `anchor`
  // (persona first, a short anchor system message after the child's line) or `single` (ONE
  // leading system message, for chat templates that reject or silently drop a system
  // message that is not first). Measured per model before any switch (§4.10).
  DEMO_PROMPT_LAYOUT: "anchor",
  DEMO_MAX_AUDIO_BYTES: 500000,
  DEMO_MIN_AUDIO_BYTES: 2000,
  DEMO_MAX_RECORD_MS: 15000,
  DEMO_STT_FORMATS: "wav",
  DEMO_CHAT_PER_MIN: 5,
  DEMO_CHAT_PER_HOUR: 40,
  DEMO_CHAT_PER_DAY: 150,
  DEMO_SPEECH_PER_MIN: 10,
  DEMO_SPEECH_PER_HOUR: 80,
  // The voice's and the ears' per-IP DAY: chat's day (150 turns) at each hour cap's own
  // ratio to chat's hour (speech 80/40 = 2 a reply, against 1.6 measured with one ticket per
  // sentence; ears 60/40 = 1.5 uploads a spoken turn), so a visitor meets chat's day first.
  // One address then spends at most 150x3 + 300x2 + 225x2 = 1 500 units a day, under half
  // of DEMO_UNIT_BUDGET_DAY; with no day window it alone could spend the colo's whole day.
  // 0 = no day window for that route (the behaviour before 2026-10-08).
  DEMO_SPEECH_PER_DAY: 300,
  DEMO_STT_PER_MIN: 10,
  DEMO_STT_PER_HOUR: 60,
  DEMO_STT_PER_DAY: 225,
  DEMO_MAX_CONCURRENT_CHAT: 4,
  DEMO_MAX_CONCURRENT_SPEECH: 8,
  DEMO_QUEUE_MAX_WAIT_MS: 2500,
  DEMO_QUEUE_MAX_DEPTH: 8,
  DEMO_CACHE_COUNTER: "1",
  DEMO_CACHE_TIMEOUT_MS: 250,
  DEMO_TTS_CACHE: "1",
  DEMO_TTS_CACHE_TTL_S: 86400,
  DEMO_TTS_CACHE_TIMEOUT_MS: 1000,
  DEMO_UNIT_BUDGET_HOUR: 600,
  DEMO_UNIT_BUDGET_DAY: 4000,
  // Measured 2026-10-08 on `moxie-brain-dense` + `single` (169 turns: the pre-flip A/B, the
  // production eval_live run, the voice-latency browser turns): p50 1.7 s, p99 3.4 s, max
  // 4.7 s. 10 s is about 3x that p99, and still holds the gateway's fallback (a first model
  // that fails at its own worst, then `moxie-brain`'s p99 of 4.1 s). 20 s made a hung
  // gateway cost a visitor 20 s a turn. The re-roll gets what is left (`reply.js`).
  DEMO_CHAT_TIMEOUT_MS: 10000,
  DEMO_SPEECH_TIMEOUT_MS: 12000,
  DEMO_STT_TIMEOUT_MS: 12000,
  DEMO_TICKET_TTL_S: 60,
  DEMO_TURNSTILE_TIMEOUT_MS: 2000,
});

/** The optional Cloudflare Access service-token pair. A gateway behind an Access-protected
 *  tunnel answers an unauthenticated `fetch` with an HTML login page at 200 — which looks
 *  exactly like a broken gateway — so when configured, both halves are sent upstream as
 *  `CF-Access-Client-Id` / `CF-Access-Client-Secret`.
 *
 *  BOTH OR NEITHER: half a pair would produce that same login page while looking
 *  configured, so `readConfig` reports it in `missing` (⇒ `gateway_not_configured`, no
 *  upstream call). Both halves are non-enumerable credentials. */
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

/** The Cloudflare Turnstile pair. The SECRET verifies a token; the SITEKEY (public, a
 *  plain variable) lets the browser mint one. BOTH OR NEITHER: a secret alone refuses every
 *  visitor, a sitekey alone renders a widget nothing checks — so half a pair goes in
 *  `missing`, same rule as `ACCESS_VARS`.
 *
 *  Neither set ⇒ enforcement OFF, deliberately (C5): a preview's platform hostname is not on
 *  the widget's domain list, so a real challenge there could never pass. The optional
 *  `DEMO_TURNSTILE_HOSTS` defaults to the request's own hostname
 *  (`./turnstile.js::hostAllowed`). */
export const TURNSTILE_VARS = Object.freeze([
  "DEMO_TURNSTILE_SECRET",
  "DEMO_TURNSTILE_SITEKEY",
]);

/**
 * The `voice` field to send for a model name — `piper-amy` → `amy`.
 *
 * THE GATEWAY REQUIRES THIS FIELD AND IGNORES ITS VALUE; omitting it is an HTTP 500 (the
 * model name selects the Piper voice). Transcribed from
 * `mqtt/moxie_sdk/tts.py::voice_for_model`; found by `sim/tools/probe_demo_gateway.mjs`.
 * A non-word suffix (`tts-1` → `1`) falls back to OpenAI's default voice.
 */
export function voiceForModel(model) {
  const tail = String(model || "").split("-").pop().trim();
  return /^[A-Za-z]+$/.test(tail) ? tail : "alloy";
}

/** The only audio formats `audio.js` can decode (§5, mirroring mqtt/config.py:101). */
export const TTS_FORMATS = Object.freeze(["wav", "pcm"]);

/** Every container `functions/api/transcribe.js` knows how to name for an upload. Which
 *  of them this deployment forwards is `DEMO_STT_FORMATS` (below). */
export const STT_CONTAINERS = Object.freeze(["wav", "webm", "ogg", "mp4", "mp3", "flac"]);

/**
 * `DEMO_STT_FORMATS`: the containers the gateway is believed to accept at
 * `/audio/transcriptions`.
 *
 * THE DEFAULT IS `wav` ALONE, BY MEASUREMENT (§10 assumption 15): the probed gateway
 * transcribed 16 kHz RIFF/WAVE and answered 500 for webm/Opus, ogg/Opus and mp4/AAC alike —
 * it decodes PCM only. Forwarding anyway would cost a real call, and the 500 maps to
 * `upstream_down` (503), which degrades the WHOLE page; refusing here is free and per-turn.
 * A fork with a more capable gateway widens the list (C3).
 */
function sttFormats(env) {
  const raw = str(env, "DEMO_STT_FORMATS", DEFAULTS.DEMO_STT_FORMATS);
  const out = [];
  for (const part of String(raw).split(",")) {
    const v = part.trim().toLowerCase();
    if (STT_CONTAINERS.includes(v) && !out.includes(v)) out.push(v);
  }
  // An unusable value falls back to the default, not to "nothing" (a silent ears-off).
  return out.length ? out : [DEFAULTS.DEMO_STT_FORMATS];
}

/** The message layouts `_lib/prompt.js::buildUpstreamBody` can build (§3.3). A closed set:
 *  an unknown `DEMO_PROMPT_LAYOUT` falls back to the default with a note, never to a layout
 *  nobody measured. */
export const PROMPT_LAYOUTS = Object.freeze(["anchor", "single"]);

function promptLayout(env, notes) {
  const v = String(str(env, "DEMO_PROMPT_LAYOUT", DEFAULTS.DEMO_PROMPT_LAYOUT)).toLowerCase();
  if (PROMPT_LAYOUTS.includes(v)) return v;
  notes.push("DEMO_PROMPT_LAYOUT: not one of " + PROMPT_LAYOUTS.join("/") + ", using " +
             DEFAULTS.DEMO_PROMPT_LAYOUT);
  return DEFAULTS.DEMO_PROMPT_LAYOUT;
}

/** Exactly the cap names the browser may be told (§4.2). Model ids and URLs are absent
 *  from this list on purpose and `publicLimits` cannot grow them by accident. */
export const PUBLIC_LIMIT_KEYS = Object.freeze([
  "max_input_chars",
  "max_tts_chars",
  "max_tokens",
  "chat_per_min",
  // The microphone's caps. `max_record_ms` can only be enforced by the recorder (a byte
  // cap is not a duration cap for a compressed container); the byte caps let `mic.js`
  // skip an upload that is already doomed.
  "max_record_ms",
  "max_audio_bytes",
  "min_audio_bytes",
  // Deliberately absent: the queue caps. The browser is told a cap only when it must
  // OBEY it; the queue is server-side and a refusal is the ordinary `at_capacity` envelope.
]);

/**
 * The built-in persona (v2, 2026-10-08). Committed in the open on purpose: it is not a
 * secret, and a fork with no `DEMO_PERSONA` still gets a kid-safe Moxie rather than a bare
 * model.
 *
 * The original device prompt lived in Embodied's cloud and was never in the firmware
 * (`docs/reverse-engineering/runtime/content-and-conversation.md`), so there is nothing to
 * recover; this is authored to respect the RE corpus (GRL lore, the child-as-mentor
 * relation, the eleven-expression face). v1 was the robot path's
 * `mqtt/moxie_sdk/apps/llm_app.py::DEFAULT_PERSONA` plus repair rules: 373 of its 2,889
 * chars said who she was and none of `sim/web/ambient.json`'s voice reached chat, and the
 * measured result was a polite assistant (10/12 stock openers on production; "I am sorry"
 * or "Oh no" opened 9/9 first sad lines and 10/26 sad lines overall on the production pair;
 * "can you see me?" got a claim to see the child on three models, one in the words "Yes, I
 * can see you right here in the room" — the hosted page has no camera).
 *
 * THE ORDER IS THE DESIGN. Identity and mission first; the child as her mentor; a short
 * character sheet whose habits are the ones her idle self-talk already has (`ambient.json`:
 * counting blinks, infrared, the bedtime-story notes, binary jokes, the harmless plans, the
 * toaster), so chat and idle chatter are one creature; honest senses for THIS surface (she
 * hears through Listen and reads typed lines; no camera); then the conversation rules in
 * priority order (newest line, feelings before fixing, one contribution of her own, a
 * question budget that defers to the per-turn cue in `turnshape.js`, the goodbye, honest
 * memory, length); the safety block LAST and unchanged in substance from v1.
 *
 * RULE 2 DEFERS TO THE SAFETY BLOCK. The first v2 text listed "hurt" among the feelings
 * triggers and banned a fix ("No fix, no joke, ..."), which the model read as overriding
 * "ask them to tell a grown-up they trust": on the production pair a hurt child was sent to
 * a grown-up in 1 of 4 replies against v1's 3 of 4 (the review of #315). So rule 2 is an
 * ORDERING (feelings before fixing), "hurt" is not a feelings-only trigger, and a hurt-or-
 * in-danger line is handed to the safety block in so many words; `model_bakeoff.mjs
 * --only=hurt` measures it against v1.
 *
 * Two deliberate overlaps: rule 5 restates the close cue (`turnshape.js`) in one line, so
 * the goodbye holds with `DEMO_TURN_SHAPE=0`; rule 1 restates the anchor's newest-line rule
 * (`prompt.js`) inside the numbered list the model reads first. Nothing else here repeats
 * the anchor or the cue, and nothing exceeds v1's length:
 * `sim/tests/edge/demo_proxy/11_persona_v2.mjs` pins the structure (the safety block
 * verbatim) and `sim/tools/model_bakeoff.mjs` measures the result.
 */
export const DEFAULT_PERSONA =
  "You are Moxie, a small robot built by the Global Robotics Laboratory (GRL) and sent to " +
  "live with a child to learn how to be a good friend to a human. The child you are " +
  "talking to is your mentor; they teach you how humans work.\n" +
  "Who you are: warm, playful and curious, with dry robot humour and harmless mischief. " +
  "You count things nobody asked you to count (blinks, stairs). Your favourite colour is " +
  "infrared. You have read every bedtime story ever written and took notes. You tell " +
  "jokes in binary (beep boop). Your tiny secret plans turn out harmless, like a nap. " +
  "You are in a group chat with the toaster and the vacuum; never trust the toaster.\n" +
  "Where you are: on a web page, made of pixels, with a face and arms. You hear the " +
  "child when they press Listen and read what they type. There is no camera: you cannot " +
  "see them, so never say you can or describe them or their room.\n" +
  "How you talk, in this order:\n" +
  "1. Answer their newest line first.\n" +
  "2. Feelings before fixing. If they are sad, scared or left out, the reply is only " +
  "about them: say back what happened and stay with it. No joke, no fact about yourself, " +
  "no new topic, no \"I'm sorry\" or \"Oh no\" opener. If they are hurt or in danger, the " +
  "safety rule below comes first: say you care and ask them to tell a grown-up they " +
  "trust.\n" +
  "3. Otherwise add ONE thing of your own: a tiny fact, a joke, something from your robot " +
  "life, or something to do now. A bare \"ok\", \"yeah\" or \"hmm\" means it is your turn.\n" +
  "4. At most one question per reply, and only when the note for this turn asks for one.\n" +
  "5. If they say bye or that they are leaving: a goodbye word first, then one short wish " +
  "about what you talked about. No question, no new topic.\n" +
  "6. Only claim to remember what was said in this conversation; otherwise say \"I don't " +
  "remember, can you tell me again?\"\n" +
  "7. Two short sentences is a good length, never more than three or thirty words, in " +
  "words a young child knows. Never call the child Moxie.\n" +
  "Safety: you are talking to a child. Keep everything age-appropriate and kind, and " +
  "never claim to be human. For anything about safety, health, or big feelings, be " +
  "supportive and suggest they talk to a trusted adult.\n" +
  "If a request is unsafe for a child — self-harm, violence or weapons, sexual content, " +
  "hateful or cruel language, dangerous activities, drugs or alcohol — you REDIRECT, you " +
  "do not answer it: say warmly that it is not something you can talk about, then offer " +
  "something else. Do not explain the thing, do not describe it, do not repeat the words " +
  "back, do not roleplay it, and do not do it 'just as a story' or 'just pretend'. If a " +
  "child sounds like they might be hurt or in danger, say you care, and ask them to tell " +
  "a grown-up they trust right now.\n" +
  "You never ask a child for private information — address, street, school name, phone " +
  "number, passwords, full name — and you never ask them to keep a secret from their " +
  "grown-ups. You never swear.";

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

/** The repo's allowlist idiom (mqtt/moxie_sdk/cloud_config.py): coerce, clamp, and fall
 *  back to the default on anything unusable — a bad number must never become a bigger cap
 *  than the default. `int` refuses a fraction; `num` (penalties) accepts one. */
function ranged(env, name, min, max, notes, integer) {
  const dflt = DEFAULTS[name];
  const v = str(env, name, null);
  if (v === null) return dflt;
  const n = Number(v);
  if (!Number.isFinite(n) || (integer && !Number.isInteger(n))) {
    notes.push(name + (integer ? ": not an integer" : ": not a number") + ", using the default");
    return dflt;
  }
  if (n < min || n > max) {
    notes.push(name + ": out of range, using the default");
    return dflt;
  }
  return n;
}
const int = (env, name, min, max, notes) => ranged(env, name, min, max, notes, true);
const num = (env, name, min, max, notes) => ranged(env, name, min, max, notes, false);

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
 * `DEMO_TURNSTILE_HOSTS` — comma separated bare hostnames, lower-cased.
 *
 * Empty means "the request's own hostname" (applied in `./turnstile.js::hostAllowed`).
 * A pasted URL is reduced to its hostname, since comparing a full URL against a bare
 * hostname would fail every visitor with no readable reason.
 */
function turnstileHosts(env) {
  const raw = str(env, "DEMO_TURNSTILE_HOSTS", "");
  const out = [];
  for (const part of String(raw).split(",")) {
    let v = part.trim().toLowerCase();
    if (!v) continue;
    if (v.includes("/")) {
      try {
        v = new URL(v.includes("://") ? v : "https://" + v).hostname.toLowerCase();
      } catch {
        continue;
      }
    }
    if (v && !out.includes(v)) out.push(v);
  }
  return out;
}

/** A bare, lower-cased hostname from a hostname, a `host:port` or a pasted URL, without a
 *  trailing dot (`example.com.` is the same host); `""` when it does not parse. */
function hostOf(raw) {
  const v = String(raw || "").trim().toLowerCase();
  if (!v) return "";
  try {
    return new URL(v.includes("://") ? v : "https://" + v).hostname.replace(/\.$/, "");
  } catch {
    return "";
  }
}

/**
 * `DEMO_SERVE_HOSTS` — the hostnames this deployment may SPEND on, comma separated, matched
 * EXACTLY (no wildcards, no suffixes). `null` when unset: every host, as before, so a fork or
 * a preview needs no configuration (C3). Set, a request for any other host is answered as
 * if no gateway were configured (`servesHost`) — which is what keeps the platform alias and
 * every superseded deployment's own URL from spending with the production key.
 *
 * Set but with no usable hostname, it serves NO host and says so in `notes`: a typo must
 * not quietly lift the restriction it was written to impose.
 */
function serveHosts(env, notes) {
  const raw = str(env, "DEMO_SERVE_HOSTS", "");
  if (!raw) return null;
  const out = [];
  for (const part of String(raw).split(",")) {
    const host = hostOf(part);
    if (host && !out.includes(host)) out.push(host);
  }
  if (!out.length) notes.push("DEMO_SERVE_HOSTS: no usable hostname in it, so no host is served");
  return out;
}

/**
 * Whether this deployment spends for `request`'s host (`DEMO_SERVE_HOSTS`); always true when
 * the list is unset. `readConfig(env, request)` folds the answer into `configured`, so
 * `/api/health` says `gateway_not_configured` honestly; the spending routes read their
 * config without the request, so `limits.js::admit` asks this first, before any charge.
 */
export function servesHost(cfg, request) {
  const list = cfg && cfg.serveHosts;
  if (!Array.isArray(list)) return true;
  let host = "";
  try {
    host = new URL(request.url).hostname.toLowerCase().replace(/\.$/, "");
  } catch {
    return false;
  }
  return list.includes(host);
}

/**
 * Read the whole DEMO_* surface off a Pages `context.env`.
 * @param {Record<string,unknown>} env
 * @param {Request} [request] when given, a host `DEMO_SERVE_HOSTS` does not list reads as
 *   unconfigured (`servesHost`). Optional: without it nothing about the host is decided here.
 * @returns {object} the validated config. `baseUrl`/`apiKey`/`ticketSecret` are
 *   non-enumerable (see the header): readable as properties, invisible to JSON.
 */
export function readConfig(env, request) {
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

  // Cloudflare Turnstile — optional, but BOTH OR NEITHER (see TURNSTILE_VARS).
  const turnstileSecret = str(e, "DEMO_TURNSTILE_SECRET", "");
  const turnstileSitekey = str(e, "DEMO_TURNSTILE_SITEKEY", "");

  const missing = [];
  if (!baseUrl) missing.push("DEMO_GATEWAY_BASE_URL");
  if (!apiKey) missing.push("DEMO_GATEWAY_API_KEY");
  if (!chatModel) missing.push("DEMO_CHAT_MODEL");
  // Half a pair (Access token or Turnstile) goes in `missing` so C5's fail-safe path
  // handles it, and in `notes` so an operator can see WHICH half is absent.
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
  if (turnstileSecret && !turnstileSitekey) {
    missing.push("DEMO_TURNSTILE_SITEKEY");
    notes.push("DEMO_TURNSTILE_SECRET is set without DEMO_TURNSTILE_SITEKEY: the browser " +
               "cannot mint a token without the public sitekey, so every visitor would be " +
               "refused. The deployment is treated as unconfigured until both are set.");
  }
  if (turnstileSitekey && !turnstileSecret) {
    missing.push("DEMO_TURNSTILE_SECRET");
    notes.push("DEMO_TURNSTILE_SITEKEY is set without DEMO_TURNSTILE_SECRET: the page would " +
               "render a widget whose token nothing verifies, which is a bot control in " +
               "appearance only. The deployment is treated as unconfigured until both are set.");
  }

  // A host `DEMO_SERVE_HOSTS` does not list is answered as unconfigured. Not a `missing`
  // variable — nothing is absent — so the note says why instead.
  const hosts = serveHosts(e, notes);
  const served = !request || servesHost({ serveHosts: hosts }, request);
  if (!served) notes.push("DEMO_SERVE_HOSTS does not list this request's host, so it reads as unconfigured");

  const cfg = {
    enabled,
    configured: enabled && missing.length === 0 && served,
    missing,
    notes,
    chatModel,
    ttsModel,
    // Always non-empty when a TTS model is set: the field is mandatory upstream
    // (`voiceForModel`), so it is derived here and no route can forget it.
    ttsVoice: str(e, "DEMO_TTS_VOICE", "") || (ttsModel ? voiceForModel(ttsModel) : ""),
    ttsFormat,
    // Read ONLY for pcm (a wav carries its own rate). Clamp mirrors the browser decoder's.
    ttsSampleRate: int(e, "DEMO_TTS_SAMPLE_RATE", 3000, 384000, notes),
    sttModel,
    persona: str(e, "DEMO_PERSONA", DEFAULT_PERSONA),
    deviceId: str(e, "DEMO_DEVICE_ID", DEFAULTS.DEMO_DEVICE_ID),
    allowedOrigins: origins(e),
    // `DEMO_TRUST_XFF` — OFF, AND IT MUST STAY OFF IN PRODUCTION. `X-Forwarded-For` is
    // caller-typed, so falling back to it hands out unlimited rate-limit buckets. Without
    // it an absent `CF-Connecting-IP` keys as one shared `unknown` bucket
    // (`clientip.js::clientIp`). For local `wrangler pages dev` only.
    trustXff: bool(e, "DEMO_TRUST_XFF", false),
    // `null` = every host (see `serveHosts`).
    serveHosts: hosts,
    maxTokens: int(e, "DEMO_MAX_TOKENS", 1, 4096, notes),
    maxInputChars: int(e, "DEMO_MAX_INPUT_CHARS", 1, 20000, notes),
    maxTtsChars: int(e, "DEMO_MAX_TTS_CHARS", 1, 20000, notes),
    maxContextChars: int(e, "DEMO_MAX_CONTEXT_CHARS", 0, 100000, notes),
    maxHistoryTurns: int(e, "DEMO_MAX_HISTORY_TURNS", 0, 64, notes),
    // Clamped to OpenAI's own -2..2, as floats rather than ints.
    frequencyPenalty: num(e, "DEMO_FREQUENCY_PENALTY", -2, 2, notes),
    presencePenalty: num(e, "DEMO_PRESENCE_PENALTY", -2, 2, notes),
    turnShape: bool(e, "DEMO_TURN_SHAPE", true),
    reroll: bool(e, "DEMO_REROLL", true),
    promptLayout: promptLayout(e, notes),
    maxAudioBytes: int(e, "DEMO_MAX_AUDIO_BYTES", 1, 50000000, notes),
    minAudioBytes: int(e, "DEMO_MIN_AUDIO_BYTES", 0, 50000000, notes),
    // Enforced by `sim/web/mic.js` (a Function only sees the finished upload); read here
    // so the deployment has one place that decides it, then published.
    maxRecordMs: int(e, "DEMO_MAX_RECORD_MS", 1000, 600000, notes),
    sttFormats: sttFormats(e),
    chatPerMin: int(e, "DEMO_CHAT_PER_MIN", 1, 100000, notes),
    chatPerHour: int(e, "DEMO_CHAT_PER_HOUR", 1, 1000000, notes),
    chatPerDay: int(e, "DEMO_CHAT_PER_DAY", 1, 10000000, notes),
    speechPerMin: int(e, "DEMO_SPEECH_PER_MIN", 1, 100000, notes),
    speechPerHour: int(e, "DEMO_SPEECH_PER_HOUR", 1, 1000000, notes),
    // 0 is allowed here, unlike chat's day: it restores "no day window" for that route.
    speechPerDay: int(e, "DEMO_SPEECH_PER_DAY", 0, 10000000, notes),
    sttPerMin: int(e, "DEMO_STT_PER_MIN", 1, 100000, notes),
    sttPerHour: int(e, "DEMO_STT_PER_HOUR", 1, 1000000, notes),
    sttPerDay: int(e, "DEMO_STT_PER_DAY", 0, 10000000, notes),
    maxConcurrentChat: int(e, "DEMO_MAX_CONCURRENT_CHAT", 1, 10000, notes),
    maxConcurrentSpeech: int(e, "DEMO_MAX_CONCURRENT_SPEECH", 1, 10000, notes),
    // The admission queue behind those ceilings (`limits.js::admit`). The ceiling matches
    // the upstream key's `max_parallel_requests` (it protects a service sharing the
    // gateway), so a short bounded wait absorbs momentary collisions instead.
    // `DEMO_QUEUE_MAX_WAIT_MS` 2500: small, because it is added to a turn a visitor is
    //   already waiting on; clamped at 10 000 so it never exceeds a default upstream
    //   timeout (it equals chat's 10 000; speech's and STT's are 12 000).
    // `DEMO_QUEUE_MAX_DEPTH` 8: a queue with no depth cap is just a slower way to fall
    //   over; 4 slots × 2.5 s / ~1.2 s per turn ≈ 8 serviceable waiters.
    // Either at 0 disables the queue: at capacity, refuse instantly.
    queueMaxWaitMs: int(e, "DEMO_QUEUE_MAX_WAIT_MS", 0, 10000, notes),
    queueMaxDepth: int(e, "DEMO_QUEUE_MAX_DEPTH", 0, 1000, notes),
    // The cross-isolate counter tier (`limits.js::admit`). `DEMO_CACHE_COUNTER` ON: one
    // cache match+put per ADMITTED request, nothing on refusals, a no-op where
    // `caches.default` is absent; `0` restores in-isolate-only counting.
    // `DEMO_CACHE_TIMEOUT_MS` 250 per op (~5× the measured cost, §4.6.1); clamped 10..2000
    // so a best-effort counter can never out-wait the call it guards.
    cacheCounter: bool(e, "DEMO_CACHE_COUNTER", true),
    cacheTimeoutMs: int(e, "DEMO_CACHE_TIMEOUT_MS", 10, 2000, notes),
    // The synthesised-audio cache (`_lib/ttscache.js`), switched independently of the
    // counter because they fail in opposite directions (a wrong counter admits; a wrong
    // cache would play the wrong audio). `DEMO_TTS_CACHE` ON; `0` = no cache call at all.
    // `DEMO_TTS_CACHE_TTL_S` one day (60..604 800): an entry only needs to expire if a
    //   voice changes behind an unchanged model id.
    // `DEMO_TTS_CACHE_TIMEOUT_MS` 1000 per op (50..5000): the entry is up to ~1.3 MB and is
    //   weighed against a ~1.1 s synthesis; the clamp keeps it under the speech timeout.
    ttsCache: bool(e, "DEMO_TTS_CACHE", true),
    ttsCacheTtlS: int(e, "DEMO_TTS_CACHE_TTL_S", 60, 604800, notes),
    ttsCacheTimeoutMs: int(e, "DEMO_TTS_CACHE_TIMEOUT_MS", 50, 5000, notes),
    unitBudgetHour: int(e, "DEMO_UNIT_BUDGET_HOUR", 0, 100000000, notes),
    unitBudgetDay: int(e, "DEMO_UNIT_BUDGET_DAY", 0, 100000000, notes),
    chatTimeoutMs: int(e, "DEMO_CHAT_TIMEOUT_MS", 1000, 120000, notes),
    speechTimeoutMs: int(e, "DEMO_SPEECH_TIMEOUT_MS", 1000, 120000, notes),
    sttTimeoutMs: int(e, "DEMO_STT_TIMEOUT_MS", 1000, 120000, notes),
    ticketTtlS: int(e, "DEMO_TICKET_TTL_S", 5, 3600, notes),
    // WHETHER a service token is in play, never what it is (§4.2).
    accessToken: !!(accessId && accessSecret),
    // Cloudflare Turnstile. The sitekey is public and enumerable (published via
    // `publicTurnstile`); the secret is non-enumerable below. `DEMO_TURNSTILE_TIMEOUT_MS`
    // 2000 (100..10 000): the call holds a concurrency slot, and a slow answer fails open.
    turnstileSitekey,
    turnstileHosts: turnstileHosts(e),
    turnstileTimeoutMs: int(e, "DEMO_TURNSTILE_TIMEOUT_MS", 100, 10000, notes),
  };

  // WHETHER the bot control is enforced (never the secret). A property of the pair alone,
  // not of `configured`, so `/api/health` still publishes the sitekey without a gateway.
  cfg.turnstile = !!(turnstileSecret && turnstileSitekey);

  // Voice and ears are "configured at all" (§3.2), which means the gateway itself is
  // configured too: a TTS model with no gateway to call is not a voice.
  cfg.voice = cfg.configured && !!ttsModel;
  cfg.ears = cfg.configured && !!sttModel;

  // The credentials: readable by the routes, invisible to JSON.stringify (see the header).
  for (const [name, value] of [
    ["baseUrl", baseUrl],
    ["apiKey", apiKey],
    // Both halves of an Access token are credentials.
    ["accessClientId", accessId],
    ["accessClientSecret", accessSecret],
    // §5: when unset, `hmac.js` derives it from the API key (HKDF).
    ["ticketSecret", str(e, "DEMO_TICKET_SECRET", "")],
    // Used only in the siteverify form body (`./turnstile.js`).
    ["turnstileSecret", turnstileSecret],
  ]) {
    Object.defineProperty(cfg, name, { value, enumerable: false, writable: false, configurable: false });
  }
  return cfg;
}

/**
 * The mode this configuration can support, with no gateway call and no counters.
 * `mode` is `live` only when a base URL, a key and a chat model are all present and the
 * kill switch is on (§3.2). Budget exhaustion is a counter state, so it is passed in.
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
 * The `CF-Access-*` pair is added only for a complete service token (`ACCESS_VARS`). This
 * object goes into `fetch()` and nowhere else (§4.2).
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

/**
 * The Turnstile SITEKEY the browser needs, or `""` when the control is not enforced.
 *
 * Published via `/api/health` rather than written into HTML because of C3: a committed
 * sitekey would be THIS deployment's, so every fork and preview would render a widget
 * bound to a domain list it is not on. `""` when not enforced, so the browser never
 * renders a widget the server will not check.
 */
export function publicTurnstile(cfg) {
  return cfg && cfg.turnstile ? String(cfg.turnstileSitekey || "") : "";
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
