/* test_demo_proxy — §16: the synthesised-audio cache. Run via the entry file, never alone. */
import {
  FULL, KEY, ORIGIN, P, call, chat, deep, eq,
  fresh, limits, ok, pcmBytes, req, sent, speech, ttscache,
  wav, wire, wire2,
} from "./harness.mjs";

/* =========================================================================== *
 * 16. THE SYNTHESISED-AUDIO CACHE — `/api/speech` stops paying twice for a line
 * =========================================================================== *
 * Spec: live-sim-demo.md §4.8, §4.6.1, §3.2, §4.1, §4.5.
 *
 * Synthesis is the most expensive thing this deployment does, and the audio for a given
 * (gateway, model, voice, format, rate, exact text) never changes, so `_lib/ttscache.js`
 * keeps it in `caches.default`. Asserted:
 *   1. A HIT IS BYTE-IDENTICAL TO A MISS, same bytes, rate and channels (16b).
 *   2. A HIT COSTS ZERO UPSTREAM CALLS, on the intercepted request log (16b).
 *   3. EVERY FAILURE COSTS EXACTLY ONE SYNTHESIS — each failure shape of `match`, body
 *      read, decode and `put`, and no `caches` at all — through the whole route (16e).
 *   4. NOTHING BUT A SUCCESSFUL SYNTHESIS IS STORED (16c).
 *   5. THE CAPS DECIDE FIRST: every refusal is made without touching the cache (16f).
 * Not asserted, because it is not true: any hit rate. The cache is per-colo and a cold
 * colo pays full price.
 */
{
  /** A fake `caches.default` that stores BYTES, with §15's three failure shapes plus the
   *  audio-only ones: a body read that fails those three ways, and a corrupt entry. */
  function audioCache(opts) {
    const o = opts || {};
    const store = new Map();
    const log = { match: 0, put: 0, keys: [], calls: [] };
    const hang = () => new Promise(() => {});
    const body = (bytes, maxAge, ageS) => {
      const h = { "Content-Type": "audio/wav", "Cache-Control": "max-age=" + maxAge };
      if (ageS !== undefined) h.Age = String(ageS);
      const res = new Response(bytes, { headers: h });
      if (o.readThrowsSync) res.arrayBuffer = () => { throw new Error("body read threw synchronously"); };
      if (o.readRejects) res.arrayBuffer = () => Promise.reject(new Error("body read rejected"));
      if (o.readHangs) res.arrayBuffer = () => hang();
      return res;
    };
    return {
      log,
      store,
      /** Pre-load an entry as another isolate in this colo would have written it. */
      seed(key, bytes, maxAge, ageS) {
        store.set(String(key), { bytes, maxAge: maxAge === undefined ? 86400 : maxAge, ageS });
        return this;
      },
      bytes(key) {
        const e = store.get(String(key));
        return e ? e.bytes : null;
      },
      match(key) {
        log.match += 1;
        log.keys.push(String(key));
        log.calls.push({ op: "match", key: String(key) });
        if (o.matchThrowsSync) throw new Error("match threw synchronously");
        if (o.matchHangs) return hang();
        if (o.matchRejects) return Promise.reject(new Error("match rejected"));
        if (o.matchReturnsJunk) return Promise.resolve({ notAResponse: true });
        const e = store.get(String(key));
        if (!e) return Promise.resolve(undefined);
        return Promise.resolve(body(o.bodyOverride === undefined ? e.bytes : o.bodyOverride, e.maxAge, e.ageS));
      },
      put(key, res) {
        log.put += 1;
        log.calls.push({ op: "put", key: String(key) });
        if (o.putThrowsSync) throw new Error("put threw synchronously");
        if (o.putHangs) return hang();
        if (o.putRejects) return Promise.reject(new Error("put rejected"));
        return (async () => {
          const buf = new Uint8Array(await res.arrayBuffer());
          const cc = /max-age=(\d+)/.exec(res.headers.get("Cache-Control") || "");
          store.set(String(key), { bytes: buf, maxAge: cc ? Number(cc[1]) : 0 });
        })();
      },
    };
  }

  /* ONE STORE, TWO TIERS. `caches.default` is shared: `_lib/limits.js`'s per-IP counter
   * writes `/__moxie/rl/...` on every ADMITTED turn and this tier writes `/__moxie/tts/...`.
   * They must not be counted together or an assertion about one is really about both — so
   * every count below is filtered by prefix, and 16g asserts the two prefixes coexist. */
  const TTS_PREFIX = "/__moxie/tts/";
  const ttsCalls = (c, op) => c.log.calls.filter((x) => x.op === op && x.key.includes(TTS_PREFIX)).length;
  const ttsKeys = (c) => c.log.calls.filter((x) => x.op === "match" && x.key.includes(TTS_PREFIX)).map((x) => x.key);
  const ttsEntries = (c) => [...c.store.keys()].filter((k) => String(k).includes(TTS_PREFIX));
  const ttsOps = (c) => ttsCalls(c, "match") + ttsCalls(c, "put");

  const cstats = () => ttscache.__ttsCacheState();
  /** Install a fake as the GLOBAL `caches.default` — the branch production takes — run the
   *  body, and always put the global back. Everything in this section drives the real
   *  route through the real lookup; nothing injects a store behind the route's back. */
  async function withCache(store, fn) {
    globalThis.caches = { default: store };
    try {
      return await fn();
    } finally {
      delete globalThis.caches;
    }
  }
  /** How many times the gateway was asked to SYNTHESISE, from the intercepted request log
   *  rather than from a counter — the same evidence the rest of this file uses. */
  const synths = () => sent.filter((s) => String(s.url).endsWith("/audio/speech")).length;
  /** One whole turn: a chat reply with a fixed line, then that line spoken. A THROW IS
   *  RECORDED, NOT PROPAGATED: on Cloudflare an escaping exception is a platform 500 page,
   *  the worst outcome this tier could produce, so "did not throw" is an assertion. */
  async function turn(line, env, text) {
    P.plan = { chat: { content: line }, speech: P.plan.speech };
    const c = await call(chat, "/api/chat", { text: text || "say it" }, null, env);
    let s;
    try {
      s = await call(speech, "/api/speech", { ticket: c.body.speech[0].ticket }, null, env);
    } catch (err) {
      ok(false, `POST /api/speech THREW instead of answering: ${err && err.message}`);
      s = { res: new Response("{}", { status: 599 }), body: { messages: [] } };
    }
    const msgs = (s.body && s.body.messages) || [];
    return { chat: c, speech: s, payload: msgs[0] ? JSON.parse(msgs[0].payload) : null };
  }
  const audioOf = (t) => (t.payload && t.payload.audio ? t.payload.audio : {});
  const bytesOf = (t) => Buffer.from(audioOf(t).buffer || "", "base64");

  const LINE = "Twinkle, twinkle, little star.";
  /** This section's deployment. `DEMO_CACHE_COUNTER=0` turns off §15's per-IP tier,
   *  which shares the store and would otherwise rate-limit these fixtures and pollute the
   *  op counts. 16a runs the shipped defaults with both tiers; 16g checks they coexist. */
  const VOICED = { ...FULL, DEMO_TTS_VOICE: "amy", DEMO_CACHE_COUNTER: "0" };
  /** The clamp floor, so a hang costs 50 ms per assertion rather than a whole second. */
  const FAST = { ...VOICED, DEMO_TTS_CACHE_TIMEOUT_MS: "50" };

  // ---- 16a. THE SEAM: the switch, and the runtime with no cache at all ----- //
  {
    const ON = wire2.readConfig(FULL);
    const OFF = wire2.readConfig({ ...FULL, DEMO_TTS_CACHE: "0" });
    eq(ON.ttsCache, true, "DEMO_TTS_CACHE defaults ON — the tier ships enabled");
    eq(OFF.ttsCache, false, "DEMO_TTS_CACHE=0 switches the audio cache off with no code change");
    eq(ON.ttsCacheTtlS, 86400, "DEMO_TTS_CACHE_TTL_S defaults to one day");
    eq(ON.ttsCacheTimeoutMs, 1000, "DEMO_TTS_CACHE_TIMEOUT_MS defaults to 1000 ms — 4x the counter tier's");
    eq(wire2.readConfig({ ...FULL, DEMO_TTS_CACHE_TTL_S: "0" }).ttsCacheTtlS, 86400,
       "…and an out-of-range TTL falls back to the default rather than becoming a stranger one");
    eq(wire2.readConfig({ ...FULL, DEMO_TTS_CACHE_TTL_S: "99999999" }).ttsCacheTtlS, 86400, "…in both directions");
    eq(wire2.readConfig({ ...FULL, DEMO_TTS_CACHE_TIMEOUT_MS: "1" }).ttsCacheTimeoutMs, 1000,
       "…and a 1 ms deadline would switch the tier off by stealth, so it is refused too");
    eq(wire2.readConfig({ ...FULL, DEMO_TTS_CACHE_TIMEOUT_MS: "60000" }).ttsCacheTimeoutMs, 1000,
       "…as would a deadline that out-waits DEMO_SPEECH_TIMEOUT_MS");
    for (const k of ["tts_cache", "tts_cache_ttl_s", "DEMO_TTS_CACHE", "DEMO_TTS_CACHE_TTL_S"]) {
      ok(!(k in wire2.publicLimits(ON)), `the audio cache is server-side only: ${k} is not published to the browser`);
    }

    // NO `caches` GLOBAL. This is the default under bare node, so every other section in
    // this file has already been running the pre-change route — but say it once, on
    // purpose, because "exactly today's behaviour" is the whole promise of this tier.
    fresh();
    eq(typeof caches, "undefined", "bare node has no caches global — the absent-cache path is the default one");
    const plain = await turn(LINE);
    eq(plain.speech.res.status, 200, "with NO cache reachable at all, /api/speech answers exactly as before");
    eq(synths(), 1, "…having synthesised exactly once");
    eq(cstats().checked, 0, "…having consulted no cache at all");
    eq(ttscache.ttsStore(wire2.readConfig(FULL)), null, "…and ttsStore() answers null, which is the seam");

    // THE KILL SWITCH: a cache is right there, and the route does not touch it.
    fresh();
    const c = audioCache();
    await withCache(c, async () => {
      const off1 = await turn(LINE, { ...FULL, DEMO_TTS_CACHE: "0" });
      eq(off1.speech.res.status, 200, "DEMO_TTS_CACHE=0 still serves the turn");
      eq(ttsOps(c), 0, "…and makes ZERO audio-cache calls — the switch is a seam, not a filter");
      eq(cstats().checked, 0, "…recorded as never checked");
      fresh();
      const off2 = await turn(LINE, { ...FULL, DEMO_TTS_CACHE: "0" });
      eq(synths(), 1, "…so the SAME line synthesises again, which is the pre-change behaviour exactly");
      deep([...bytesOf(off2)], [...bytesOf(off1)], "…and answers the same audio the gateway just made");
    });
    eq(ttscache.ttsStore(wire2.readConfig({ ...FULL, DEMO_TTS_CACHE: "0" })), null,
       "ttsStore() answers null for a switched-off deployment even with a global cache present");
  }

  // ---- 16b. THE HIT: byte-identical audio, zero upstream calls, one op ----- //
  {
    fresh();
    const colo = audioCache();
    await withCache(colo, async () => {
      const first = await turn(LINE, VOICED);
      eq(first.speech.res.status, 200, "the first visitor to a colo gets a synthesised line");
      eq(synths(), 1, "…which cost one upstream synthesis");
      eq(ttsCalls(colo, "match"), 1, "…one cache read");
      eq(ttsCalls(colo, "put"), 1, "…and one cache write");
      eq(cstats().miss, 1, "…recorded as a miss");
      eq(cstats().wrote, 1, "…and a write");
      eq(cstats().ops, 2, "…two completed cache ops on a miss");
      const key = ttsKeys(colo)[0];

      // THE STORED ENTRY IS THE AUDIO, ROUND-TRIPPED THROUGH THE REAL DECODER. If the
      // envelope lost a sample or a rate, the byte comparison below would still pass while
      // a child heard the wrong thing, so the entry itself is decoded and compared.
      const stored = wav.pcmFromAudio(colo.bytes(key), { format: "wav" });
      deep([...stored.pcm], [...bytesOf(first)], "the STORED entry decodes to exactly the PCM that was served");
      eq(stored.sampleRate, audioOf(first).sample_rate, "…carrying the same sample rate");
      eq(stored.channels, audioOf(first).channels, "…and the same channel count");

      // A NEW ISOLATE, THE SAME COLO CACHE. And the stub is told to answer with DIFFERENT
      // audio from here on, so audio that still matches the first turn can only have come
      // out of the cache — a hit proven by content, not by a counter.
      fresh();
      P.plan = { speech: { audio: wav.writeWav(pcmBytes(77), { sampleRate: 8000, channels: 1, bitsPerSample: 16 }) } };
      const second = await turn(LINE, VOICED);
      eq(second.speech.res.status, 200, "a second isolate serving the same line answers 200");
      eq(second.speech.body.reason, null, "…with no reason");
      eq(second.speech.body.degraded, false, "…and not degraded: a hit is an ordinary success");
      eq(synths(), 0, "A HIT COSTS ZERO UPSTREAM CALLS — nothing was posted to /audio/speech");
      eq(sent.length, 1, "…the only outbound request in the turn was the chat completion");
      eq(ttsCalls(colo, "put"), 1, "…and a hit writes nothing back: still the one write from the miss");
      eq(cstats().hit, 1, "…recorded as a hit");
      eq(cstats().ops, 1, "…one cache op for a hit, two for a miss");

      // BYTES, NOT LENGTHS.
      const a = bytesOf(first);
      const b = bytesOf(second);
      ok(a.length > 100, `there is real audio to compare, got ${a.length} bytes`);
      eq(b.length, a.length, "the hit returns the same number of PCM bytes");
      eq(Buffer.compare(a, b), 0, "THE HIT IS BYTE-IDENTICAL TO THE MISS — compared as bytes");
      eq(audioOf(second).buffer, audioOf(first).buffer, "…so the base64 on the wire is identical too");
      eq(audioOf(second).sample_rate, audioOf(first).sample_rate, "…as is the declared sample rate");
      eq(audioOf(second).channels, audioOf(first).channels, "…and the declared channel count");
      eq(audioOf(second).sample_rate, 22050, "…which is the FIRST synthesis's rate, not the stub's new 8000");

      // The rest of the envelope is the turn's own, never the cached turn's: the event id
      // comes from THIS ticket, or two visitors would share one event.
      const e2 = JSON.parse(second.chat.body.messages[0].payload).event_id;
      eq(second.payload.event_id, e2, "the event id is THIS turn's, not the one whose audio was cached");
      ok(second.payload.event_id !== first.payload.event_id, "…and the two turns do not share an event id");
      deep(Object.keys(second.payload).sort(), ["audio", "chunk_num", "event_id", "marks", "request_source"],
           "…and the CloudTTSResponse field set is unchanged by the cache");

      // THE TTL IS THE CONFIGURED ONE, carried on the entry itself — it is both the
      // cache's eviction clock and `readCachedAudio`'s own staleness test, so the two can
      // only agree if this is the number written.
      eq(colo.store.get(key).maxAge, 86400, "the entry carries DEMO_TTS_CACHE_TTL_S as its max-age");
    });

    // THE HEADER'S OWN RATE SURVIVES THE ROUND TRIP (§2.2): a 16 kHz voice must play at
    // 16 kHz on a hit too, which is why the stored body is a WAV, not bare samples.
    fresh();
    const odd = audioCache();
    await withCache(odd, async () => {
      P.plan = { speech: { audio: wav.writeWav(pcmBytes(64), { sampleRate: 16000, channels: 1, bitsPerSample: 16 }) } };
      const a = await turn("A sixteen kilohertz line.", VOICED);
      eq(audioOf(a).sample_rate, 16000, "the miss carries the WAV header's 16000, not the configured 22050");
      fresh();
      P.plan = { speech: { audio: wav.writeWav(pcmBytes(9), { sampleRate: 44100, channels: 1, bitsPerSample: 16 }) } };
      const b = await turn("A sixteen kilohertz line.", VOICED);
      eq(synths(), 0, "…the repeat is a hit");
      eq(audioOf(b).sample_rate, 16000,
         "…and the HIT carries the STORED 16000 too — not the configured rate, and not the stub's new one");
      eq(audioOf(b).channels, 1, "…with the stored channel count");
      eq(Buffer.compare(bytesOf(b), bytesOf(a)), 0, "…and byte-identical samples");
    });

    // A CUSTOM TTL is honoured, so the knob is a knob.
    fresh();
    const ttl = audioCache();
    await withCache(ttl, async () => {
      await turn(LINE, { ...VOICED, DEMO_TTS_CACHE_TTL_S: "3600" });
      const k = ttsEntries(ttl)[0];
      eq(ttl.store.get(k).maxAge, 3600, "DEMO_TTS_CACHE_TTL_S=3600 writes max-age=3600");
    });
  }

  // ---- 16c. NEVER CACHE ANYTHING BUT A SUCCESSFUL SYNTHESIS --------------- //
  // A cached refusal would be inherited by every visitor to the colo for a day; a cached
  // partial body would be static on every hit.
  {
    const badly = [
      ["an upstream 500", { status: 500 }],
      ["an upstream 429", { status: 429 }],
      ["a 3xx redirect, unfollowed", { status: 302 }],
      ["a JSON error body where audio was expected", { status: 200, body: JSON.stringify({ error: "nope" }) }],
      ["an HTML Access login page", { status: 200, body: "<!DOCTYPE html><html><body>login</body></html>" }],
      ["an empty 200 body", { status: 200, body: "" }],
      ["a text/plain proxy error", { status: 200, body: "upstream connect error or disconnect" }],
      ["a gateway timeout", { throw: "TimeoutError" }],
      ["a network failure", { throw: "TypeError" }],
    ];
    for (const [label, sp] of badly) {
      fresh();
      const c = audioCache();
      await withCache(c, async () => {
        P.plan = { speech: sp };
        const t = await turn(LINE, VOICED);
        eq(t.speech.body.ok, false, `${label}: the visitor is told the voice is degraded`);
        eq(t.speech.body.degraded, true, `…${label} degrades`);
        eq(ttsCalls(c, "put"), 0, `NEVER CACHE A NON-SUCCESS: ${label} writes NOTHING to the cache`);
        deep(ttsEntries(c), [], `…${label} leaves the audio store empty`);
        eq(cstats().wrote, 0, `…${label} is recorded as no write`);

        // …and the failure did not poison the key either: the next good turn for the same
        // line still synthesises and still succeeds.
        fresh();
        P.plan = {};
        const good = await turn(LINE, VOICED);
        eq(good.speech.res.status, 200, `…and after ${label} the next turn for that line is served normally`);
        eq(synths(), 1, `…by synthesising it, because ${label} cached nothing to serve from`);
        eq(ttsEntries(c).length, 1, `…and THAT one is stored`);
      });
    }
  }

  // ---- 16d. THE KEY: everything that changes the audio is in it ----------- //
  // A key ignoring the voice serves one child a line in another's voice. Each component is
  // varied ALONE, at the key and end to end (the second configuration must MISS).
  {
    const R = req("/api/speech", { ticket: "x" });
    const keyFor = (env, text) => ttscache.ttsCacheKey(wire2.readConfig(env), R, text === undefined ? LINE : text);
    const base = await keyFor(VOICED);

    ok(base.startsWith(ORIGIN + "/__moxie/tts/"),
       `the entry lives on our OWN origin under a non-route prefix — got ${base}`);
    const digest = base.slice((ORIGIN + "/__moxie/tts/").length);
    ok(/^[0-9a-f]{64}$/.test(digest), `…and the whole 256-bit HMAC, untruncated, got ${digest.length} hex chars`);
    ok(!base.includes("Twinkle") && !base.toLowerCase().includes("twinkle"),
       "the TEXT is never in a cache key — the entry is keyed, not readable");
    ok(!base.includes("gw.invalid.test") && !base.includes("test-voice-model") && !base.includes("amy"),
       "…and neither is the gateway, the model or the voice");
    eq(await keyFor(VOICED), base, "the same inputs key the same entry, or nothing would ever hit");

    const variants = [
      ["the MODEL", { ...VOICED, DEMO_TTS_MODEL: "other-voice-model" }, undefined],
      ["the VOICE", { ...VOICED, DEMO_TTS_VOICE: "ryan" }, undefined],
      ["the FORMAT", { ...VOICED, DEMO_TTS_FORMAT: "pcm" }, undefined],
      ["the SAMPLE RATE", { ...VOICED, DEMO_TTS_SAMPLE_RATE: "16000" }, undefined],
      ["the GATEWAY", { ...VOICED, DEMO_GATEWAY_BASE_URL: "https://other.invalid.test/v1" }, undefined],
      ["the TEXT", VOICED, "Twinkle, twinkle, little star"],
      ["ONE COMMA of the text", VOICED, "Twinkle twinkle, little star."],
      ["the CASE of the text", VOICED, "twinkle, twinkle, little star."],
    ];
    const seenKeys = new Map([[base, "the base configuration"]]);
    for (const [label, env, text] of variants) {
      const k = await keyFor(env, text);
      ok(k !== base, `changing ${label} changes the cache key`);
      ok(!seenKeys.has(k), `…and ${label} does not collide with ${seenKeys.get(k) || ""}`);
      seenKeys.set(k, label);
    }
    // Length-prefixed, so no two component boundaries can be slid into each other.
    ok((await keyFor({ ...VOICED, DEMO_TTS_MODEL: "ab", DEMO_TTS_VOICE: "c" })) !==
       (await keyFor({ ...VOICED, DEMO_TTS_MODEL: "a", DEMO_TTS_VOICE: "bc" })),
       "the components are length-prefixed: 'ab'+'c' and 'a'+'bc' are different keys");

    // END TO END. Warm the colo under one voice, then ask for the same line under another:
    // it must MISS and synthesise, because the alternative is a child hearing the wrong one.
    for (const [label, env] of [
      ["a different VOICE", { ...VOICED, DEMO_TTS_VOICE: "ryan" }],
      ["a different MODEL", { ...VOICED, DEMO_TTS_MODEL: "other-voice-model" }],
      ["a different SAMPLE RATE", { ...VOICED, DEMO_TTS_SAMPLE_RATE: "16000" }],
      ["a different FORMAT", { ...VOICED, DEMO_TTS_FORMAT: "pcm" }],
    ]) {
      fresh();
      const colo = audioCache();
      await withCache(colo, async () => {
        await turn(LINE, VOICED);
        eq(synths(), 1, `warming the colo under the base configuration synthesises once (${label})`);
        fresh();
        const other = await turn(LINE, env);
        eq(other.speech.res.status, 200, `${label} still serves the line`);
        eq(synths(), 1, `${label} MISSES and pays for its own synthesis — it is never served the base voice`);
        eq(cstats().hit, 0, `…recorded as no hit for ${label}`);
        eq(ttsEntries(colo).length, 2, `…and ${label} stores a SECOND entry rather than overwriting the first`);
      });
    }

    // The control: the same configuration, twice, DOES hit. Without this the four
    // assertions above would also pass on a cache that never hits at all.
    fresh();
    const colo = audioCache();
    await withCache(colo, async () => {
      await turn(LINE, VOICED);
      fresh();
      await turn(LINE, VOICED);
      eq(synths(), 0, "CONTROL: the SAME configuration and the same line hits, so the misses above mean something");
      eq(ttsEntries(colo).length, 1, "…and stores exactly one entry");
    });
  }

  // ---- 16e. FAIL OPEN — every failure mode, through the whole route -------- //
  // Each case must answer the SAME 200 with the SAME bytes as no cache at all, having
  // synthesised exactly once: "costs nothing but a synthesis", asserted literally.
  {
    // The reference: one turn with no cache in the picture at all.
    fresh();
    const reference = await turn(LINE, FAST);
    const REF = bytesOf(reference);
    eq(synths(), 1, "the reference turn, with no cache, synthesises once");
    ok(REF.length > 100, "…and produces real audio to compare against");

    // Learn the key this configuration and line use, so the corrupt-entry cases can seed it.
    fresh();
    const learn = audioCache();
    await withCache(learn, () => turn(LINE, FAST));
    const KEY = ttsKeys(learn)[0] || "no-key";
    const GOOD = learn.bytes(KEY);
    ok(GOOD && GOOD.length > 100, "…and a good entry to corrupt");

    const modes = [
      ["a cache MISS", () => audioCache(), { miss: 1 }],
      ["a STALE entry, past its own max-age", () => audioCache().seed(KEY, GOOD, 86400, 999999), { stale: 1 }],
      ["a match that THROWS SYNCHRONOUSLY", () => audioCache({ matchThrowsSync: true }).seed(KEY, GOOD), { errors: 1 }],
      ["a match that REJECTS", () => audioCache({ matchRejects: true }).seed(KEY, GOOD), { errors: 1 }],
      ["a match that HANGS FOR EVER", () => audioCache({ matchHangs: true }).seed(KEY, GOOD), { timeouts: 1 }],
      ["a match that answers something that is not a Response", () => audioCache({ matchReturnsJunk: true }), { corrupt: 1 }],
      ["a body read that THROWS SYNCHRONOUSLY", () => audioCache({ readThrowsSync: true }).seed(KEY, GOOD), { errors: 1 }],
      ["a body read that REJECTS", () => audioCache({ readRejects: true }).seed(KEY, GOOD), { errors: 1 }],
      ["a body read that HANGS FOR EVER", () => audioCache({ readHangs: true }).seed(KEY, GOOD), { timeouts: 1 }],
      ["an entry whose bytes are NOT a WAV", () => audioCache({ bodyOverride: new Uint8Array([1, 2, 3, 4, 5, 6]) }).seed(KEY, GOOD), { corrupt: 1 }],
      ["an entry that is an HTML page", () => audioCache({ bodyOverride: "<!DOCTYPE html><html></html>" }).seed(KEY, GOOD), { corrupt: 1 }],
      ["an entry that is a JSON error body", () => audioCache({ bodyOverride: '{"error":"gone"}' }).seed(KEY, GOOD), { corrupt: 1 }],
      ["an entry that is EMPTY", () => audioCache({ bodyOverride: new Uint8Array(0) }).seed(KEY, GOOD), { corrupt: 1 }],
      ["an entry TRUNCATED mid-body", () => audioCache({ bodyOverride: GOOD.slice(0, 30) }).seed(KEY, GOOD), { corrupt: 1 }],
      // Two errors, not one: with no `match` AND no `put`, both halves of the tier fail —
      // and both of them fall open, which is the point of listing it.
      ["a store with NO METHODS AT ALL", () => ({ log: { match: 0, put: 0, keys: [], calls: [] }, store: new Map() }), { errors: 2, wrote: 0 }],
      ["a put that THROWS SYNCHRONOUSLY", () => audioCache({ putThrowsSync: true }), { errors: 1, miss: 1 }],
      ["a put that REJECTS", () => audioCache({ putRejects: true }), { errors: 1, miss: 1 }],
      ["a put that HANGS FOR EVER", () => audioCache({ putHangs: true }), { timeouts: 1, miss: 1 }],
    ];
    for (const [label, make, want] of modes) {
      fresh();
      const c = make();
      await withCache(c, async () => {
        const t = await turn(LINE, FAST);
        eq(t.speech.res.status, 200, `FAIL OPEN: ${label} must still answer the ordinary 200`);
        eq(t.speech.body.reason, null, `FAIL OPEN: ${label} carries no refusal reason`);
        eq(t.speech.body.degraded, false, `FAIL OPEN: ${label} does not degrade the page`);
        eq(synths(), 1, `FAIL OPEN: ${label} costs EXACTLY ONE synthesis — no more, and no refusal`);
        eq(Buffer.compare(bytesOf(t), REF), 0, `FAIL OPEN: ${label} returns exactly the audio a cacheless run returns`);
        eq(limits.__state().inflight.speech, 0, `…and ${label} leaks no concurrency slot`);
        for (const [k, v] of Object.entries(want)) eq(cstats()[k], v, `…and ${label} is recorded as ${k}`);
      });
    }

    // A `caches` GLOBAL THAT THROWS ON ACCESS — a runtime that has the name and not the
    // thing. `ttsStore` must answer null rather than let the getter escape into the route.
    fresh();
    Object.defineProperty(globalThis, "caches", {
      configurable: true,
      get() { throw new Error("no cache on this runtime"); },
    });
    try {
      eq(ttscache.ttsStore(wire2.readConfig(FAST)), null, "a caches global that THROWS answers null, not an exception");
      const t = await turn(LINE, FAST);
      eq(t.speech.res.status, 200, "FAIL OPEN: a caches global that throws still serves the turn");
      eq(synths(), 1, "…with exactly one synthesis");
      eq(Buffer.compare(bytesOf(t), REF), 0, "…and exactly the cacheless audio");
    } finally {
      delete globalThis.caches;
    }
    eq(typeof caches, "undefined", "the global is put back, so no later block inherits a cache");

    // A KEY THAT CANNOT BE DERIVED. The last seatbelt: whatever went wrong, no key means no
    // cache, which means one synthesis.
    fresh();
    const noKey = await ttscache.ttsCacheKey(wire2.readConfig(FAST), { url: "not a url" }, LINE);
    eq(noKey, "", "a request whose URL will not parse yields NO key rather than throwing");
    eq(await ttscache.readCachedAudio(audioCache(), wire2.readConfig(FAST), ""), null,
       "…and an empty key reads nothing, which is a miss");
  }

  // ---- 16f. THE CAPS DECIDE FIRST — a hit is not a way past one ----------- //
  // Refusals must cost zero CACHE calls too: consulted before the caps, a warm entry would
  // serve audio to a refused request and void `DEMO_MAX_TTS_CHARS`, the TTL and replay set.
  {
    fresh();
    const colo = audioCache();
    await withCache(colo, async () => {
      // Warm the entry under the shipped configuration.
      const warm = await turn(LINE, VOICED);
      eq(warm.speech.res.status, 200, "the line is warm in this colo");
      const warmed = ttsCalls(colo, "match");

      // 1. The CONFIGURATION GOT TIGHTER. The same warm line, redeemed under a
      //    DEMO_MAX_TTS_CHARS below its length, is `too_long` — not a free cache hit.
      fresh();
      const tight = { ...VOICED, DEMO_MAX_TTS_CHARS: String(LINE.length - 1) };
      P.plan = { chat: { content: LINE } };
      const c1 = await call(chat, "/api/chat", { text: "say it" }, null, VOICED);
      const before = ttsOps(colo);
      const s1 = await call(speech, "/api/speech", { ticket: c1.body.speech[0].ticket }, null, tight);
      eq(s1.res.status, 400, "an over-length ticket is refused even though the audio is sitting in the cache");
      eq(s1.body.reason, "too_long", "…as too_long");
      deep(s1.body.messages, [], "…with no audio at all");
      eq(ttsOps(colo) - before, 0, "…and the audio cache was not touched: the cap decides first");

      // 2. A FORGED TICKET. Nothing is looked up for a caller who never paid for a turn.
      fresh();
      const b2 = ttsOps(colo);
      const forged = await call(speech, "/api/speech", { ticket: "v1.AAAA.BBBB" }, null, VOICED);
      eq(forged.body.reason, "bad_ticket", "a forged ticket is bad_ticket");
      eq(ttsOps(colo) - b2, 0, "…and touches no cache entry");

      // 3. A REPLAYED TICKET. The per-isolate spent set still decides before the cache.
      fresh();
      P.plan = { chat: { content: LINE } };
      const c3 = await call(chat, "/api/chat", { text: "say it" }, null, VOICED);
      await call(speech, "/api/speech", { ticket: c3.body.speech[0].ticket }, null, VOICED);
      const b3 = ttsOps(colo);
      const replay = await call(speech, "/api/speech", { ticket: c3.body.speech[0].ticket }, null, VOICED);
      eq(replay.body.reason, "bad_ticket", "a replayed ticket is refused inside the isolate that spent it");
      eq(ttsOps(colo) - b3, 0, "…without a cache call");

      // 4. A FORBIDDEN ORIGIN — the cheapest refusal of all.
      fresh();
      const b4 = ttsOps(colo);
      const hotlinked = await call(speech, "/api/speech", { ticket: "v1.a.b" },
        { Origin: "https://evil.invalid.test", "Sec-Fetch-Site": "cross-site" }, VOICED);
      eq(hotlinked.body.reason, "forbidden_origin", "a hotlinked request is forbidden_origin");
      eq(ttsOps(colo) - b4, 0, "…and never reaches the cache");

      // 5. A RATE-LIMITED VISITOR. DEMO_SPEECH_PER_MIN turns, then a refusal — and the
      //    refusal is free of cache calls even though every one of those turns was a hit.
      fresh();
      const limited = { ...VOICED, DEMO_SPEECH_PER_MIN: "2" };
      P.plan = { chat: { content: LINE } };
      const tickets = [];
      for (let i = 0; i < 3; i++) {
        const c = await call(chat, "/api/chat", { text: "say it" }, null, limited);
        tickets.push(c.body.speech[0].ticket);
      }
      await call(speech, "/api/speech", { ticket: tickets[0] }, null, limited);
      await call(speech, "/api/speech", { ticket: tickets[1] }, null, limited);
      const b5 = ttsOps(colo);
      const rl = await call(speech, "/api/speech", { ticket: tickets[2] }, null, limited);
      eq(rl.res.status, 429, "the third speech turn in the minute is rate-limited");
      eq(rl.body.reason, "rate_limited", "…as rate_limited");
      eq(ttsOps(colo) - b5, 0, "…and a rate-limited visitor makes no cache call either");

      ok(warmed >= 1, "…and the warm entry that made all of that meaningful was really read at least once");
    });
  }

  // ---- 16g. TWO TIERS, ONE STORE, THE SHIPPED DEFAULTS -------------------- //
  // `limits.js`'s per-IP counter writes into the same `caches.default`. With both on, as
  // shipped, neither may read or overwrite the other; the key prefixes are the separation.
  {
    fresh();
    const shared = audioCache();
    await withCache(shared, async () => {
      const t = await turn(LINE, { ...FULL, DEMO_TTS_VOICE: "amy" });
      eq(t.speech.res.status, 200, "with BOTH tiers on and one store, a turn is served normally");
      const rl = [...shared.store.keys()].filter((k) => String(k).includes("/__moxie/rl/"));
      const tts = ttsEntries(shared);
      ok(rl.length >= 1, `the counter tier wrote its own entries, got ${rl.length}`);
      eq(tts.length, 1, "…and the audio tier wrote exactly one of its own");
      ok(rl.every((k) => !tts.includes(k)), "…and not one key is shared between the two tiers");
      ok(tts.every((k) => k.startsWith(ORIGIN + "/__moxie/tts/")), "…the audio prefix is its own");
      ok(rl.every((k) => k.startsWith(ORIGIN + "/__moxie/rl/")), "…and so is the counter's");

      // The counter's body is a JSON integer and the audio tier's is a RIFF/WAVE. Feeding
      // either to the other is exactly the mix-up the prefixes prevent, so prove the audio
      // reader refuses a counter entry rather than handing a child `{"n":1}` as samples.
      let asAudio = "it threw";
      try {
        asAudio = await ttscache.readCachedAudio(shared, wire2.readConfig(FULL), rl[0]);
      } catch (err) {
        // `readCachedAudio` MAY NOT THROW, ever. Its whole contract is "audio, or null" —
        // an exception here escapes into `/api/speech` and becomes a 500 with the
        // platform's HTML error page instead of a synthesis.
        ok(false, `readCachedAudio THREW on an entry it did not write: ${err && err.message}`);
      }
      eq(asAudio, null, "the audio reader refuses a COUNTER entry: JSON is not audio, and it says so by missing");

      // And a second turn still hits its own entry with both tiers live.
      fresh();
      const again = await turn(LINE, { ...FULL, DEMO_TTS_VOICE: "amy" });
      eq(synths(), 0, "…and with both tiers on, the repeat is still a hit costing zero synthesis");
      eq(Buffer.compare(bytesOf(again), bytesOf(t)), 0, "…returning the same bytes");
    });
  }
}
