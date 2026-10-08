/* Harness for `sim/test_demo_proxy.mjs`: the stubbed gateway, request helpers, and the
 * no-leak sweep every section's responses go through. */
import { join } from "node:path";
import {
  repo, api, ledger, BASE, KEY, ORIGIN, post, leakSweep, jsonOf, fakeCache,
} from "../common.mjs";

export { execFileSync } from "node:child_process";
export { readFileSync } from "node:fs";
export { join } from "node:path";
export { repo, BASE, KEY, ORIGIN, fakeCache };

/** `sim/web` — the deployed asset tree, for the fixtures that read the REAL corpus. */
export const web0 = join(repo, "sim", "web");

export const { fails, C, ok, eq, deep } = ledger();

export const chat = await api("chat.js");
export const speech = await api("speech.js");
export const limits = await api("_lib", "limits.js");
export const wav = await api("_lib", "wav.js");
export const wire = await api("_lib", "wire.js");
export const hmac = await api("_lib", "hmac.js");
export const ttscache = await api("_lib", "ttscache.js");
export const wire2 = await api("_lib", "env.js");
export const env0 = await api("_lib", "envelope.js");
export const turnshape = await api("_lib", "turnshape.js");
export const prompt = await api("_lib", "prompt.js");

export const FULL = {
  DEMO_GATEWAY_BASE_URL: BASE,
  DEMO_GATEWAY_API_KEY: KEY,
  DEMO_CHAT_MODEL: "test-brain-model",
  DEMO_TTS_MODEL: "test-voice-model",
};

/** Every secret-shaped string that must never appear in a response, anywhere. */
export const FORBIDDEN = [KEY, BASE, "gw.invalid.test", "test-brain-model", "test-voice-model"];

/* --------------------------------------------------------------------------- *
 * The stubbed gateway
 * --------------------------------------------------------------------------- */
/** Every outbound request the routes built. Cleared in place, never reassigned. */
export const sent = [];
/** `P.plan` is what the stub answers next; `P.chatCalls` counts chat completions. */
export const P = { plan: {}, chatCalls: 0 };

export function pcmBytes(n) {
  const b = new Uint8Array(n * 2);
  for (let i = 0; i < n; i++) {
    b[i * 2] = i & 0xff;
    b[i * 2 + 1] = (i >> 8) & 0xff;
  }
  return b;
}

const chatReply = (content) => new Response(JSON.stringify({ choices: [{ message: { content } }] }), {
  status: 200, headers: { "Content-Type": "application/json" },
});

globalThis.fetch = async (url, opt) => {
  sent.push({ url: String(url), opt });
  const plan = P.plan;
  const isChat = String(url).endsWith("/chat/completions");
  const p = (isChat ? plan.chat : plan.speech) || {};
  /* A gateway that has never heard of `frequency_penalty` answers 400 to a body carrying
   * it — the failure the retry in `chat.js` exists for. Decided on the BODY, not a call
   * counter, so a retry that still sent the field cannot pass. */
  if (isChat && plan.rejectPenalties) {
    let sentPenalties = false;
    try { sentPenalties = "frequency_penalty" in JSON.parse(opt.body); } catch { /* not JSON */ }
    if (sentPenalties) return new Response('{"error":{"message":"unknown field"}}', { status: 400 });
  }
  if (p.throw) {
    const e = new Error("stub");
    e.name = p.throw;
    throw e;
  }
  // An explicit `status` or `body` means "answer exactly this" — including a 200 carrying
  // something the route did not ask for (an HTML page, JSON where audio was expected).
  if ((p.status && p.status !== 200) || p.body !== undefined) {
    return new Response(p.body === undefined ? "" : p.body, {
      status: p.status || 200,
      headers: p.headers || {},
    });
  }
  if (isChat) {
    P.chatCalls += 1;
    /* `contents`: a conversation's worth of answers in order, the last repeating for ever
     * — how a re-roll that lands on the same line again is expressed. */
    if (Array.isArray(p.contents) && p.contents.length) {
      return chatReply(p.contents.length > 1 ? p.contents.shift() : p.contents[0]);
    }
    /* The Nth chat call of a turn fails: a re-roll that does not come back must never
     * cost the visitor the reply they already had. */
    if (p.failSecondAt && P.chatCalls >= p.failSecondAt) {
      if (p.failSecondAt === P.chatCalls && p.secondThrows) {
        const e = new Error("stub"); e.name = p.secondThrows; throw e;
      }
      return new Response('{"error":{"message":"upstream is having a moment"}}', { status: 500 });
    }
    return chatReply(p.content === undefined ? "Hi there! Want to hear a joke?" : p.content);
  }
  const body = p.audio || wav.writeWav(pcmBytes(200), { sampleRate: 22050, channels: 1, bitsPerSample: 16 });
  // The real gateway labels a valid RIFF/WAVE body `audio/mpeg` (§2.2); the stub lies too.
  return new Response(body, { status: 200, headers: { "Content-Type": "audio/mpeg" } });
};

/* --------------------------------------------------------------------------- *
 * Harness
 * --------------------------------------------------------------------------- */
export const req = (path, body, headers) => post(path, body, headers);

/** Reset every counter and the per-isolate spent-ticket set. The audio cache's COUNTERS
 *  reset but no store does: that is the isolate boundary §15b/§16's hit tests rely on. */
export function fresh() {
  limits.__reset();
  speech.__resetSpent();
  ttscache.__resetTtsCache();
  sent.length = 0;
  P.plan = {};
  P.chatCalls = 0;
}

/** The §4.2 sweep (`common.mjs::leakSweep`), run on EVERY response this suite produces. */
export async function assertClean(res, label) {
  C.sweeps += 1;
  await leakSweep(ok, res, FORBIDDEN, label, { stripTopic: true });
}

/** POST to a route, sweep the response, and hand back `{res, body}`. */
export async function call(route, path, payload, headers, env) {
  const res = await route.onRequestPost({ request: req(path, payload, headers), env: env || FULL });
  await assertClean(res, path + " " + JSON.stringify(payload).slice(0, 60));
  return { res, body: await jsonOf(res) };
}

export const upstreamCalls = () => limits.__state().stats.upstreamCalls;

/* ---- the Cache API tier (§15) ---- */
export const cacheStats = () => limits.__state().stats.cache;
/** One admission straight at `admit()` with a cache injected. `cache: null` is "there is
 *  no cache here", NOT the same as omitting the key. */
export const admitWith = (cfg, cache, ip, route, nowS) =>
  limits.admit({
    request: req("/api/" + (route || "chat"), { text: "x" }, { "CF-Connecting-IP": ip || "203.0.113.9" }),
    cfg,
    route: route || "chat",
    cache,
    nowS,
  });
