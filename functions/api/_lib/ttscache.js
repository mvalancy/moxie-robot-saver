/* functions/api/_lib/ttscache.js — the synthesised-audio cache behind `POST /api/speech`.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §4.8 (this tier), §4.6.1 (the Cache API
 * measurement that cleared it to be built), §3.2 (the route contract it must not change),
 * §2.2 (the wire the audio ends up on), §5 (the three variables).
 *
 * ============================================================================
 * WHAT THIS IS.
 *
 * `/api/speech` synthesises the same sentence from scratch every time it is asked for it.
 * Synthesis is the most expensive thing this deployment does — measured against the real
 * gateway on 2026-09-02, a 30-character line cost **131 348 B and 1 091 ms** — and the
 * audio for a given (voice, text) is the same audio every time. So the second visitor to
 * hear a line we have already made should not be paying to make it again.
 *
 * This module keeps that audio in `caches.default`, keyed on everything that changes it,
 * and hands it back on a hit. On a hit `/api/speech` makes **zero upstream calls**.
 *
 * WHAT THIS IS NOT, SAID BEFORE THE FLATTERING HALF (the same discipline `./limits.js`'s
 * counter tier is written under, and for the same reason — the previous version of that
 * file's documentation said only the flattering half and someone sized a risk wrongly):
 *
 *   1. **IT IS PER-COLO.** Cloudflare's cache "does not replicate outside of the
 *      originating data center" (§4.6.1). A colo that has not heard a line before pays for
 *      it, every time, exactly as today. There is no global hit rate here and nothing may
 *      claim one.
 *   2. **A COLD COLO STILL PAYS.** The first visitor to a colo, the first visitor after
 *      `DEMO_TTS_CACHE_TTL_S`, and the first visitor after a configuration change all pay
 *      full price. The cache saves repeats; it does not save first plays.
 *   3. **THE HIT RATE IS NOT MEASURED AND CANNOT BE MEASURED FROM A PREVIEW.** A branch
 *      preview carries no `DEMO_*` secrets, so `/api/speech` answers
 *      `gateway_not_configured` and returns long before this module is reached — the same
 *      limitation §4.6.1 recorded for the counter tier. What is bounded below is the
 *      COST, not the saving: one extra `match` on a miss, which is a few tens of
 *      milliseconds against a ~1 100 ms synthesis.
 *   4. **AND THE ONE THING THE PREMISE GOT WRONG.** The demo's scripted copy — the
 *      fallback lines, the degraded announcement, the ambient quips — **never reaches this
 *      route at all** and therefore is never cached. `/api/speech` has no text field: the
 *      text arrives inside a ticket, and `chat.js`:150 is the only place a ticket is ever
 *      minted, from a live gateway reply. A hard-blocked turn deliberately mints none
 *      (`chat.js::blocked`), and every scripted line is spoken from a clip or the browser
 *      voice. So what this tier can actually deduplicate is **repeated gateway replies**,
 *      which at `TEMPERATURE = 0.8` repeat by chance rather than by construction.
 * ============================================================================
 *
 * THE RULES, WHICH ARE THE WHOLE OF THE DESIGN.
 *
 * **A MISS OR AN ERROR COSTS NOTHING BUT A SYNTHESIS.** Every failure mode — a miss, a
 * stale entry, a `match` that throws, rejects or hangs for ever, a `put` that does any of
 * those, no `caches` global at all, a body that will not parse, an entry that is not the
 * audio we stored — falls through to exactly the call the route made before this module
 * existed. Nothing here can turn into a refusal, and nothing here can throw into the
 * route. `sim/test_demo_proxy.mjs` §16 asserts each one by name, through the whole route.
 *
 * **NOTHING BUT A SUCCESSFUL SYNTHESIS IS EVER STORED.** `writeCachedAudio` is called on
 * exactly one path: after `callGateway` returned `ok`, after `pcmFromAudio` decoded the
 * body into non-empty 16-bit PCM. An upstream 500, a JSON error body, an Access login
 * page, a timeout, a refusal — none of them reach it, and none of them has a cache entry
 * to poison a later visitor with.
 *
 * **THE STORED BODY IS A WAV, NOT RAW PCM PLUS HEADERS.** The rate and the channel count
 * are properties of the audio, so they belong inside it: a 44-byte RIFF header makes the
 * entry self-describing, survives any header a cache might choose not to keep, and — the
 * part that matters — means the hit path decodes with `pcmFromAudio`, the *same* function
 * the miss path decodes the gateway's answer with. Byte-identity between the two paths is
 * then a property of one decoder, not a claim about two.
 */
