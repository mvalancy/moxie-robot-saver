/* Shared harness for the Cloudflare-edge node suites (`sim/test_demo_proxy.mjs`,
 * `sim/test_turnstile.mjs`, `sim/test_demo_ears.mjs`, `sim/test_demo_tickets.mjs`).
 *
 * The Pages Functions are plain ES modules, so every suite imports the real handlers and
 * calls them with a synthetic `Request` and a plain object as `context.env`, with `fetch`
 * stubbed. Nothing here needs a Cloudflare account, a gateway key or the network.
 */
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

export const repo = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");

/** Import a module under `functions/api/`, e.g. `api("_lib", "limits.js")`. */
export const api = (...parts) => import(join(repo, "functions", "api", ...parts));

/** One assertion ledger per suite. `C.asserts` counts every check made. */
export function ledger() {
  const fails = [];
  const C = { asserts: 0, sweeps: 0 };
  const ok = (c, m) => { C.asserts++; if (!c) fails.push(m); };
  const eq = (a, b, m) => ok(a === b, `${m} — got ${JSON.stringify(a)}, want ${JSON.stringify(b)}`);
  const deep = (a, b, m) => eq(JSON.stringify(a), JSON.stringify(b), m);
  return { fails, C, ok, eq, deep };
}

/* The fake deployment. The host is RFC 6761 `.invalid` (unresolvable, so a stub that
 * leaked a real request could reach nothing) and the key is shaped so the repo's
 * pre-commit secret grep cannot mistake it for a real one. */
export const BASE = "https://gw.invalid.test/v1";
export const KEY = "sk-testonly-abcdefghijklmnopqrstuv";
export const ORIGIN = "https://demo.invalid.test";
export const GATEWAY = Object.freeze({
  DEMO_GATEWAY_BASE_URL: BASE,
  DEMO_GATEWAY_API_KEY: KEY,
  DEMO_CHAT_MODEL: "test-brain-model",
});

/** The headers a real same-origin browser POST carries. */
export const BROWSER_HEADERS = Object.freeze({
  Origin: ORIGIN,
  "Sec-Fetch-Site": "same-origin",
  "CF-Connecting-IP": "203.0.113.9",
});

