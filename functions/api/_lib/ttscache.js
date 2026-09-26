/* functions/api/_lib/ttscache.js — the synthesised-audio cache behind `POST /api/speech`.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §4.8 (this tier), §4.6.1 (the Cache API
 * measurement), §3.2, §5.
 *
 * Synthesis is the most expensive thing this deployment does (~131 KB and ~1.1 s for a
 * 30-character line) and the audio for one (voice, text) never changes, so it is kept in
 * `caches.default`. A HIT MAKES ZERO UPSTREAM CALLS.
 *
 * LIMITS, stated plainly:
 *   1. PER-COLO — Cloudflare's cache does not replicate; there is no global hit rate.
 *   2. A COLD COLO (or first play after the TTL or a config change) PAYS full price.
 *   3. THE HIT RATE IS UNMEASURED; a secretless preview never reaches this module. The
 *      bounded quantity is the COST: one extra `match` on a miss.
 *   4. Scripted copy never reaches `/api/speech` (tickets are minted only from live gateway
 *      replies), so what this deduplicates is repeated gateway replies.
 *
 * RULES:
 *   * A MISS OR AN ERROR COSTS NOTHING BUT A SYNTHESIS. Every failure (miss, stale, a
 *     throwing/hanging `match` or `put`, no `caches`, a corrupt entry) falls through to the
 *     ordinary upstream call; nothing here refuses or throws into the route
 *     (`sim/test_demo_proxy.mjs` §16).
 *   * ONLY A SUCCESSFUL SYNTHESIS IS STORED — after `pcmFromAudio` decoded non-empty PCM.
 *   * THE STORED BODY IS A WAV, so the entry is self-describing and a hit decodes through
 *     the same `pcmFromAudio` as a miss.
 */
import { CACHE_ERROR, CACHE_TIMEOUT, withDeadline } from "./limits.js";
import { TTS_CACHE_INFO, keyedTag } from "./hmac.js";
import { pcmFromAudio, writeWav } from "./wav.js";

/** Entry path prefix on the deployment's own origin (not a Function route). */
const CACHE_PATH = "/__moxie/tts/";

/**
 * The FULL 256-bit HMAC, not the counter tier's truncation: a counter collision merely
 * throttles two visitors together, but a collision here would play one child another's
 * sentence.
 */
const DIGEST_HEX = 64;

/**
 * What the tier RECORDED, for tests — never for a decision. `ops` counts real Cache API
 * round trips; `stale`/`corrupt`/`errors`/`timeouts` are every way it fell open.
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
 * The store to use: `caches.default`, or `null` ("behave as if there were no cache", what
 * bare node gets). Not async, and the switch is tested first: with `DEMO_TTS_CACHE=0`
 * nothing touches `caches` or derives a key.
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
 * Length-prefix one key component so the join is injective (`1:a2:bc` ≠ `2:ab1:c`);
 * otherwise one voice's audio could be served under another's name.
 */
function lp(s) {
  const v = String(s === undefined || s === null ? "" : s);
  return v.length + ":" + v;
}

/**
 * THE CACHE KEY: every input to the synthesis plus the text. A key that omits one serves
 * a child a line in somebody else's voice, reliably.
 *
 *   * `"v1"` — the entry format; bump to abandon old entries.
 *   * `cfg.baseUrl` — the gateway (same model name elsewhere = a different voice). Only an
 *     HMAC input; never appears in the URL.
 *   * `cfg.ttsModel` — on our gateway the model id IS the voice.
 *   * `cfg.ttsVoice` — `DEMO_TTS_VOICE` can override the wire field independently.
 *   * `cfg.ttsFormat` — changes the request and the decode.
 *   * `cfg.ttsSampleRate` — under `pcm` it IS the playback rate.
 *   * the exact TEXT — not trimmed, lowercased or normalised.
 * Deliberately NOT: the persona and char cap (already reflected in the final text), the
 * ticket's event id/chunk (would make every key unique), the visitor (not personal).
 *
 * Keyed (`hmac.js::keyedTag`), so nobody can enumerate entries by guessing sentences, and
 * rotating the key or ticket secret invalidates every entry for free.
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

/** Record a `withDeadline` failure; true when `r` is one. */
function failed(r) {
  if (r === CACHE_TIMEOUT) {
    stats.timeouts += 1;
    return true;
  }
  if (r === CACHE_ERROR) {
    stats.errors += 1;
    return true;
  }
  return false;
}

/**
 * Read the audio for `key`, or `null` for "synthesise it".
 *
 * NEVER THROWS AND NEVER REJECTS: anything but decodable audio we stored is `null` (one
 * synthesis). The deadline covers BOTH the `match` and the body read (up to ~1.3 MB). A
 * hit is one cache op; a miss is one plus the caller's `put`.
 */
export async function readCachedAudio(store, cfg, key) {
  if (!store || !key) return null;
  stats.checked += 1;
  try {
    const hit = await withDeadline(cfg.ttsCacheTimeoutMs, () => store.match(key));
    if (failed(hit)) return null;
    stats.ops += 1;
    if (!hit) {
      stats.miss += 1;
      return null;
    }

    // A hit past its own `max-age` is treated as absent (same rule as the counter tier).
    const age = Number(hit.headers.get("Age"));
    const cc = /max-age\s*=\s*(\d+)/i.exec(hit.headers.get("Cache-Control") || "");
    const maxAge = cc ? Number(cc[1]) : 0;
    if (Number.isFinite(age) && maxAge > 0 && age >= maxAge) {
      stats.stale += 1;
      return null;
    }

    const buf = await withDeadline(cfg.ttsCacheTimeoutMs, () => hit.arrayBuffer());
    if (failed(buf)) return null;

    // The miss path's decoder. An entry that is not a readable 16-bit WAV is `corrupt`.
    const out = pcmFromAudio(new Uint8Array(buf), { format: "wav", sampleRate: cfg.ttsSampleRate, channels: 1 });
    if (!out.pcm.length) {
      stats.corrupt += 1;
      return null;
    }
    stats.hit += 1;
    return { pcm: out.pcm, sampleRate: out.sampleRate, channels: out.channels };
  } catch {
    // The outer seatbelt (a throwing decode, a malformed hit or store): never costs a turn.
    stats.corrupt += 1;
    return null;
  }
}

/**
 * Store one successful synthesis. Best effort; the visitor already has their audio.
 * AWAITED rather than `waitUntil`, so tests can observe the write finish; the deadline
 * bounds it at a fraction of the synthesis it follows.
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
            // One TTL, on the entry, so our `Age` test and eviction agree.
            "Cache-Control": "max-age=" + cfg.ttsCacheTtlS,
          },
        }),
      ),
    );
    if (!failed(wrote)) {
      stats.ops += 1;
      stats.wrote += 1;
    }
  } catch {
    stats.errors += 1; // FAIL OPEN: the visitor keeps the audio they already have
  }
}