import { CACHE_ERROR, CACHE_TIMEOUT, withDeadline } from "./limits.js";
import { TTS_CACHE_INFO, keyedTag } from "./hmac.js";
import { pcmFromAudio, writeWav } from "./wav.js";

/** The path prefix of this tier's entries, on the deployment's OWN origin — the same shape
 *  `./limits.js` uses for its counter, and not a Function route. */
const CACHE_PATH = "/__moxie/tts/";

/**
 * THE FULL 256 BITS OF THE HMAC, and not the 96 the counter tier truncates to.
 *
 * The two truncations are not the same decision. A collision in the counter's tag merges
 * two visitors into one rate-limit bucket, which throttles them together — the
 * conservative direction. A collision HERE would hand one child the audio of somebody
 * else's sentence, in the wrong words. There is no conservative direction for that, so the
 * digest is not truncated at all.
 */
const DIGEST_HEX = 64;

/**
 * What the tier RECORDED, for tests and for the report — never for a decision.
 *
 * `ops` counts real Cache API round trips, which is the latency budget; `hit`/`miss` are
 * the two ordinary outcomes; and `stale` + `corrupt` + `errors` + `timeouts` are every way
 * this tier fell open to a synthesis it could in principle have avoided.
 */
const stats = {
  checked: 0,
  ops: 0,
  hit: 0,
  miss: 0,
  stale: 0,
  corrupt: 0,
  wrote: 0,
  errors: 0,
  timeouts: 0,
};

/** Tests only. */
export function __ttsCacheState() {
  return { ...stats };
}

/** Tests only. */
export function __resetTtsCache() {
  for (const k of Object.keys(stats)) stats[k] = 0;
}

/**
 * The store this request should use: `caches.default` on the real runtime, or `null` —
 * which is the "behave exactly as before" path and is what bare `node` always gets.
 *
 * NOT `async`, and it does the switch test FIRST: with `DEMO_TTS_CACHE=0` the route never
 * touches the `caches` global, never derives a key, and never awaits anything new.
 */
export function ttsStore(cfg) {
  if (!cfg || !cfg.ttsCache) return null;
  try {
    return (typeof caches !== "undefined" && caches && caches.default) || null;
  } catch {
    return null; // a runtime that throws on the global is a runtime without a cache
  }
}

/**
 * Length-prefix one key component, so the join is injective.
 *
 * `"a" + "bc"` and `"ab" + "c"` are the same string and would be the same cache key —
 * which is how a cache starts serving one voice's audio under another's name. With the
 * length in front they cannot collide: `1:a2:bc` is not `2:ab1:c`. `String#length` is
 * UTF-16 code units, which is fine because the same measure is used on both sides.
 */
function lp(s) {
  const v = String(s === undefined || s === null ? "" : s);
  return v.length + ":" + v;
}

