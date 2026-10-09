/* test_demo_proxy §16: the synthesised-audio cache (§4.8) — `/api/speech` stops paying twice
 * for a line. A hit is byte-identical to a miss and costs zero upstream calls; every failure
 * costs exactly one synthesis; only a successful synthesis is stored; the caps decide first;
 * and on Pages the answer never waits for the write (16h).
 * Not asserted: any hit rate (per-colo; a cold colo pays full price). Run via the entry file. */
import {
  FULL, ORIGIN, P, assertClean, call, chat, deep, eq,
  fresh, limits, ok, pcmBytes, req, sent, speech, ttscache, wav, wire2,
} from "./harness.mjs";

{
  /** A fake `caches.default` that stores BYTES: common.mjs's fakeCache failure shapes plus
   *  a body read that fails three ways, junk from match, and a corrupt entry. */
  function audioCache(o = {}) {
    const store = new Map();
    const log = { calls: [] };
    const hang = () => new Promise(() => {});
    const body = (bytes, maxAge, ageS) => {
      const res = new Response(bytes, { headers: {
        "Content-Type": "audio/wav", "Cache-Control": "max-age=" + maxAge, ...(ageS === undefined ? {} : { Age: String(ageS) }) } });
      if (o.readThrowsSync) res.arrayBuffer = () => { throw new Error("read threw"); };
      if (o.readRejects) res.arrayBuffer = () => Promise.reject(new Error("read rejected"));
      if (o.readHangs) res.arrayBuffer = hang;
      return res;
    };
    return {
      log, store,
      seed(key, bytes, maxAge = 86400, ageS) { store.set(String(key), { bytes, maxAge, ageS }); return this; },
      bytes(key) { const e = store.get(String(key)); return e ? e.bytes : null; },
      match(key) {
        log.calls.push({ op: "match", key: String(key) });
        if (o.matchThrowsSync) throw new Error("match threw");
        if (o.matchHangs) return hang();
        if (o.matchRejects) return Promise.reject(new Error("match rejected"));
        if (o.matchReturnsJunk) return Promise.resolve({ notAResponse: true });
        const e = store.get(String(key));
        return Promise.resolve(e ? body(o.bodyOverride === undefined ? e.bytes : o.bodyOverride, e.maxAge, e.ageS) : undefined);
      },
      put(key, res) {
        log.calls.push({ op: "put", key: String(key) });
        if (o.putThrowsSync) throw new Error("put threw");
        if (o.putHangs) return hang();
        if (o.putRejects) return Promise.reject(new Error("put rejected"));
        return (async () => {
          const cc = /max-age=(\d+)/.exec(res.headers.get("Cache-Control") || "");
          store.set(String(key), { bytes: new Uint8Array(await res.arrayBuffer()), maxAge: cc ? Number(cc[1]) : 0 });
        })();
      },
    };
  }

  // ONE STORE, TWO TIERS: limits.js's counter writes /__moxie/rl/ into the same caches.default,
  // so every count here is filtered to /__moxie/tts/ (16g asserts they coexist).
  const TTS = "/__moxie/tts/";
  const ttsCalls = (c, op) => c.log.calls.filter((x) => x.op === op && x.key.includes(TTS)).length;
  const ttsOps = (c) => ttsCalls(c, "match") + ttsCalls(c, "put");
  const ttsEntries = (c) => [...c.store.keys()].filter((k) => k.includes(TTS));
  const cstats = () => ttscache.__ttsCacheState();
  /** The global `caches.default` production reads, always put back. */
  async function withCache(store, fn) {
    globalThis.caches = { default: store };
    try { return await fn(); } finally { delete globalThis.caches; }
  }
  /** Synthesis calls, from the intercepted request log. */
  const synths = () => sent.filter((s) => s.url.endsWith("/audio/speech")).length;
  /** One whole turn: a chat reply with a fixed line, then that line spoken. A THROW IS
   *  RECORDED, NOT PROPAGATED — on Cloudflare it would be a platform 500 page. */
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
  const wavOf = (n, sampleRate) => wav.writeWav(pcmBytes(n), { sampleRate, channels: 1, bitsPerSample: 16 });

  const LINE = "Twinkle, twinkle, little star.";
  // DEMO_CACHE_COUNTER=0 keeps §15's per-IP tier (same store) out of the op counts; 16g runs both.
  const VOICED = { ...FULL, DEMO_TTS_VOICE: "amy", DEMO_CACHE_COUNTER: "0" };
  const FAST = { ...VOICED, DEMO_TTS_CACHE_TIMEOUT_MS: "50" };

  // 16a. THE SEAM: defaults and clamps, no cache at all, and the kill switch.
  {
    const ON = wire2.readConfig(FULL);
    deep([ON.ttsCache, wire2.readConfig({ ...FULL, DEMO_TTS_CACHE: "0" }).ttsCache, ON.ttsCacheTtlS, ON.ttsCacheTimeoutMs],
         [true, false, 86400, 1000], "DEMO_TTS_CACHE defaults ON (=0 off), TTL one day, deadline 1000 ms");
    for (const [k, v, field, want] of [["DEMO_TTS_CACHE_TTL_S", "0", "ttsCacheTtlS", 86400], ["DEMO_TTS_CACHE_TTL_S", "99999999", "ttsCacheTtlS", 86400],
      ["DEMO_TTS_CACHE_TIMEOUT_MS", "1", "ttsCacheTimeoutMs", 1000], ["DEMO_TTS_CACHE_TIMEOUT_MS", "60000", "ttsCacheTimeoutMs", 1000]]) {
      eq(wire2.readConfig({ ...FULL, [k]: v })[field], want, `${k}=${v} is out of range and falls back to the default`);
    }
    ok(!Object.keys(wire2.publicLimits(ON)).some((k) => /tts_cache/i.test(k)), "the audio cache is server-side only");

    fresh();
    const plain = await turn(LINE);
    deep([plain.speech.res.status, synths(), cstats().checked], [200, 1, 0],
         "with NO caches global, /api/speech answers as before: one synthesis, no cache consulted");
    eq(ttscache.ttsStore(wire2.readConfig(FULL)), null, "…and ttsStore() answers null, which is the seam");

    fresh();
    const c = audioCache();
    await withCache(c, async () => {
      const OFF = { ...FULL, DEMO_TTS_CACHE: "0" };
      eq(ttscache.ttsStore(wire2.readConfig(OFF)), null, "ttsStore() is null for a switched-off deployment even with a cache present");
      const off1 = await turn(LINE, OFF);
      fresh();
      const off2 = await turn(LINE, OFF);
      deep([off2.speech.res.status, ttsOps(c), synths()], [200, 0, 1],
           "DEMO_TTS_CACHE=0 makes ZERO audio-cache calls, so the same line synthesises again");
      ok(bytesOf(off2).equals(bytesOf(off1)), "…answering the same audio");
    });
  }

  // 16b. THE HIT: byte-identical audio, zero upstream calls, one op.
  {
    fresh();
    const colo = audioCache();
    await withCache(colo, async () => {
      const first = await turn(LINE, VOICED);
      deep([first.speech.res.status, synths(), ttsCalls(colo, "match"), ttsCalls(colo, "put"), cstats().miss, cstats().wrote, cstats().ops],
           [200, 1, 1, 1, 1, 1, 2], "a miss: one synthesis, one read, one write (recorded miss/wrote, two ops)");
      const key = ttsEntries(colo)[0];
      // The stored entry is decoded through the REAL decoder, so a lost sample/rate is caught.
      const stored = wav.pcmFromAudio(colo.bytes(key), { format: "wav" });
      ok(Buffer.from(stored.pcm).equals(bytesOf(first)) && stored.sampleRate === audioOf(first).sample_rate &&
         stored.channels === audioOf(first).channels, "the STORED entry decodes to exactly the PCM, rate and channels served");
      eq(colo.store.get(key).maxAge, 86400, "the entry carries DEMO_TTS_CACHE_TTL_S as its max-age");

      // A new isolate, same colo, and the stub now answers DIFFERENT audio: a match can only
      // have come out of the cache — a hit proven by content, not a counter.
      fresh();
      P.plan = { speech: { audio: wavOf(77, 8000) } };
      const second = await turn(LINE, VOICED);
      deep([second.speech.res.status, second.speech.body.reason, second.speech.body.degraded], [200, null, false],
           "a hit is an ordinary success");
      deep([synths(), sent.length, ttsCalls(colo, "put"), cstats().hit, cstats().ops], [0, 1, 1, 1, 1],
           "A HIT COSTS ZERO UPSTREAM CALLS (only the chat completion went out), writes nothing, one op");
      ok(bytesOf(first).length > 100 && bytesOf(second).equals(bytesOf(first)), "THE HIT IS BYTE-IDENTICAL TO THE MISS — compared as bytes");
      deep([audioOf(second).sample_rate, audioOf(second).channels], [22050, 1], "…at the FIRST synthesis's rate, not the stub's new 8000");
      const e2 = JSON.parse(second.chat.body.messages[0].payload).event_id;
      ok(second.payload.event_id === e2 && e2 !== first.payload.event_id, "the event id is THIS turn's, never the cached turn's");
      deep(Object.keys(second.payload).sort(), ["audio", "chunk_num", "event_id", "marks", "request_source"], "the field set is unchanged by the cache");
    });

    // The header's own rate survives the round trip: the stored body is a WAV, not bare samples.
    fresh();
    await withCache(audioCache(), async () => {
      P.plan = { speech: { audio: wavOf(64, 16000) } };
      const a = await turn("A sixteen kilohertz line.", VOICED);
      fresh();
      P.plan = { speech: { audio: wavOf(9, 44100) } };
      const b = await turn("A sixteen kilohertz line.", VOICED);
      deep([synths(), audioOf(a).sample_rate, audioOf(b).sample_rate, audioOf(b).channels], [0, 16000, 16000, 1],
           "a 16 kHz line is a hit that carries the STORED 16000 — not the configured rate, not the stub's new one");
      ok(bytesOf(b).equals(bytesOf(a)), "…with byte-identical samples");
    });

    fresh();
    const ttl = audioCache();
    await withCache(ttl, () => turn(LINE, { ...VOICED, DEMO_TTS_CACHE_TTL_S: "3600" }));
    eq(ttl.store.get(ttsEntries(ttl)[0]).maxAge, 3600, "DEMO_TTS_CACHE_TTL_S=3600 writes max-age=3600");
  }

  // 16c. NEVER CACHE ANYTHING BUT A SUCCESSFUL SYNTHESIS — a cached refusal would be every
  // colo visitor's for a day. The failure also must not poison the key.
  for (const [label, sp] of [
    ["an upstream 500", { status: 500 }], ["an upstream 429", { status: 429 }], ["a 3xx redirect, unfollowed", { status: 302 }],
    ["a JSON error body where audio was expected", { status: 200, body: JSON.stringify({ error: "nope" }) }],
    ["an HTML Access login page", { status: 200, body: "<!DOCTYPE html><html><body>login</body></html>" }],
    ["an empty 200 body", { status: 200, body: "" }],
    ["a text/plain proxy error", { status: 200, body: "upstream connect error or disconnect" }],
    ["a gateway timeout", { throw: "TimeoutError" }], ["a network failure", { throw: "TypeError" }],
  ]) {
    fresh();
    const c = audioCache();
    await withCache(c, async () => {
      P.plan = { speech: sp };
      const t = await turn(LINE, VOICED);
      deep([t.speech.body.ok, t.speech.body.degraded], [false, true], `${label}: the voice degrades`);
      deep([ttsCalls(c, "put"), ttsEntries(c).length, cstats().wrote], [0, 0, 0], `NEVER CACHE A NON-SUCCESS: ${label} writes NOTHING`);
      fresh();
      P.plan = {};
      const good = await turn(LINE, VOICED);
      deep([good.speech.res.status, synths(), ttsEntries(c).length], [200, 1, 1],
           `…and after ${label} the next turn for that line synthesises, succeeds, and is stored`);
    });
  }

  // 16d. THE KEY: everything that changes the audio is in it, nothing readable is.
  {
    const R = req("/api/speech", { ticket: "x" });
    const keyFor = (env, text = LINE) => ttscache.ttsCacheKey(wire2.readConfig(env), R, text);
    const base = await keyFor(VOICED);
    ok(/^[0-9a-f]{64}$/.test(base.startsWith(ORIGIN + TTS) ? base.slice((ORIGIN + TTS).length) : ""),
       `the entry lives on our OWN origin under a non-route prefix, keyed by the whole 256-bit HMAC — got ${base}`);
    ok(!/twinkle|gw\.invalid\.test|test-voice-model|amy/i.test(base), "the text, gateway, model and voice are never in the key");
    eq(await keyFor(VOICED), base, "the same inputs key the same entry, or nothing would ever hit");
    const seen = new Map([[base, "the base configuration"]]);
    for (const [label, env, text] of [
      ["the MODEL", { ...VOICED, DEMO_TTS_MODEL: "other-voice-model" }], ["the VOICE", { ...VOICED, DEMO_TTS_VOICE: "ryan" }],
      ["the FORMAT", { ...VOICED, DEMO_TTS_FORMAT: "pcm" }], ["the SAMPLE RATE", { ...VOICED, DEMO_TTS_SAMPLE_RATE: "16000" }],
      ["the GATEWAY", { ...VOICED, DEMO_GATEWAY_BASE_URL: "https://other.invalid.test/v1" }],
      ["the TEXT", VOICED, "Twinkle, twinkle, little star"], ["ONE COMMA of the text", VOICED, "Twinkle twinkle, little star."],
      ["the CASE of the text", VOICED, "twinkle, twinkle, little star."],
      // length-prefixed components: 'ab'+'c' and 'a'+'bc' cannot slide into each other
      ["model/voice boundaries (ab|c)", { ...VOICED, DEMO_TTS_MODEL: "ab", DEMO_TTS_VOICE: "c" }],
      ["model/voice boundaries (a|bc)", { ...VOICED, DEMO_TTS_MODEL: "a", DEMO_TTS_VOICE: "bc" }],
    ]) {
      const k = await keyFor(env, text);
      ok(!seen.has(k), `changing ${label} changes the cache key (collides with ${seen.get(k) || "nothing"})`);
      seen.set(k, label);
    }
    // End to end: warm under one voice, ask under another — it must MISS, never serve the
    // wrong voice; the same configuration twice is the CONTROL that does hit.
    for (const [label, env, wantSynths, wantEntries] of [
      ["a different VOICE", { ...VOICED, DEMO_TTS_VOICE: "ryan" }, 1, 2],
      ["a different FORMAT", { ...VOICED, DEMO_TTS_FORMAT: "pcm" }, 1, 2],
      ["CONTROL: the same configuration", VOICED, 0, 1],
    ]) {
      fresh();
      const colo = audioCache();
      await withCache(colo, async () => {
        await turn(LINE, VOICED);
        fresh();
        const other = await turn(LINE, env);
        deep([other.speech.res.status, synths(), ttsEntries(colo).length], [200, wantSynths, wantEntries],
             `${label}: ${wantSynths ? "MISSES, pays its own synthesis and stores a SECOND entry" : "HITS, so the misses mean something"}`);
      });
    }
  }

  // 16e. FAIL OPEN — every failure answers the SAME 200 with the SAME bytes as no cache,
  // at a cost of exactly ONE synthesis.
  {
    fresh();
    const REF = bytesOf(await turn(LINE, FAST));
    ok(REF.length > 100, "the cacheless reference turn produces real audio");
    fresh();
    const learn = audioCache();
    await withCache(learn, () => turn(LINE, FAST));
    const KEY = ttsEntries(learn)[0] || "no-key";
    const GOOD = learn.bytes(KEY);

    for (const [label, make, want] of [
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
      // no match AND no put: both halves fail, and both fall open
      ["a store with NO METHODS AT ALL", () => ({ log: { calls: [] }, store: new Map() }), { errors: 2, wrote: 0 }],
      ["a put that THROWS SYNCHRONOUSLY", () => audioCache({ putThrowsSync: true }), { errors: 1, miss: 1 }],
      ["a put that REJECTS", () => audioCache({ putRejects: true }), { errors: 1, miss: 1 }],
      ["a put that HANGS FOR EVER", () => audioCache({ putHangs: true }), { timeouts: 1, miss: 1 }],
    ]) {
      fresh();
      await withCache(make(), async () => {
        const t = await turn(LINE, FAST);
        deep([t.speech.res.status, t.speech.body.reason, t.speech.body.degraded, synths()], [200, null, false, 1],
             `FAIL OPEN: ${label} answers the ordinary 200 at a cost of EXACTLY ONE synthesis`);
        ok(bytesOf(t).equals(REF), `FAIL OPEN: ${label} returns exactly the audio a cacheless run returns`);
        eq(limits.__state().inflight.speech, 0, `…and ${label} leaks no concurrency slot`);
        for (const [k, v] of Object.entries(want)) eq(cstats()[k], v, `…and ${label} is recorded as ${k}`);
      });
    }

    // A `caches` global that THROWS on access (the name without the thing).
    fresh();
    Object.defineProperty(globalThis, "caches", { configurable: true, get() { throw new Error("no cache on this runtime"); } });
    try {
      eq(ttscache.ttsStore(wire2.readConfig(FAST)), null, "a caches global that THROWS answers null, not an exception");
      const t = await turn(LINE, FAST);
      ok(t.speech.res.status === 200 && synths() === 1 && bytesOf(t).equals(REF), "FAIL OPEN: …and the turn is served, once, cacheless");
    } finally {
      delete globalThis.caches;
    }
    // A key that cannot be derived means no cache, which means one synthesis.
    eq(await ttscache.ttsCacheKey(wire2.readConfig(FAST), { url: "not a url" }, LINE), "", "an unparseable URL yields NO key rather than throwing");
    eq(await ttscache.readCachedAudio(audioCache(), wire2.readConfig(FAST), ""), null, "…and an empty key reads nothing");
  }

  // 16f. THE CAPS DECIDE FIRST — consulted before them, a warm entry would serve a refused
  // request and void DEMO_MAX_TTS_CHARS, the ticket TTL and the replay set.
  {
    fresh();
    const colo = audioCache();
    await withCache(colo, async () => {
      eq((await turn(LINE, VOICED)).speech.res.status, 200, "the line is warm in this colo");
      const refusal = async (label, run, want) => {
        const before = ttsOps(colo);
        const r = await run();
        eq(`${r.res.status} ${r.body.reason}`, want, `${label} is refused even though the audio is sitting in the cache`);
        eq(ttsOps(colo) - before, 0, `…and ${label} never touches the audio cache: the cap decides first`);
      };
      const ticket = async (env = VOICED) => {
        P.plan = { chat: { content: LINE } };
        return (await call(chat, "/api/chat", { text: "say it" }, null, env)).body.speech[0].ticket;
      };
      fresh();
      const t1 = await ticket();
      await refusal("an over-length ticket", () => call(speech, "/api/speech", { ticket: t1 }, null,
        { ...VOICED, DEMO_MAX_TTS_CHARS: String(LINE.length - 1) }), "400 too_long");
      fresh();
      await refusal("a forged ticket", () => call(speech, "/api/speech", { ticket: "v1.AAAA.BBBB" }, null, VOICED), "400 bad_ticket");
      fresh();
      const t3 = await ticket();
      await call(speech, "/api/speech", { ticket: t3 }, null, VOICED);
      await refusal("a replayed ticket", () => call(speech, "/api/speech", { ticket: t3 }, null, VOICED), "400 bad_ticket");
      fresh();
      await refusal("a hotlinked request", () => call(speech, "/api/speech", { ticket: "v1.a.b" },
        { Origin: "https://evil.invalid.test", "Sec-Fetch-Site": "cross-site" }, VOICED), "403 forbidden_origin");
      fresh();
      const limited = { ...VOICED, DEMO_SPEECH_PER_MIN: "2" };
      const ts = [await ticket(limited), await ticket(limited), await ticket(limited)];
      for (const t of ts.slice(0, 2)) await call(speech, "/api/speech", { ticket: t }, null, limited);
      await refusal("a rate-limited visitor", () => call(speech, "/api/speech", { ticket: ts[2] }, null, limited), "429 rate_limited");
    });
  }

  // 16g. TWO TIERS, ONE STORE, THE SHIPPED DEFAULTS: disjoint prefixes, and the audio reader
  // refuses a counter entry (a child must never be handed `{"n":1}` as samples) without throwing.
  {
    fresh();
    const shared = audioCache();
    await withCache(shared, async () => {
      const t = await turn(LINE, { ...FULL, DEMO_TTS_VOICE: "amy" });
      eq(t.speech.res.status, 200, "with BOTH tiers on and one store, a turn is served normally");
      const rl = [...shared.store.keys()].filter((k) => k.startsWith(ORIGIN + "/__moxie/rl/"));
      const tts = ttsEntries(shared);
      ok(rl.length >= 1 && tts.length === 1 && tts.every((k) => k.startsWith(ORIGIN + TTS)) && !rl.some((k) => tts.includes(k)),
         "each tier writes its own entries under its own prefix, sharing no key");
      let asAudio = "it threw";
      try { asAudio = await ttscache.readCachedAudio(shared, wire2.readConfig(FULL), rl[0]); } catch { /* recorded below */ }
      eq(asAudio, null, "the audio reader refuses a COUNTER entry by missing — and may never throw");
      fresh();
      const again = await turn(LINE, { ...FULL, DEMO_TTS_VOICE: "amy" });
      ok(synths() === 0 && bytesOf(again).equals(bytesOf(t)), "…and with both tiers on, the repeat is still a byte-identical hit");
    });
  }

  // 16h. THE ANSWER NEVER WAITS FOR THE WRITE. On Cloudflare the put is handed to
  // `context.waitUntil` and finishes after the response; bare node has no `waitUntil` and
  // still awaits it, which is the path 16a-16g pin (16b reads `wrote` the moment the call
  // returns). The clock here is VIRTUAL and frozen while the route runs: a response that
  // waited for a hanging put could only arrive through the put's deadline timer, which
  // fires only when the test fires it.
  {
    const realSetTimeout = globalThis.setTimeout;
    const realClearTimeout = globalThis.clearTimeout;
    const REAL_MS = 2000;   // real time allowed for a route that is NOT waiting on a timer
    const SERVED = Buffer.from(pcmBytes(200));   // the stub's synthesis, as samples
    const ticketFor = async (env) => {
      P.plan = { chat: { content: LINE }, speech: P.plan.speech };
      return (await call(chat, "/api/chat", { text: "say it" }, null, env)).body.speech[0].ticket;
    };
    /** Redeem a ticket the way Pages calls the route: a context whose `waitUntil` records
     *  what it was handed, with every timer frozen until the response has arrived (or
     *  REAL_MS of real time pass). The frozen timers are fired afterwards, so neither the
     *  route nor the handed write is left pending. */
    async function onPages(ticket, env) {
      const handed = [];
      const timers = [];
      let answered = false;
      globalThis.setTimeout = (fn) => { timers.push(fn); return timers.length; };
      globalThis.clearTimeout = (id) => { timers[id - 1] = null; };
      try {
        const route = speech.onRequestPost({ request: req("/api/speech", { ticket }), env, waitUntil: (p) => { handed.push(p); } });
        route.then(() => { answered = true; }, () => { answered = true; });
        const until = Date.now() + REAL_MS;
        while (!answered && Date.now() < until) await new Promise((r) => realSetTimeout(r, 5));
        const frozen = { answered, handed: handed.length, writePending: false, inflight: limits.__state().inflight.speech };
        if (handed.length) {
          let settled = false;
          handed[0].then(() => { settled = true; }, () => { settled = true; });
          await new Promise((r) => setImmediate(r));
          frozen.writePending = !settled;
        }
        for (let k = 0; k < timers.length; k++) if (timers[k]) { const f = timers[k]; timers[k] = null; f(); }
        const res = await route;
        await assertClean(res, "/api/speech on Pages");
        const body = JSON.parse(await res.clone().text());
        const audio = body.messages && body.messages[0] ? JSON.parse(body.messages[0].payload).audio : {};
        const outcomes = await Promise.all(handed.map((p) => Promise.resolve(p).then(() => "resolved", () => "rejected")));
        return { res, body, audio, bytes: Buffer.from(audio.buffer || "", "base64"), handed, outcomes, frozen };
      } finally {
        globalThis.setTimeout = realSetTimeout;
        globalThis.clearTimeout = realClearTimeout;
      }
    }

    // A put that HANGS FOR EVER: today's await would hold the answer for the whole deadline.
    fresh();
    await withCache(audioCache({ putHangs: true }), async () => {
      const t = await onPages(await ticketFor(FAST), FAST);
      ok(t.frozen.answered, "a hanging put does not delay the response: it arrived while the put still hung and no timer had fired");
      ok(t.frozen.handed === 1 && t.frozen.writePending, "…because context.waitUntil was handed the write, still pending when the answer left");
      deep([t.res.status, t.body.reason, t.bytes.equals(SERVED)], [200, null, true], "…and the answer carries the synthesized audio");
      deep([t.outcomes, cstats().timeouts, cstats().wrote], [["resolved"], 1, 0],
           "…and the handed write still ends at its own deadline, resolved, recorded as a timeout");
      eq(t.frozen.inflight, 0, "…and the concurrency slot was back before the write ended, not held for it");
    });

    // A put that REJECTS or THROWS: the answer is unaffected and the handed promise resolves.
    for (const [label, o] of [["REJECTS", { putRejects: true }], ["THROWS SYNCHRONOUSLY", { putThrowsSync: true }]]) {
      fresh();
      await withCache(audioCache(o), async () => {
        const t = await onPages(await ticketFor(FAST), FAST);
        deep([t.frozen.answered, t.res.status, t.body.reason, t.bytes.equals(SERVED)], [true, 200, null, true],
             `a put that ${label} never fails the response: 200 with the synthesized audio`);
        deep([t.handed.length, t.outcomes, cstats().errors, cstats().wrote], [1, ["resolved"], 1, 0],
             `…and the write handed to waitUntil RESOLVES (it never rejects into the runtime), recorded as an error`);
      });
    }

    // A working cache: the entry lands after the answer and decodes to exactly what was served.
    fresh();
    const colo = audioCache();
    await withCache(colo, async () => {
      const t = await onPages(await ticketFor(VOICED), VOICED);
      deep([t.frozen.answered, t.res.status, t.handed.length, t.outcomes], [true, 200, 1, ["resolved"]],
           "a working put is handed to waitUntil and resolves after the answer");
      const key = ttsEntries(colo)[0];
      const stored = key ? wav.pcmFromAudio(colo.bytes(key), { format: "wav" }) : { pcm: new Uint8Array(0) };
      ok(Buffer.from(stored.pcm).equals(t.bytes) && stored.sampleRate === t.audio.sample_rate && stored.channels === t.audio.channels,
         "…and the STORED entry still decodes to exactly the PCM, rate and channels served");
      deep([cstats().wrote, ttsCalls(colo, "put")], [1, 1], "…written once");

      // The next redemption of the same line is a hit and hands nothing to waitUntil.
      fresh();
      P.plan = { speech: { audio: wavOf(77, 8000) } };
      const hit = await onPages(await ticketFor(VOICED), VOICED);
      deep([hit.res.status, synths(), hit.handed.length, hit.bytes.equals(t.bytes)], [200, 0, 0, true],
           "…so the next redemption is a byte-identical hit with no write to hand off");
    });

    // A window refusal: the cap decides before the cache, so nothing is read, written or handed.
    fresh();
    const capped = audioCache();
    await withCache(capped, async () => {
      const limited = { ...VOICED, DEMO_SPEECH_PER_MIN: "2" };
      const ts = [await ticketFor(limited), await ticketFor(limited), await ticketFor(limited)];
      for (const t of ts.slice(0, 2)) await onPages(t, limited);
      const before = ttsOps(capped);
      const refused = await onPages(ts[2], limited);
      deep([`${refused.res.status} ${refused.body.reason}`, ttsOps(capped) - before, refused.handed.length], ["429 rate_limited", 0, 0],
           "a window refusal makes ZERO audio-cache calls and hands nothing to waitUntil");
    });
  }
}