/** A same-origin POST to `path`; a non-string body is JSON-encoded. */
export function post(path, body, headers, contentType = "application/json") {
  return new Request(ORIGIN + path, {
    method: "POST",
    headers: Object.assign({ "Content-Type": contentType }, BROWSER_HEADERS, headers || {}),
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
}

/** `{text, headerText}` of a response, for the no-leak sweeps. */
export async function responseText(res) {
  const text = await res.clone().text();
  let headerText = "";
  for (const [k, v] of res.headers.entries()) headerText += k + ": " + v + "\n";
  return { text, headerText };
}

/**
 * The §4.2 no-leak sweep shared by the proxy and ears suites: every forbidden string is
 * absent from the body, every header, and any base64 audio buffer DECODED (`includes`
 * cannot see inside base64, which once hid a raw-body passthrough in `/api/speech`).
 * Missing pieces (non-JSON body, no audio) are skipped, never failures. `stripTopic`
 * exempts the MQTT topic string from the no-URL check.
 */
export async function leakSweep(ok, res, forbidden, label, { stripTopic = false } = {}) {
  const { text, headerText } = await responseText(res);
  for (const secret of forbidden) {
    ok(!text.includes(secret), `${label}: the response BODY leaked ${JSON.stringify(secret.slice(0, 12))}…`);
    ok(!headerText.includes(secret), `${label}: a response HEADER leaked ${JSON.stringify(secret.slice(0, 12))}…`);
  }
  ok(!/\bBearer\b/i.test(text), `${label}: the body contains the word Bearer`);
  ok(!/https?:\/\//.test(stripTopic ? text.replace(/"topic":"[^"]*"/g, "") : text),
     `${label}: the body contains a URL`);
  let envelope = null;
  try { envelope = JSON.parse(text); } catch {}
  const msgs = envelope && Array.isArray(envelope.messages) ? envelope.messages : [];
  for (const m of msgs) {
    let payload = null;
    try { payload = JSON.parse(m && m.payload); } catch {}
    const b64 = payload && payload.audio && typeof payload.audio.buffer === "string" ? payload.audio.buffer : "";
    if (!b64) continue;
    let decoded = "";
    try { decoded = Buffer.from(b64, "base64").toString("latin1"); } catch {}
    for (const secret of forbidden) {
      ok(!decoded.includes(secret),
         `${label}: the AUDIO BUFFER DECODES to bytes containing ${JSON.stringify(secret.slice(0, 12))}…`);
    }
    ok(!/https?:\/\//.test(decoded), `${label}: the audio buffer decodes to something carrying a URL`);
  }
}

/** The response body as JSON, or `null`. */
export async function jsonOf(res) {
  try { return JSON.parse(await res.clone().text()); } catch { return null; }
}

/** A 16 kHz mono 16-bit RIFF/WAVE of `ms` milliseconds of silence — the shape
 *  `sim/web/mic.js::encodeWav` produces, which the route sniffs rather than trusting
 *  the Content-Type. */
export function wavBytes(ms, rate = 16000) {
  const samples = Math.round((rate * ms) / 1000);
  const bytes = new Uint8Array(44 + samples * 2);
  const view = new DataView(bytes.buffer);
  const wr = (off, str) => { for (let i = 0; i < str.length; i++) view.setUint8(off + i, str.charCodeAt(i)); };
  wr(0, "RIFF"); view.setUint32(4, 36 + samples * 2, true); wr(8, "WAVE");
  wr(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  wr(36, "data"); view.setUint32(40, samples * 2, true);
  return bytes;
}

/** Import each section module in order; each runs its assertions at top level. */
export async function runSections(dirUrl, names) {
  for (const n of names) await import(new URL(n, dirUrl));
}

/**
 * A fake `caches.default` (`match`/`put` only) that logs what it was asked, for the Cache
 * API tier of `_lib/limits.js` (node has no `caches`). The failure switches are distinct
 * SHAPES — sync throw, rejection, a promise that never settles — because a naive
 * `Promise.resolve(x()).catch()` misses the first. Options:
 *   match/put{ThrowsSync,Rejects,Hangs}, putStoresThenHangs (the write that lands and then
 *   never answers: what makes "retry the unpublished units" a double charge),
 *   bodyOverride, only (aim every switch at keys containing this substring),
 *   unitsCount (answer every `/units/` entry with this count, whatever bucket is asked —
 *   so a suite never reads the clock), seedMaxAge (default max-age for `seed`, 60).
 */
export function fakeCache(opts) {
  const o = opts || {};
  const store = new Map();
  const log = { match: 0, put: 0, keys: [], puts: [] };
  const hang = () => new Promise(() => {});
  const json = (body, maxAge, ageS) => {
    const h = { "Content-Type": "application/json", "Cache-Control": "max-age=" + maxAge };
    if (ageS !== undefined) h.Age = String(ageS);
    return Promise.resolve(new Response(body, { headers: h }));
  };
  return {
    log,
    store,
    /** Pre-load an entry as another isolate would have. A number seeds `{n}`; `ageS` past
     *  `maxAge` is the stale entry a real cache would never serve. */
    seed(key, body, ageS, maxAge) {
      store.set(String(key), {
        body: JSON.stringify(typeof body === "number" ? { n: body } : body),
        maxAge: maxAge === undefined ? (o.seedMaxAge === undefined ? 60 : o.seedMaxAge) : maxAge,
        ageS,
      });
      return this;
    },
    body(key) {
      const e = store.get(String(key));
      if (!e) return null;
      try { return JSON.parse(e.body); } catch { return null; }
    },
    count(key) {
      const b = this.body(key);
      return b ? b.n : null;
    },
    match(key) {
      log.match += 1;
      log.keys.push(String(key));
      const e = store.get(String(key));
      if (o.only && String(key).indexOf(o.only) < 0) return e ? json(e.body, e.maxAge) : Promise.resolve(undefined);
      if (o.matchThrowsSync) throw new Error("match threw synchronously");
      if (o.matchHangs) return hang();
      if (o.matchRejects) return Promise.reject(new Error("match rejected"));
      if (o.unitsCount !== undefined && String(key).indexOf("/__moxie/rl/units/") >= 0) {
        return json(JSON.stringify({ n: o.unitsCount }), 3600);
      }
      if (!e) return Promise.resolve(undefined);
      return json(o.bodyOverride === undefined ? e.body : o.bodyOverride, e.maxAge, e.ageS);
    },
    put(key, res) {
      log.put += 1;
      log.puts.push(String(key));
      if (o.only && String(key).indexOf(o.only) < 0) return Promise.resolve();
      if (o.putThrowsSync) throw new Error("put threw synchronously");
      if (o.putHangs) return hang();
      if (o.putRejects) return Promise.reject(new Error("put rejected"));
      const write = (async () => {
        const body = await res.text();
        const cc = /max-age=(\d+)/.exec(res.headers.get("Cache-Control") || "");
        store.set(String(key), { body, maxAge: cc ? Number(cc[1]) : 0 });
      })();
      if (o.putStoresThenHangs) return write.then(() => hang());
      return write;
    },
  };
}