/**
 * THE CACHE KEY, and every component of it with the reason it is there.
 *
 * A key that leaves out something which changes the audio is worse than no cache at all:
 * it serves one child a line in somebody else's voice, and it does so *reliably*, for as
 * long as the entry lives. So the rule is not "key on the text" — it is "key on every
 * input to the synthesis, plus the text".
 *
 *   * `"v1"` — the ENTRY FORMAT. If what is stored ever stops being a 16-bit RIFF/WAVE,
 *     old entries must not be read as if it were. Bumping this abandons them all.
 *   * `cfg.baseUrl` — the GATEWAY. The same model *name* on a different gateway is a
 *     different Piper build and a different voice. It never leaves this function: it is
 *     an input to an HMAC, and the digest is what appears in the URL.
 *   * `cfg.ttsModel` — the MODEL, which on our gateway *is* the voice: the voice is
 *     encoded in the model id (`_lib/env.js::voiceForModel`, `mqtt/config.py`:91-92).
 *     This is the single most important component and the one whose absence the mutation
 *     test in §16 exists to catch.
 *   * `cfg.ttsVoice` — the `voice` FIELD actually sent on the wire. Usually derived from
 *     the model, but `DEMO_TTS_VOICE` overrides it independently, and a gateway that
 *     honours the field would then speak differently under an unchanged model id.
 *   * `cfg.ttsFormat` — `wav` or `pcm`. It is the `response_format` on the request, and it
 *     changes how the answer is decoded (`_lib/wav.js`), so it changes the samples.
 *   * `cfg.ttsSampleRate` — the RATE. Under `DEMO_TTS_FORMAT=pcm` there is no header and
 *     this configured number *is* the playback rate; the same bytes at 16 kHz and at
 *     22.05 kHz are two different voices, one of them a chipmunk.
 *   * the exact TEXT — the point of the whole thing. Exact: not trimmed, not lowercased,
 *     not normalised. Two strings that differ by a comma are two different recordings, and
 *     any normalisation here is a way to serve the wrong words.
 *
 * NOT included, deliberately: `cfg.persona` and `DEMO_MAX_TTS_CHARS` (they shape what the
 * text IS, and the text is already here, final and truncated), the ticket's `eventId` and
 * `chunkNum` (they identify the turn, not the audio, and including them would make every
 * key unique and the cache useless), and the visitor (the audio is not personal — it is a
 * line we wrote).
 *
 * Keyed rather than plain: `keyedTag` HMACs under HKDF from the deployment's own secret
 * material, so a Cache API entry sitting under a URL on our origin cannot be enumerated by
 * anyone guessing sentences (`_lib/hmac.js::keyedTag` carries the full argument). It also
 * means rotating `DEMO_GATEWAY_API_KEY` or `DEMO_TICKET_SECRET` rotates every key here,
 * which is a free and correct invalidation.
 *
 * @returns {Promise<string>} the key URL, or `""` if one could not be derived — which the
 *   caller treats as "no cache", i.e. today's behaviour.
 */
export async function ttsCacheKey(cfg, request, text) {
  try {
    const canon =
      lp("v1") +
      lp(cfg.baseUrl) +
      lp(cfg.ttsModel) +
      lp(cfg.ttsVoice) +
      lp(cfg.ttsFormat) +
      lp(cfg.ttsSampleRate) +
      lp(text);
    const digest = await keyedTag(cfg, TTS_CACHE_INFO, canon, DIGEST_HEX);
    return new URL(request.url).origin + CACHE_PATH + digest;
  } catch {
    stats.errors += 1;
    return ""; // FAIL OPEN: no key, no cache, one synthesis — exactly as before
  }
}

/**
 * Read the audio for `key`, or `null` for "synthesise it".
 *
 * NEVER THROWS AND NEVER REJECTS. Every outcome that is not "here is decodable audio we
 * stored" answers `null`, and `null` costs the caller precisely one synthesis — which is
 * what it would have paid anyway. The deadline is on BOTH awaits: `match()` resolves when
 * the headers are there, and reading the body is a second wait that moves up to ~1.3 MB
 * (`speech.js`'s header does that arithmetic), so a wedged read must not hold the visitor's
 * turn open any more than a wedged lookup may.
 *
 * ONE CACHE OP. A hit is one `match` and no `put`; a miss is one `match` and the caller's
 * `put`. §4.6.1 row h measured three ops at <=44 ms and this tier never spends more than
 * two.
 */
export async function readCachedAudio(store, cfg, key) {
  if (!store || !key) return null;
  stats.checked += 1;
  try {
    const hit = await withDeadline(cfg.ttsCacheTimeoutMs, () => store.match(key));
    if (hit === CACHE_TIMEOUT) {
      stats.timeouts += 1;
      return null;
    }
    if (hit === CACHE_ERROR) {
      stats.errors += 1;
      return null;
    }
    stats.ops += 1;
    if (!hit) {
      stats.miss += 1;
      return null;
    }

    // A hit past its own `max-age`. A real cache would not serve one; a fake in a test
    // will, and the required answer is "treat it as absent" — the same rule
    // `./limits.js::readCount` applies to the counter tier.
    const age = Number(hit.headers.get("Age"));
    const cc = /max-age\s*=\s*(\d+)/i.exec(hit.headers.get("Cache-Control") || "");
    const maxAge = cc ? Number(cc[1]) : 0;
    if (Number.isFinite(age) && maxAge > 0 && age >= maxAge) {
      stats.stale += 1;
      return null;
    }

    const buf = await withDeadline(cfg.ttsCacheTimeoutMs, () => hit.arrayBuffer());
    if (buf === CACHE_TIMEOUT) {
      stats.timeouts += 1;
      return null;
    }
    if (buf === CACHE_ERROR) {
      stats.errors += 1;
      return null;
    }

    // THE SAME DECODER THE MISS PATH USES, told the same thing about what it is looking
    // at. An entry that is not a readable 16-bit WAV — truncated, overwritten, written by
    // an older format, or simply not ours — is `corrupt` and is not audio a child hears.
    const out = pcmFromAudio(new Uint8Array(buf), { format: "wav", sampleRate: cfg.ttsSampleRate, channels: 1 });
    if (!out.pcm.length) {
      stats.corrupt += 1;
      return null;
    }
    stats.hit += 1;
    return { pcm: out.pcm, sampleRate: out.sampleRate, channels: out.channels };
  } catch {
    // `pcmFromAudio` throwing on a body that is not ours, a `hit` with no `headers`, a
    // store whose `match` is not a function — the outer seatbelt, in the idiom of
    // `./limits.js::sharedThenGrant`. It may never cost a visitor their turn.
    stats.corrupt += 1;
    return null;
  }
}

/**
 * Store one successful synthesis. Best effort, and a failure is not an error the visitor
 * ever learns about: they already have their audio.
 *
 * AWAITED RATHER THAN HANDED TO `waitUntil`, on purpose. `context.waitUntil` exists on
 * Pages Functions and would take this off the visitor's clock — but it would also put the
 * one write in this tier on a code path no test can observe finishing, and the measured
 * cost of a cache op on this platform is tens of milliseconds against a synthesis that
 * just cost ~1 100 ms. The deadline bounds the worst case; if a colo is ever seen to make
 * a `put` expensive, this is the line to move, and the fail-open rules do not change.
 */
export async function writeCachedAudio(store, cfg, key, audio) {
  if (!store || !key || !audio || !audio.pcm || !audio.pcm.length) return;
  try {
    const body = writeWav(audio.pcm, {
      sampleRate: audio.sampleRate,
      channels: audio.channels,
      bitsPerSample: 16,
    });
    const wrote = await withDeadline(cfg.ttsCacheTimeoutMs, () =>
      store.put(
        key,
        new Response(body, {
          headers: {
            "Content-Type": "audio/wav",
            // One TTL, carried on the entry itself, so `readCachedAudio`'s `Age` test and
            // the cache's own eviction agree about when this stops being current.
            "Cache-Control": "max-age=" + cfg.ttsCacheTtlS,
          },
        }),
      ),
    );
    if (wrote === CACHE_TIMEOUT) stats.timeouts += 1;
    else if (wrote === CACHE_ERROR) stats.errors += 1;
    else {
      stats.ops += 1;
      stats.wrote += 1;
    }
  } catch {
    stats.errors += 1; // FAIL OPEN: the visitor keeps the audio they already have
  }
}
