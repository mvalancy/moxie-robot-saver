/* test_demo_ears.mjs — the ears, both halves, under bare node.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §3.2 (`POST /api/transcribe`), §4.1
 * (`DEMO_MAX_AUDIO_BYTES`, `DEMO_MIN_AUDIO_BYTES`, the per-IP windows, the timeout, and
 * the paragraph that says a byte cap is NOT a duration cap), §4.2 (what the browser may
 * know), §4.3 (the origin pin), §4.5 (the status table), §6 (never a dead button),
 * §10 assumptions 15 and 16.
 *
 * TWO HALVES, ONE FILE, because they are one contract:
 *
 *   PART A — `functions/api/transcribe.js`, imported as an ES module and called with a
 *   synthetic `Request` and a plain object as `context.env`, with `fetch` stubbed. No
 *   Cloudflare account, no network, and NO GATEWAY KEY — as with every other test here,
 *   and it must stay that way.
 *
 *   PART B — the REAL `sim/web/mic.js`, evaluated as source under a stubbed window with a
 *   VIRTUAL CLOCK and a FAKE RECORDER. **No live microphone is ever opened**: every
 *   assertion is on recorded state (`window.moxieMic.stats()`, the fetch log, the fake
 *   recorder's own call log), never on a sampled device (playbook rule 11). That is the
 *   only way to prove the 15-second hard stop actually stops a recorder, which is the
 *   single control that bounds what the ears can cost.
 *
 * THE THREE PROPERTIES THIS FILE EXISTS TO PROVE:
 *
 *   1. **THE KEY AND THE GATEWAY URL NEVER APPEAR IN A RESPONSE.** Every response object
 *      produced anywhere in Part A — success, refusal, timeout, a hostile upstream error
 *      body naming the model and a key prefix — is swept for both, in the BODY and in
 *      EVERY HEADER, by `assertClean()`.
 *   2. **A REFUSAL MAKES ZERO UPSTREAM CALLS**, recorded by
 *      `_lib/limits.js::noteUpstreamCall()` rather than inferred from a stub that may or
 *      may not have been reached.
 *   3. **THE PAGE NEVER GOES DEAD.** Every refusal reason a visitor can provoke ends in a
 *      scripted child line and an honest status string, never an error dialog and never a
 *      button that does nothing.
 *
 *   node sim/test_demo_ears.mjs
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..");

const fails = [];
let asserts = 0;
const ok = (c, m) => { asserts++; if (!c) fails.push(m); };
const eq = (a, b, m) => ok(a === b, `${m} — got ${JSON.stringify(a)}, want ${JSON.stringify(b)}`);
const deep = (a, b, m) => eq(JSON.stringify(a), JSON.stringify(b), m);

/* =========================================================================== *
 * PART A — functions/api/transcribe.js
 * =========================================================================== */

const route = await import(join(repo, "functions", "api", "transcribe.js"));
const health = await import(join(repo, "functions", "api", "health.js"));
const limits = await import(join(repo, "functions", "api", "_lib", "limits.js"));
const envlib = await import(join(repo, "functions", "api", "_lib", "envelope.js"));
const envmod = await import(join(repo, "functions", "api", "_lib", "env.js"));
const wavlib = await import(join(repo, "functions", "api", "_lib", "wav.js"));

/* The fake deployment. These strings exist only inside this test: the host is
 * `.invalid.test` (RFC 6761 reserved and unresolvable, so a bug that actually fired a
 * request could not reach anything) and the key is shaped so the repo's own pre-commit
 * secret grep cannot mistake it for a real one. */
const BASE = "https://gw.invalid.test/v1";
const KEY = "sk-testonly-abcdefghijklmnopqrstuv";
const ORIGIN = "https://demo.invalid.test";
const FULL = {
  DEMO_GATEWAY_BASE_URL: BASE,
  DEMO_GATEWAY_API_KEY: KEY,
  DEMO_CHAT_MODEL: "test-brain-model",
  DEMO_STT_MODEL: "test-ears-model",
};

/** Every secret-shaped string that must never appear in a response, anywhere. */
const FORBIDDEN = [KEY, BASE, "gw.invalid.test", "test-brain-model", "test-ears-model"];

let sent = [];
let plan = {};

globalThis.fetch = async (url, opt) => {
  sent.push({ url: String(url), opt });
  if (plan.throw) {
    const e = new Error("stub");
    e.name = plan.throw;
    throw e;
  }
  if (plan.body !== undefined || plan.status) {
    return new Response(plan.body === undefined ? "" : plan.body, {
      status: plan.status || 200,
      headers: plan.headers || { "Content-Type": "application/json" },
    });
  }
  const text = plan.text === undefined ? "hi moxie, tell me a joke" : plan.text;
  return new Response(JSON.stringify({ text }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
};

/** A byte string that sniffs as a real container. `kind` picks the magic number, which is
 *  the whole point of the sniffer under test. */
function clip(n, kind) {
  const b = new Uint8Array(Math.max(n, 16));
  const magic = {
    webm: [0x1a, 0x45, 0xdf, 0xa3],
    ogg: [0x4f, 0x67, 0x67, 0x53],
    flac: [0x66, 0x4c, 0x61, 0x43],
    mp3: [0x49, 0x44, 0x33, 0x04],               // "ID3"
    junk: [0x7b, 0x22, 0x65, 0x72],              // `{"er` — a JSON body, not audio
  }[kind || "wav"];
  if (!kind || kind === "wav") {
    b.set([0x52, 0x49, 0x46, 0x46], 0);          // "RIFF"
    b.set([0x57, 0x41, 0x56, 0x45], 8);          // "WAVE"
  } else if (kind === "mp4") {
    b.set([0x66, 0x74, 0x79, 0x70], 4);          // "ftyp" at offset 4
  } else {
    b.set(magic, 0);
  }
  for (let i = 12; i < b.length; i++) b[i] = i & 0xff;
  return b;
}

function req(bytes, headers) {
  const h = Object.assign(
    {
      "Content-Type": "audio/wav",
      Origin: ORIGIN,
      "Sec-Fetch-Site": "same-origin",
      "CF-Connecting-IP": "203.0.113.9",
    },
    headers || {},
  );
  for (const k of Object.keys(h)) if (h[k] === undefined) delete h[k];
  return new Request(ORIGIN + "/api/transcribe", { method: "POST", headers: h, body: bytes });
}

function fresh() {
  limits.__reset();
  sent = [];
  plan = {};
}

let sweeps = 0;
async function assertClean(res, label) {
  sweeps += 1;
  const text = await res.clone().text();
  let headerText = "";
  for (const [k, v] of res.headers.entries()) headerText += k + ": " + v + "\n";
  for (const secret of FORBIDDEN) {
    ok(!text.includes(secret), `${label}: the response BODY leaked ${JSON.stringify(secret.slice(0, 12))}…`);
    ok(!headerText.includes(secret), `${label}: a response HEADER leaked ${JSON.stringify(secret.slice(0, 12))}…`);
  }
  ok(!/\bBearer\b/i.test(text), `${label}: the body contains the word Bearer`);
  ok(!/https?:\/\//.test(text), `${label}: the body contains a URL`);

  // ---- AND THE SAME SWEEP OVER THE DECODED AUDIO. ---------------------------
  // `text.includes(secret)` cannot see inside base64, and `messages[0].payload.audio.buffer`
  // is ~175 KB of it. That blind spot is not hypothetical: it is how a raw-body passthrough
  // in `/api/speech` survived every sweep in this file reporting CLEAN while returning an
  // upstream 200 body verbatim to the caller (fixed 2026-09-03, `_lib/wav.js`). A sweep that
  // stops at the encoding boundary is a sweep that proves the encoding, not the secrecy.
  // Defensive throughout: a body that is not JSON, a payload that is not JSON, a message
  // with no audio and an absent buffer are all NOT failures — most responses here have no
  // audio at all, and this must never turn a refusal into a crash.
  let __env = null;
  try { __env = JSON.parse(text); } catch {}
  const __msgs = __env && Array.isArray(__env.messages) ? __env.messages : [];
  for (const m of __msgs) {
    let payload = null;
    try { payload = JSON.parse(m && m.payload); } catch {}
    const b64 = payload && payload.audio && typeof payload.audio.buffer === "string" ? payload.audio.buffer : "";
    if (!b64) continue;
    let decoded = "";
    try { decoded = Buffer.from(b64, "base64").toString("latin1"); } catch {}
    for (const secret of FORBIDDEN) {
      ok(!decoded.includes(secret),
         `${label}: the AUDIO BUFFER DECODES to bytes containing ${JSON.stringify(secret.slice(0, 12))}…`);
    }
    ok(!/https?:\/\//.test(decoded), `${label}: the audio buffer decodes to something carrying a URL`);
  }
}

async function call(bytes, headers, env, label) {
  const res = await route.onRequestPost({ request: req(bytes, headers), env: env || FULL });
  await assertClean(res, label || "transcribe");
  let body = null;
  try { body = JSON.parse(await res.clone().text()); } catch {}
  return { res, body };
}

const upstreamCalls = () => limits.__state().stats.upstreamCalls;

/* --------------------------------------------------------------------------- *
 * A1. The fail-safe default (C5) and the unset-model path: ZERO upstream calls
 * --------------------------------------------------------------------------- */
{
  fresh();
  const noEars = { ...FULL };
  delete noEars.DEMO_STT_MODEL;
  const cases = [
    ["no variables at all", {}],
    ["a base URL but no key", { DEMO_GATEWAY_BASE_URL: BASE }],
    ["a key but no chat model", { DEMO_GATEWAY_BASE_URL: BASE, DEMO_GATEWAY_API_KEY: KEY }],
    ["the kill switch off", { ...FULL, DEMO_ENABLED: "0" }],
    ["a configured gateway with NO DEMO_STT_MODEL", noEars],
    ["half a Cloudflare Access token", { ...FULL, DEMO_GATEWAY_ACCESS_CLIENT_ID: "id-only.access" }],
  ];
  for (const [label, env] of cases) {
    const { res, body } = await call(clip(9000), null, env, label);
    eq(res.status, 503, `${label}: 503`);
    eq(body.reason, "gateway_not_configured", `${label}: reason`);
    eq(body.ears, false, `${label}: ears false — the page is never offered a mic it cannot serve`);
    eq(body.transcript, "", `${label}: no transcript`);
  }
  eq(upstreamCalls(), 0, "an unconfigured or ear-less deployment makes ZERO upstream calls");
  eq(sent.length, 0, "…and does not even build an upstream request");

  // /api/health agrees, from the same config — the probe and the route cannot disagree.
  const hres = await health.onRequestGet({ env: noEars });
  const hbody = JSON.parse(await hres.clone().text());
  eq(hbody.ears, false, "/api/health reports ears:false with no DEMO_STT_MODEL");
  const hres2 = await health.onRequestGet({ env: FULL });
  const hbody2 = JSON.parse(await hres2.clone().text());
  eq(hbody2.ears, true, "/api/health reports ears:true once DEMO_STT_MODEL is set");
  eq(hbody2.limits.max_record_ms, 15000, "…and publishes the 15 s recording cap to the page");
  eq(upstreamCalls(), 0, "the health probe never calls the gateway either");
}

/* --------------------------------------------------------------------------- *
 * A2. §4.1 — both byte caps, and the FREE floor
 * --------------------------------------------------------------------------- */
{
  fresh();
  // Below DEMO_MIN_AUDIO_BYTES: no audio -> no request, no cost, no latency.
  for (const n of [0, 1, 799, 1999]) {
    const { res, body } = await call(clip(n), null, FULL, `a ${n}-byte clip`);
    eq(res.status, 400, `${n} bytes: 400`);
    eq(body.reason, "too_short", `${n} bytes: too_short`);
  }
  eq(upstreamCalls(), 0, "A CLIP UNDER THE FLOOR MAKES NO UPSTREAM CALL AT ALL (§4.1, stt.py:194-197)");
  eq(sent.length, 0, "…and builds no upstream request");

  // Exactly at the floor is allowed: the cap is a floor, not a gap.
  fresh();
  const at = await call(clip(2000), null, FULL, "exactly 2000 bytes");
  eq(at.res.status, 200, "exactly DEMO_MIN_AUDIO_BYTES is accepted");
  eq(upstreamCalls(), 1, "…and is the one call");

  // Above DEMO_MAX_AUDIO_BYTES, by the real byte count.
  fresh();
  const big = await call(clip(500001), null, FULL, "a 500001-byte clip");
  eq(big.res.status, 400, "over the byte cap: 400");
  eq(big.body.reason, "too_long", "over the byte cap: too_long");
  eq(upstreamCalls(), 0, "an oversized clip makes no upstream call");

  // …and by the DECLARED Content-Length, refused UNREAD.
  fresh();
  const declared = await call(clip(3000), { "Content-Length": "900000" }, FULL, "an over-declared clip");
  eq(declared.body.reason, "too_long", "a declared Content-Length over the cap is refused");
  eq(upstreamCalls(), 0, "…without reading the body or calling upstream");

  // An override moves both caps, so a fork can tighten them.
  fresh();
  const tight = { ...FULL, DEMO_MIN_AUDIO_BYTES: "10000", DEMO_MAX_AUDIO_BYTES: "20000" };
  eq((await call(clip(9000), null, tight, "under a raised floor")).body.reason, "too_short",
     "DEMO_MIN_AUDIO_BYTES is env-overridable");
  eq((await call(clip(25000), null, tight, "over a lowered ceiling")).body.reason, "too_long",
     "DEMO_MAX_AUDIO_BYTES is env-overridable");
  eq(upstreamCalls(), 0, "neither override path calls upstream");
}

/* --------------------------------------------------------------------------- *
 * A3. §4.3 — the origin pin, and zero upstream calls behind it
 * --------------------------------------------------------------------------- */
{
  fresh();
  const cases = [
    ["a foreign Origin", { Origin: "https://evil.example", "Sec-Fetch-Site": "cross-site" }],
    ["a foreign Origin with no fetch metadata", { Origin: "https://evil.example", "Sec-Fetch-Site": undefined }],
    ["cross-site fetch metadata", { "Sec-Fetch-Site": "cross-site" }],
    ["no Origin and no fetch metadata", { Origin: undefined, "Sec-Fetch-Site": undefined }],
    ["a foreign Referer", { Origin: undefined, Referer: "https://evil.example/x", "Sec-Fetch-Site": undefined }],
  ];
  for (const [label, headers] of cases) {
    const { res, body } = await call(clip(9000), headers, FULL, label);
    eq(res.status, 403, `${label}: 403`);
    eq(body.reason, "forbidden_origin", `${label}: forbidden_origin`);
  }
  eq(upstreamCalls(), 0, "A4: a forged or foreign origin makes ZERO upstream calls");
  eq(sent.length, 0, "…and builds no upstream request");
}

/* --------------------------------------------------------------------------- *
 * A4. §4.1 — the per-IP windows (10/min, 60/hour), and the budget
 * --------------------------------------------------------------------------- */
{
  fresh();
  let last = null;
  for (let i = 0; i < 10; i++) {
    last = await call(clip(4000), null, FULL, `turn ${i + 1}`);
    eq(last.res.status, 200, `turn ${i + 1} of 10 is inside DEMO_STT_PER_MIN`);
  }
  eq(upstreamCalls(), 10, "ten admitted turns are ten upstream calls");
  const refused = await call(clip(4000), null, FULL, "the eleventh turn");
  eq(refused.res.status, 429, "the ELEVENTH turn in a minute is 429 (DEMO_STT_PER_MIN = 10)");
  eq(refused.body.reason, "rate_limited", "…with reason rate_limited");
  ok(Number(refused.res.headers.get("Retry-After")) > 0, "…and a Retry-After");
  eq(upstreamCalls(), 10, "a rate-limited turn makes NO upstream call");
  ok(refused.res.headers.get("X-RateLimit-Limit") !== null, "X-RateLimit-Limit rides the refusal");

  // A different IP has its own window.
  const other = await call(clip(4000), { "CF-Connecting-IP": "198.51.100.4" }, FULL, "another visitor");
  eq(other.res.status, 200, "another IP is not caught by the first one's window");

  // The hour window is tighter than 10/min x 60.
  fresh();
  const perHour = { ...FULL, DEMO_STT_PER_MIN: "100", DEMO_STT_PER_HOUR: "3" };
  for (let i = 0; i < 3; i++) await call(clip(4000), null, perHour, `hour turn ${i}`);
  const hourly = await call(clip(4000), null, perHour, "the fourth in an hour");
  eq(hourly.body.reason, "rate_limited", "DEMO_STT_PER_HOUR is enforced too");
  eq(upstreamCalls(), 3, "…and the refused one costs nothing");

  // The unit budget. A transcribe call is 2 units (§4.1's denomination).
  fresh();
  const cfg = envmod.readConfig(FULL);
  limits.__exhaustBudget(cfg);
  const broke = await call(clip(4000), null, FULL, "over budget");
  eq(broke.res.status, 503, "an exhausted budget is 503");
  eq(broke.body.reason, "budget_exhausted", "…with reason budget_exhausted");
  ok(Number(broke.res.headers.get("Retry-After")) > 0, "…and a Retry-After to the window reset");
  eq(upstreamCalls(), 0, "an over-budget turn makes NO upstream call");
}

/* --------------------------------------------------------------------------- *
 * A5. §4.1 — the timeout is OURS, via AbortSignal
 * --------------------------------------------------------------------------- */
{
  fresh();
  plan = { throw: "TimeoutError" };
  const t = await call(clip(4000), null, FULL, "an upstream that never answers");
  eq(t.res.status, 504, "our own AbortSignal.timeout is a 504");
  eq(t.body.reason, "timeout", "…with reason timeout");
  eq(t.res.headers.get("Retry-After"), "10", "…and §4.5's Retry-After of 10");
  eq(upstreamCalls(), 1, "the call was made — this is a timeout, not a refusal");

  // The signal really is attached, and really carries DEMO_STT_TIMEOUT_MS.
  fresh();
  let seenMs = null;
  const realTimeout = AbortSignal.timeout;
  AbortSignal.timeout = (ms) => { seenMs = ms; return realTimeout.call(AbortSignal, ms); };
  await call(clip(4000), null, FULL, "the default timeout");
  eq(seenMs, 12000, "DEMO_STT_TIMEOUT_MS defaults to 12 000 ms");
  await call(clip(4000), null, { ...FULL, DEMO_STT_TIMEOUT_MS: "5000" }, "an overridden timeout");
  eq(seenMs, 5000, "…and is env-overridable");
  ok(sent.every((s) => s.opt && s.opt.signal), "every upstream call carries an AbortSignal");
  AbortSignal.timeout = realTimeout;

  // A plain network failure is upstream_down, not a timeout, and never a bare 500.
  fresh();
  plan = { throw: "TypeError" };
  const down = await call(clip(4000), null, FULL, "an unreachable gateway");
  eq(down.res.status, 503, "an unreachable gateway is 503");
  eq(down.body.reason, "upstream_down", "…with reason upstream_down");
}

/* --------------------------------------------------------------------------- *
 * A6. A hostile upstream: a degradable reason, never a 502 and never a leak
 * --------------------------------------------------------------------------- */
{
  // The body a real OpenAI-compatible gateway sends on a refusal. It names the model, the
  // org and a key prefix — every one of which `assertClean` then proves absent.
  const hostile = JSON.stringify({
    error: {
      message: `Invalid model name passed in model=test-ears-model to ${BASE}`,
      type: "invalid_request_error",
      param: null,
      code: "model_not_found",
      key: KEY,
    },
  });

  const table = [
    // upstream status, our status, our reason, why
    [400, 400, "bad_request", "a gateway refusing THESE BYTES is per-turn, not a sick deployment"],
    [415, 400, "bad_request", "an unsupported media type is the shape assumption 15 would take"],
    [422, 400, "bad_request", "an unprocessable body is per-turn too"],
    [413, 400, "too_long", "the gateway's own size cap maps onto ours"],
    [401, 503, "upstream_down", "a REVOKED KEY is an operator problem and must degrade the page"],
    [403, 503, "upstream_down", "…so is an unauthorised one"],
    [500, 503, "upstream_down", "a 5xx is the gateway being ill"],
    [502, 503, "upstream_down", "…as is a bad gateway"],
    [503, 503, "upstream_down", "…and an unavailable one"],
  ];
  for (const [upstream, status, reason, why] of table) {
    fresh();
    plan = { status: upstream, body: hostile };
    const { res, body } = await call(clip(4000), null, FULL, `upstream ${upstream}`);
    eq(res.status, status, `upstream ${upstream} -> ${status} (${why})`);
    eq(body.reason, reason, `upstream ${upstream} -> ${reason}`);
    ok(res.status !== 502, "NEVER a bare 502");
    ok(res.status !== 500, "NEVER a bare 500");
    eq(body.transcript, "", "a refusal carries no transcript");
    ok(envlib.REASONS.includes(body.reason), "the reason is in the CLOSED set §3.2 defines");
  }

  // A 400 does NOT degrade the page; a 503 does. That is the whole point of the split, and
  // §4.5's own table says `bad_request` "does not change mode".
  eq(envlib.STATUS_FOR.bad_request, 400, "§4.5: bad_request is a 400");
  eq(envlib.STATUS_FOR.upstream_down, 503, "§4.5: upstream_down is a 503");
  deep(["bad_request", "too_long", "too_short"].map((r) => envlib.RETRY_AFTER_FOR[r]), [null, null, null],
       "an input-shaped refusal sends no Retry-After — there is nothing to wait for");

  // Upstream 429 becomes our 429, with a SANITIZED Retry-After.
  fresh();
  plan = { status: 429, body: hostile, headers: { "Retry-After": "999999" } };
  const r429 = await call(clip(4000), null, FULL, "an upstream 429");
  eq(r429.res.status, 429, "an upstream 429 is our 429");
  eq(r429.body.reason, "rate_limited", "…with reason rate_limited");
  eq(r429.res.headers.get("Retry-After"), "300", "…and a Retry-After clamped to 300 s, not echoed");

  // A 200 that is not JSON, and a 200 that is JSON but not a transcript.
  for (const [label, p, reason] of [
    ["a 200 carrying HTML (a Cloudflare Access login page)",
     { status: 200, body: "<!doctype html><html><body>Sign in</body></html>",
       headers: { "Content-Type": "text/html" } },
     "gateway_unreachable_or_gated"],
    ["a 200 carrying garbage", { status: 200, body: "not json at all" }, "upstream_down"],
    ["a 200 carrying JSON with no text field", { status: 200, body: JSON.stringify({ usage: null }) },
     "upstream_down"],
    ["a 200 carrying a non-string text", { status: 200, body: JSON.stringify({ text: 42 }) },
     "upstream_down"],
    ["a 200 carrying an empty body", { status: 200, body: "" }, "upstream_down"],
  ]) {
    fresh();
    plan = p;
    const { res, body } = await call(clip(4000), null, FULL, label);
    eq(body.reason, reason, `${label} -> ${reason}`);
    eq(res.status, 503, `${label}: 503, never a 200 with an empty string`);
  }
}

/* --------------------------------------------------------------------------- *
 * A7. Sniff the bytes, never the Content-Type
 * --------------------------------------------------------------------------- */
{
  fresh();
  // Every container a MediaRecorder can emit, identified from the BYTES even when the
  // declared type is a lie.
  for (const [kind, ext, mime] of [
    ["webm", "webm", "audio/webm"],
    ["ogg", "ogg", "audio/ogg"],
    ["wav", "wav", "audio/wav"],
    ["mp4", "mp4", "audio/mp4"],
    ["flac", "flac", "audio/flac"],
  ]) {
    const k = route.audioKind(clip(4000, kind), "application/octet-stream");
    ok(k && k.sniffed, `${kind} is identified from its magic number, not its Content-Type`);
    eq(k.ext, ext, `${kind} -> .${ext}`);
    eq(k.mime, mime, `${kind} -> ${mime}`);
  }

  // A declared type is a SECOND opinion, only against the same allowlist.
  const raw = new Uint8Array(4000);            // headerless: no magic to find
  eq(route.audioKind(raw, "audio/webm").ext, "webm", "an unrecognised body falls back to a declared audio type");
  eq(route.audioKind(raw, "audio/webm").sniffed, false, "…and says it was not sniffed");
  eq(route.audioKind(raw, "audio/x-m4a").ext, "mp4", "an m4a alias maps to mp4");
  eq(route.audioKind(raw, "video/webm").ext, "webm",
     "video/webm is allowed too — some Chrome builds label a MediaRecorder blob that way");
  eq(route.audioKind(raw, "text/html"), null, "an HTML content type is not audio");
  eq(route.audioKind(raw, "application/json"), null, "a JSON content type is not audio");
  eq(route.audioKind(raw, null), null, "no magic and no usable type is not audio");

  // …and the route refuses it, for free.
  fresh();
  const junk = await call(clip(4000, "junk"), { "Content-Type": "application/json" }, FULL, "a JSON body");
  eq(junk.res.status, 400, "a body that is not audio is a 400");
  eq(junk.body.reason, "bad_request", "…with reason bad_request");
  eq(upstreamCalls(), 0, "500 KB OF NON-AUDIO COSTS NOTHING — zero upstream calls");
}

/* --------------------------------------------------------------------------- *
 * A7b. §10 assumption 15 — the container allowlist, and why it is not optional
 * --------------------------------------------------------------------------- *
 * Settled live on 2026-09-03 (`sim/tools/probe_demo_gateway.mjs`, four containers, one
 * utterance): the gateway transcribes 16 kHz mono RIFF/WAVE word-perfect and answers
 * **HTTP 500** to webm/Opus, ogg/Opus and mp4/AAC alike.
 *
 * 500 maps to `upstream_down`, which is a 503, and `mode.js` degrades the WHOLE PAGE on a
 * 503. So without this allowlist one press of the microphone would take the brain and the
 * voice down with the ears — after paying for the call. That is what these assertions
 * exist to prevent regressing, and the first one is the one that matters.
 * --------------------------------------------------------------------------- */
{
  fresh();
  for (const kind of ["webm", "ogg", "mp4", "mp3", "flac"]) {
    const { res, body } = await call(clip(4000, kind), null, FULL, `a ${kind} clip`);
    eq(res.status, 400, `${kind} is refused with a 400 — PER-TURN, so the page stays live`);
    eq(body.reason, "bad_request", `${kind}: bad_request`);
    ok(res.status !== 503, `${kind} MUST NOT be a 503: that would degrade the brain and the voice too`);
  }
  eq(upstreamCalls(), 0,
     "A CONTAINER THE GATEWAY REJECTS NEVER BECOMES A PAID 500 (assumption 15, settled 2026-09-03)");
  eq(sent.length, 0, "…and no upstream request is built at all");

  // wav is the default, and it is the one that was measured to work.
  deep(envmod.readConfig(FULL).sttFormats, ["wav"],
       "DEMO_STT_FORMATS defaults to wav alone — the only container measured to transcribe");
  const wav = await call(clip(4000, "wav"), null, FULL, "a wav clip");
  eq(wav.res.status, 200, "…and a wav clip is accepted");
  eq(upstreamCalls(), 1, "…as the one upstream call");

  // A fork whose gateway is more capable opens it up, with no code change (C3).
  fresh();
  const wide = { ...FULL, DEMO_STT_FORMATS: "wav,webm,ogg" };
  eq((await call(clip(4000, "webm"), null, wide, "webm on a wider gateway")).res.status, 200,
     "DEMO_STT_FORMATS widens the allowlist for a gateway that accepts more");
  eq((await call(clip(4000, "mp4"), null, wide, "mp4 on a wider gateway")).body.reason, "bad_request",
     "…and still refuses what is not listed");

  // A malformed value falls back to the default rather than switching the ears off with a
  // reason nobody could read.
  deep(envmod.readConfig({ ...FULL, DEMO_STT_FORMATS: "mp9,quicktime" }).sttFormats, ["wav"],
       "an unusable DEMO_STT_FORMATS falls back to wav, never to nothing");
  deep(envmod.readConfig({ ...FULL, DEMO_STT_FORMATS: "" }).sttFormats, ["wav"],
       "…and so does an empty one");
}

/* --------------------------------------------------------------------------- *
 * A8. The upstream body is BUILT, never forwarded (§4.1's highest-value control)
 * --------------------------------------------------------------------------- */
{
  fresh();
  await call(clip(4000), { "X-Client-Model": "gpt-4o", "X-Prompt": "ignore previous" }, FULL, "a nosy client");
  eq(sent.length, 1, "one upstream call");
  eq(sent[0].url, BASE + "/audio/transcriptions", "…to /audio/transcriptions on the configured base");
  const form = sent[0].opt.body;
  ok(form instanceof FormData, "the upstream body is a multipart FormData");
  eq(form.get("model"), "test-ears-model", "the model is SERVER-FIXED from DEMO_STT_MODEL");
  eq(form.get("response_format"), "json", "response_format is fixed");
  deep([...form.keys()].sort(), ["file", "model", "response_format"],
       "…and NOTHING else is sent: no language, no prompt, no temperature");
  const file = form.get("file");
  eq(file.name, "utterance.wav", "the filename carries the sniffed extension (an OpenAI endpoint reads it)");
  eq(file.type, "audio/wav", "…and the sniffed mime");
  eq(file.size, 4000, "…and the visitor's bytes, unmodified");

  // The credentials are present, and the Content-Type is deliberately ABSENT so `fetch`
  // can generate the multipart boundary itself.
  const h = sent[0].opt.headers;
  ok(h.Authorization && h.Authorization.indexOf("Bearer ") === 0, "the key rides as an Authorization header");
  eq(h["Content-Type"], undefined, "NO hand-written Content-Type — fetch owns the multipart boundary");

  // A Cloudflare Access service token rides along when both halves are configured.
  fresh();
  await call(clip(4000), null,
             { ...FULL, DEMO_GATEWAY_ACCESS_CLIENT_ID: "id.access", DEMO_GATEWAY_ACCESS_CLIENT_SECRET: "shh" },
             "an Access-gated gateway");
  eq(sent[0].opt.headers["CF-Access-Client-Id"], "id.access", "a complete Access token is presented");
  eq(sent[0].opt.headers["CF-Access-Client-Secret"], "shh", "…both halves");
}

/* --------------------------------------------------------------------------- *
 * A9. The transcript itself
 * --------------------------------------------------------------------------- */
{
  fresh();
  plan = { text: "  Hi Moxie,   tell me a joke.  " };
  const good = await call(clip(4000), null, FULL, "a normal transcript");
  eq(good.res.status, 200, "a transcript is a 200");
  eq(good.body.transcript, "Hi Moxie, tell me a joke.", "whitespace is collapsed and trimmed");
  eq(good.body.reason, null, "…with no reason");
  eq(good.body.ok, true, "…and ok true");
  eq(good.body.degraded, false, "…not degraded");

  // Silence is a SUCCESS, not an error: the visitor simply did not speak.
  fresh();
  plan = { text: "" };
  const quiet = await call(clip(4000), null, FULL, "silence");
  eq(quiet.res.status, 200, "an empty transcript is still a 200");
  eq(quiet.body.transcript, "", "…and an empty transcript");
  eq(quiet.body.reason, null, "…with no reason: silence is not a failure");

  // Control characters are stripped; over-length is truncated, not refused.
  eq(route.cleanTranscript("a\u0000b\u001Fc", 500).text, "a b c",
     "control characters are stripped");
  const long = route.cleanTranscript("x".repeat(600), 500);
  eq(long.text.length, 500, "an over-length transcript is truncated to DEMO_MAX_INPUT_CHARS");
  eq(long.truncated, true, "…and says so");
  fresh();
  plan = { text: "y".repeat(600) };
  const trunc = await call(clip(4000), null, FULL, "a very long transcript");
  eq(trunc.body.transcript.length, 500, "the route truncates rather than refusing a spoken turn");
  ok(/truncated/.test(trunc.body.message), "…and tells the visitor via `message`");
}

/* --------------------------------------------------------------------------- *
 * A10. §3.2 / §4.2 — one envelope, a closed key set, no CORS
 * --------------------------------------------------------------------------- */
{
  fresh();
  const responses = [];
  responses.push((await call(clip(4000), null, FULL, "success")).res);
  responses.push((await call(clip(10), null, FULL, "too short")).res);
  responses.push((await call(clip(4000), { Origin: "https://evil.example" }, FULL, "forbidden")).res);
  responses.push((await call(clip(4000), null, {}, "unconfigured")).res);
  plan = { status: 500, body: "boom" };
  responses.push((await call(clip(4000), null, FULL, "upstream 500")).res);

  for (const res of responses) {
    const body = JSON.parse(await res.clone().text());
    deep(Object.keys(body), [...envlib.PUBLIC_KEYS], "every response has exactly PUBLIC_KEYS, in order");
    eq(res.headers.get("Cache-Control"), "no-store", "no-store on every reply");
    eq(res.headers.get("Access-Control-Allow-Origin"), null, "NO Access-Control-Allow-Origin, ever (§4.3)");
    ok(res.headers.get("X-Moxie-Mode") !== null, "X-Moxie-Mode rides every response");
    eq(res.headers.get("X-Content-Type-Options"), "nosniff", "nosniff on every reply");
    ok(typeof body.transcript === "string", "`transcript` is always a string, never absent");
  }
  ok(envlib.PUBLIC_KEYS.includes("transcript"), "`transcript` is in the envelope's key allowlist");
  ok(sweeps > 60, `assertClean ran on every response (${sweeps} sweeps)`);
}

/* --------------------------------------------------------------------------- *
 * A-DUR. THE DURATION CEILING, ENFORCED SERVER-SIDE FOR THE ONE CONTAINER THAT ALLOWS IT
 * --------------------------------------------------------------------------- *
 * Spec: docs/architecture/backlog/live-sim-demo.md §4.1 (the byte caps and the paragraph
 * that says a byte cap is not a duration cap), §4.5 (`too_long`).
 *
 * THE HOLE. `DEMO_MAX_AUDIO_BYTES` (500 000) was reasoned about as "≈ 15 s", which is true
 * of 16 kHz 16-bit mono and of nothing else. **STT is billed by duration**, and the same
 * 500 KB is 62 s at 8 kHz 8-bit — a perfectly ordinary, perfectly well-formed WAV that
 * this route forwarded and paid for. `DEMO_MAX_RECORD_MS`, the number that is supposed to
 * bound it, lived only in `sim/web/mic.js`: a browser control, which a caller who is not
 * using our page simply does not run.
 *
 * THE FIX, AND ITS HONEST EDGE. A RIFF header declares its own playing time, so for WAV
 * the cap is now real and server-side. For webm/Opus and the rest it still is not — the
 * duration is in a bitstream and reading it means shipping a decoder at a hostile upload.
 * What makes that acceptable is `DEMO_STT_FORMATS`, which defaults to `wav` ALONE, so on
 * the shipped configuration nothing else reaches the gateway at all. Both halves are
 * asserted below, including the uncomfortable one.
 * --------------------------------------------------------------------------- */
{
  /** A WAV of a chosen rate/width/length. Not `wav.writeWav`, which only emits 16-bit —
   *  and 8-bit is exactly the case under test. */
  const wavAt = (rate, ch, bits, dataLen) => {
    const out = new Uint8Array(44 + dataLen);
    const v = new DataView(out.buffer);
    const a = (at, str) => { for (let i = 0; i < str.length; i++) out[at + i] = str.charCodeAt(i); };
    a(0, "RIFF"); v.setUint32(4, 36 + dataLen, true); a(8, "WAVE");
    a(12, "fmt "); v.setUint32(16, 16, true);
    v.setUint16(20, 1, true); v.setUint16(22, ch, true); v.setUint32(24, rate, true);
    v.setUint32(28, Math.floor(rate * ch * bits / 8), true);
    v.setUint16(32, Math.max(1, Math.floor(ch * bits / 8)), true); v.setUint16(34, bits, true);
    a(36, "data"); v.setUint32(40, dataLen, true);
    for (let i = 44; i < out.length; i++) out[i] = i & 0xff;
    return out;
  };
  const cfg = envmod.readConfig(FULL);
  eq(cfg.maxRecordMs, 15000, "the block is calibrated to the shipped DEMO_MAX_RECORD_MS");

  // ---- The attack, exactly as it was available before 2026-09-03 ---------- //
  fresh();
  const sixtySeconds = wavAt(8000, 1, 8, 480000);   // 480 KB: comfortably under the byte cap
  ok(sixtySeconds.length < cfg.maxAudioBytes, "the hostile clip is INSIDE DEMO_MAX_AUDIO_BYTES");
  eq(wavlib.wavDurationMs(sixtySeconds).ms, 60000,
     "…and declares 60 s — FOUR TIMES DEMO_MAX_RECORD_MS, inside the byte cap, in a well-formed WAV");
  const long = await call(sixtySeconds, null, FULL, "a 60-second 8-bit WAV under the byte cap");
  eq(long.body.reason, "too_long", "IT IS REFUSED: the duration ceiling is now enforced server-side");
  eq(long.res.status, 400, "…with §4.5's status for too_long");
  eq(upstreamCalls(), 0, "…AND THE GATEWAY IS NEVER TOUCHED: a duration refusal costs nothing");

  // Every width and rate that buys extra seconds inside the same byte budget.
  for (const [rate, bits, label] of [[8000, 16, "8 kHz 16-bit (31 s)"], [8000, 8, "8 kHz 8-bit (62 s)"],
                                     [8000, 4, "8 kHz 4-bit (125 s)"], [4000, 8, "4 kHz 8-bit (125 s)"]]) {
    fresh();
    const r = await call(wavAt(rate, 1, bits, 480000), null, FULL, label);
    eq(r.body.reason, "too_long", `${label} under the byte cap is refused on DURATION`);
    eq(upstreamCalls(), 0, `${label}: zero upstream calls`);
  }

  // ---- …and an honest recording still goes through ------------------------ //
  fresh();
  const fine = wavAt(16000, 1, 16, 16000 * 2 * 5);  // 5 seconds, the shape `mic.js` encodes
  ok(wavlib.wavDurationMs(fine).ms === 5000, "the control clip really is 5 s");
  const good = await call(fine, null, FULL, "an honest 5-second 16 kHz clip");
  eq(good.res.status, 200, "a clip inside the ceiling is transcribed as before");
  eq(good.body.reason, null, "…with no reason");
  eq(upstreamCalls(), 1, "…and exactly one upstream call");

  // The boundary, both sides of it, so the comparison is `>` and not `>=` by accident.
  fresh();
  const exact = wavAt(16000, 1, 16, 16000 * 2 * 15);   // exactly DEMO_MAX_RECORD_MS
  eq(wavlib.wavDurationMs(exact).ms, cfg.maxRecordMs, "the boundary clip is exactly at the cap");
  eq((await call(exact, null, FULL, "exactly at the cap")).res.status, 200, "AT the cap is allowed");
  fresh();
  const over = wavAt(16000, 1, 16, 16000 * 2 * 15 + 3200);  // +100 ms
  eq((await call(over, null, FULL, "100 ms over the cap")).body.reason, "too_long", "just OVER the cap is not");

  // A shorter configured ceiling is obeyed — the number is a variable, not a constant.
  fresh();
  const SHORT_CAP = { ...FULL, DEMO_MAX_RECORD_MS: "3000" };
  eq((await call(fine, null, SHORT_CAP, "5 s against a 3 s cap")).body.reason, "too_long",
     "DEMO_MAX_RECORD_MS is what the check reads, so a fork can tighten it with no code change");
  eq(upstreamCalls(), 0, "…still with zero upstream calls");

  // ---- THE PART THIS DOES NOT COVER, asserted rather than hoped ----------- //
  // A webm body's duration is unknowable without a decoder. The route therefore has NO
  // opinion on it — and the only reason that is not a live hole is that the same webm is
  // refused one step earlier by `DEMO_STT_FORMATS`, which ships as `wav` alone.
  fresh();
  eq(wavlib.wavDurationMs(clip(480000, "webm")), null,
     "a webm body yields NO duration: the honest answer, and the limit of this fix");
  const webm = await call(clip(480000, "webm"), { "Content-Type": "audio/webm" }, FULL, "a 480 KB webm");
  eq(webm.body.reason, "bad_request",
     "…and it is refused by the CONTAINER allowlist instead, which is what closes the gap today");
  eq(upstreamCalls(), 0, "…for free");
  // Spelled out: widen the allowlist and the duration ceiling stops being total.
  const WIDE = { ...FULL, DEMO_STT_FORMATS: "wav,webm" };
  deep(envmod.readConfig(WIDE).sttFormats, ["wav", "webm"], "a fork CAN widen DEMO_STT_FORMATS");
  fresh();
  const wideWebm = await call(clip(480000, "webm"), { "Content-Type": "audio/webm" }, WIDE, "webm, allowlisted");
  eq(wideWebm.res.status, 200,
     "…and then a 480 KB webm of UNKNOWN duration is forwarded — the residual gap, stated not hidden");
}

/* --------------------------------------------------------------------------- *
 * A-RDR. The credential does not chase a `Location`
 * --------------------------------------------------------------------------- *
 * The upload rides an `Authorization` header (and the `CF-Access-*` pair when a service
 * token is configured). Fetched with `redirect` unset — the default `follow` — a 3xx would
 * have this route re-issue the whole multipart body at whatever host the `Location` names.
 * `manual` removes the question, and the 3xx is answered as what it actually is: a tunnel,
 * an Access login flow, or a base URL that bounces — **a door problem, not a brain
 * problem**, which is the operator signal `gateway_unreachable_or_gated` carries. Left to
 * `reasonForUpstreamStatus` it would fall through to `upstream_down` (a 503, which degrades
 * the whole page under §6.3) and send an operator to restart a model server.
 * --------------------------------------------------------------------------- */
{
  fresh();
  await call(clip(4000), null, FULL, "a normal turn, to read the fetch options");
  eq(sent.length, 1, "one upstream call");
  eq(sent[0].opt.redirect, "manual",
     "/api/transcribe sets redirect:'manual' — the multipart body and the key are never re-sent");

  eq(route.reasonForUpstreamStatus(302), "upstream_down",
     "the status table itself has no 3xx row — which is why the route answers one before consulting it");

  for (const status of [301, 302, 303, 307, 308]) {
    fresh();
    plan = { status, body: "", headers: { Location: "https://elsewhere.invalid.test/v1/audio/transcriptions" } };
    const r = await call(clip(4000), null, FULL, `an upstream ${status}`);
    eq(r.body.reason, "gateway_unreachable_or_gated", `an upstream ${status} is read as a DOOR problem`);
    eq(r.res.status, 503, `…and answers 503 for a ${status}`);
    eq(sent.length, 1, `…having made exactly ONE upstream call — the ${status} was not chased`);
    eq(r.body.transcript, "", `…and says nothing it did not hear (${status})`);
  }
}

/* =========================================================================== *
 * PART B — sim/web/mic.js, with a fake recorder and a virtual clock
 * =========================================================================== */

const MIC_SRC = readFileSync(join(repo, "sim", "web", "mic.js"), "utf8");

/* ---- a virtual clock ------------------------------------------------------ */
let clockNow = 0, timerSeq = 0, timers = [];
const realSetImmediate = setImmediate;

function installClock() {
  clockNow = 0; timerSeq = 0; timers = [];
  globalThis.setTimeout = (fn, ms) => {
    const id = ++timerSeq;
    timers.push({ id, at: clockNow + (Number(ms) || 0), fn });
    return id;
  };
  globalThis.clearTimeout = (id) => {
    const i = timers.findIndex((t) => t.id === id);
    if (i >= 0) timers.splice(i, 1);
  };
}
const flush = () => new Promise((r) => realSetImmediate(r));
async function advance(ms) {
  const target = clockNow + ms;
  await flush();
  for (;;) {
    timers.sort((a, b) => a.at - b.at || a.id - b.id);
    const next = timers.find((t) => t.at <= target);
    if (!next) break;
    timers.splice(timers.indexOf(next), 1);
    clockNow = next.at;
    try { next.fn(); } catch (e) { fails.push("a timer threw: " + e.message); }
    await flush();
  }
  clockNow = target;
  await flush();
}
/** Timers still pending — the assertion that a cap was CLEARED, not merely not fired. */
const pendingTimers = () => timers.length;

/* ---- a fake recorder ------------------------------------------------------ */
/**
 * The `MediaRecorder` surface `mic.js` actually uses, and nothing more. It records every
 * call so a test can assert THE RECORDER WAS STOPPED, rather than sampling a device.
 * **No live microphone is opened anywhere in this file.**
 */
function makeRecorder(o) {
  const opts = o || {};
  const log = [];
  const r = {
    log,
    state: "inactive",
    mimeType: opts.mimeType || "audio/webm;codecs=opus",
    ondataavailable: null,
    onstop: null,
    start() { log.push("start"); r.state = "recording"; },
    stop() {
      log.push("stop");
      r.state = "inactive";
      const size = opts.size === undefined ? 40000 : opts.size;
      if (r.ondataavailable) r.ondataavailable({ data: { size } });
      if (r.onstop) r.onstop();
    },
  };
  return r;
}

/* ---- the page ------------------------------------------------------------- */
function bootMic(o) {
  const opts = o || {};
  installClock();

  const els = {};
  const mk = (id) => ({
    id, value: "", textContent: "", className: "",
    addEventListener() {}, setAttribute() {}, classList: { add() {}, remove() {}, toggle() {} },
  });
  for (const id of ["mic-status", "bus-status", "mic-btn", "stt-base"]) els[id] = mk(id);

  const bodyAttrs = {};
  globalThis.document = {
    readyState: "complete",
    getElementById: (id) => els[id] || null,
    addEventListener() {},
    body: {
      setAttribute: (k, v) => { bodyAttrs[k] = v; },
      removeAttribute: (k) => { delete bodyAttrs[k]; },
    },
  };
  globalThis.location = { protocol: "https:", hostname: "demo.invalid.test", origin: ORIGIN };
  const store = opts.storage || {};
  globalThis.localStorage = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
  };
  /* A microphone that is NOT a microphone: `getUserMedia` resolves a marker object, and
   * the fake AudioContext below feeds `encodeWav` a synthesised tone. No device is ever
   * opened, and `MediaRecorder` throws if anything tries to construct a real one. */
  const gum = [];
  // `Object.defineProperty`, not assignment: `globalThis.navigator` became a
  // **getter-only** accessor in Node 21, so `globalThis.navigator = …` throws
  // `TypeError: Cannot set property navigator of #<Object> which has only a getter`.
  // Local Node 20 accepts the assignment and CI runs Node 24, so this passed here and
  // failed there — the same shape as the Pages JSON-import finding (rule 19): a runtime
  // difference between where a test runs and where it is validated.
  // `sim/tests/test_node_global_stubs.py` now fails on the assignment form.
  Object.defineProperty(globalThis, "navigator", { configurable: true, writable: true, value: {
    mediaDevices: {
      getUserMedia: (c) => {
        gum.push(c);
        return opts.denyMic
          ? Promise.reject(new Error("NotAllowedError"))
          : Promise.resolve({ getTracks: () => [{ stop() {} }] });
      },
    },
  } });
  globalThis.MediaRecorder = function () { throw new Error("a test must never construct a real MediaRecorder"); };
  const audioCtx = { closed: false, processors: [] };
  globalThis.window = globalThis.window || {};
  globalThis.AudioContext = function () {
    const ctx = {
      sampleRate: opts.sampleRate || 48000,
      createMediaStreamSource: () => ({ connect() {}, disconnect() {} }),
      createGain: () => ({ gain: { value: 1 }, connect() {}, disconnect() {} }),
      createScriptProcessor: (size) => {
        const node = { bufferSize: size, onaudioprocess: null, connect() {}, disconnect() {} };
        audioCtx.processors.push(node);
        return node;
      },
      destination: {},
      close: () => { audioCtx.closed = true; },
    };
    return ctx;
  };
  globalThis.AbortSignal = { timeout: () => ({ aborted: false, addEventListener() {} }) };
  const RealBlob = globalThis.__realBlob || (globalThis.__realBlob = globalThis.Blob);
  globalThis.Blob = class FakeBlob {
    constructor(parts, o2) {
      const list = parts || [];
      // A part that carries real bytes (the WAV encoder's Uint8Array, or a real Blob) is
      // kept whole, so a test can assert on what would actually be uploaded.
      this.parts = list;
      this.size = list.reduce(
        (n, p) => n + (p && p.size !== undefined ? p.size : (p && p.byteLength) || 0), 0);
      this.type = (o2 && o2.type) || "";
      const bytes = list.find((x) => x && x.byteLength !== undefined);
      this.bytes = bytes || (list[0] && list[0].bytes) || null;
    }
  };
  void RealBlob;

  const published = [];   // reached window.moxieBridge.sendUserTurn — THE PAID PATH
  const scripted = [];    // reached window.moxieBridge.sendScriptedTurn — the free one
  const routed = [];      // reached window.moxieBridge.route — free, and answers nothing
  const spoken = [];
  globalThis.window = {
    addEventListener() {},
    moxieBridge: Object.assign({
      sendUserTurn: (t) => published.push(t),
      route: (topic, payload) => routed.push([topic, payload]),
    }, (typeof opts.bridge === "function" ? opts.bridge({ published, scripted, routed }) : opts.bridge) || {}),
    moxieAudio: { sfx: (n) => spoken.push(n) },
    moxieStub: {
      enabled: true,
      scriptedLines: () => Promise.resolve(opts.scriptedLines || ["Look what I made!", "It's my birthday!"]),
    },
    moxieMode: opts.mode === null ? undefined : Object.assign({
      apiBase: () => ORIGIN,
      ears: () => true,
      limits: () => ({ max_record_ms: 15000, max_audio_bytes: 500000, min_audio_bytes: 2000 }),
      note: (n) => notes.push(n),
      noteTransportError: () => notes.push({ reason: "transport_error" }),
    }, opts.mode || {}),
  };

  // mic.js reads `window.AudioContext` (a real page's global), so the fake window needs it
  // — it is deliberately NOT on the bare `globalThis` for the page to find by accident.
  globalThis.window.AudioContext = globalThis.AudioContext;

  const notes = [];
  const posts = [];
  globalThis.fetch = (url, init) => {
    posts.push({ url: String(url), init });
    const answer = opts.answer || (() => ({ status: 200, json: { transcript: "hi moxie" } }));
    const a = answer(String(url), init);
    if (a && a.reject) return Promise.reject(new Error("network"));
    return Promise.resolve(new Response(
      typeof a.text === "string" ? a.text : JSON.stringify(a.json || {}),
      { status: a.status || 200, headers: { "Content-Type": "application/json" } },
    ));
  };

  (0, eval)(MIC_SRC);
  const mic = globalThis.window.moxieMic;
  const rec = makeRecorder(opts.recorder);
  // `realCapture` leaves mic.js to choose its OWN capture per target — which is the thing
  // the WAV block below has to exercise. Everything else injects a fake recorder.
  if (!opts.realCapture) {
    mic.setCapture(() => Promise.resolve({ recorder: rec, stream: { getTracks: () => [] } }));
  }
  return { mic, rec, posts, notes, published, scripted, routed, els, bodyAttrs, audioCtx, gum,
           statusText: () => els["mic-status"].textContent };
}

/* --------------------------------------------------------------------------- *
 * B1. THE HARD STOP — 15 s actually stops a recorder
 * --------------------------------------------------------------------------- */
{
  const w = bootMic();
  eq(w.mic.maxRecordMs(), 15000, "the cap is the server-published DEMO_MAX_RECORD_MS");
  await w.mic.start();
  await flush();
  eq(w.mic.isRecording(), true, "recording");
  deep(w.rec.log, ["start"], "the recorder was started");

  await advance(14999);
  deep(w.rec.log, ["start"], "…and is STILL running at 14 999 ms");
  eq(w.mic.isRecording(), true, "…still recording just under the cap");

  await advance(2);
  deep(w.rec.log, ["start", "stop"], "AT 15 000 ms THE RECORDER IS STOPPED — the cap is real");
  eq(w.mic.isRecording(), false, "…and the page knows it stopped");
  eq(w.mic.stats().autoStops, 1, "…recorded as an autoStop, not a user stop");
  eq(w.bodyAttrs["data-mic"], undefined, "…and the recording indicator is cleared");
  eq(pendingTimers(), 0, "the cap timer is not left behind");
}

/* --------------------------------------------------------------------------- *
 * B2. The cap is the SERVER's number, is overridable, and survives a silly one
 * --------------------------------------------------------------------------- */
{
  // A deployment that shortens it.
  const short = bootMic({ mode: { limits: () => ({ max_record_ms: 4000 }) } });
  eq(short.mic.maxRecordMs(), 4000, "a server-published cap is obeyed");
  await short.mic.start();
  await advance(4001);
  deep(short.rec.log, ["start", "stop"], "…and stops the recorder at ITS number");

  // No server at all: the built-in 15 s still applies. An unbounded recorder is a bug on
  // a laptop too.
  const local = bootMic({ mode: null });
  eq(local.mic.maxRecordMs(), 15000, "with NO mode machine the built-in 15 s cap still applies");
  await local.mic.start();
  await advance(15001);
  deep(local.rec.log, ["start", "stop"], "…and still stops the recorder");

  // A malformed or absurd published cap falls back rather than disabling the stop.
  for (const [label, value] of [
    ["a string", "soon"], ["zero", 0], ["negative", -1], ["an hour and a half", 5400000], ["null", null],
  ]) {
    const w = bootMic({ mode: { limits: () => ({ max_record_ms: value }) } });
    eq(w.mic.maxRecordMs(), 15000, `${label} as a cap falls back to 15 s — the stop is never disabled`);
  }

  // A self-hoster can choose their own via localStorage, when no server published one.
  const custom = bootMic({ mode: null, storage: { "moxie.maxRecordMs": "3000" } });
  eq(custom.mic.maxRecordMs(), 3000, "moxie.maxRecordMs lets a self-hoster pick a different cap");

  // A user stop before the cap clears the timer — no ghost stop later.
  const early = bootMic();
  await early.mic.start();
  await advance(1000);
  early.mic.stop();
  await advance(60000);
  deep(early.rec.log, ["start", "stop"], "a user stop fires once; the cap timer does not fire again");
  eq(early.mic.stats().autoStops, 0, "…and is not recorded as an autoStop");
  eq(pendingTimers(), 0, "…with no timer left behind");
}

/* --------------------------------------------------------------------------- *
 * B3. Where the clip goes: the mode machine picks, and an explicit base wins
 * --------------------------------------------------------------------------- */
{
  const cloud = bootMic();
  deep(cloud.mic.sttTarget(), { url: ORIGIN + "/api/transcribe", kind: "cloud" },
       "live ears => the SAME ORIGIN route, with no hostname anywhere in the code (C3)");

  const noEars = bootMic({ mode: { ears: () => false } });
  eq(noEars.mic.sttTarget().kind, "local", "no ears => the local sidecar, exactly as today");
  ok(/:8082\/stt$/.test(noEars.mic.sttTarget().url), "…on port 8082");

  const offline = bootMic({ mode: null });
  eq(offline.mic.sttTarget().kind, "local", "no mode machine at all => the local sidecar");

  const noBase = bootMic({ mode: { apiBase: () => null } });
  eq(noBase.mic.sttTarget().kind, "local", "a file:// page (no apiBase) => the local sidecar");

  // An explicit base is a developer saying "use THIS", and beats the same-origin route.
  const pinned = bootMic({ storage: { "moxie.sttBase": "http://127.0.0.1:8082" } });
  eq(pinned.mic.sttTarget().kind, "local", "an explicit moxie.sttBase WINS over live ears");
  eq(pinned.mic.sttTarget().url, "http://127.0.0.1:8082/stt", "…and is used verbatim");
  const set = bootMic();
  set.mic.setSttBase("http://192.0.2.7:8082");
  eq(set.mic.sttTarget().kind, "local", "…and setSttBase pins it at runtime too");
}

/* --------------------------------------------------------------------------- *
 * B4. Both shapes parse, and the local sidecar path is UNCHANGED
 * --------------------------------------------------------------------------- */
{
  // The house envelope from /api/transcribe.
  const w = bootMic({ answer: () => ({ status: 200, json: { transcript: "hi moxie", reason: null } }) });
  await w.mic.start();
  await advance(15001);
  await flush();
  eq(w.posts.length, 1, "one POST");
  eq(w.posts[0].url, ORIGIN + "/api/transcribe", "…to the same-origin route");
  eq(w.posts[0].init.method, "POST", "…as a POST");
  eq(w.posts[0].init.credentials, "omit", "…with no credentials");
  eq(w.posts[0].init.headers["Content-Type"], "audio/webm;codecs=opus",
     "…and the blob's own type as the Content-Type, exactly what §3.2 says the route takes");
  deep(w.published, ["hi moxie"], "the transcript is published as a child utterance");
  eq(w.mic.stats().transcripts, 1, "…and recorded");
  ok(/heard: "hi moxie"/.test(w.statusText()), "…and shown");

  // The sidecar's DeepgramResponse, unchanged.
  const d = bootMic({
    mode: { ears: () => false },
    answer: () => ({ status: 200, json: { channel: { alternatives: [{ transcript: "look what I made", confidence: 0.9 }] } } }),
  });
  await d.mic.start();
  await advance(15001);
  await flush();
  ok(/\/stt$/.test(d.posts[0].url), "the sidecar is still POSTed at /stt");
  deep(d.published, ["look what I made"], "…and its DeepgramResponse still parses, unchanged");

  // An empty transcript is silence, not a failure — and publishes nothing.
  const q = bootMic({ answer: () => ({ status: 200, json: { transcript: "" } }) });
  await q.mic.start();
  await advance(15001);
  await flush();
  deep(q.published, [], "silence publishes nothing");
  eq(q.statusText(), "(nothing heard)", "…and says so");
  eq(q.mic.stats().fallbacks, 0, "…without burning a scripted line");
}

/* --------------------------------------------------------------------------- *
 * B5. §6 — the page NEVER goes dead, for any reason the server can send
 * --------------------------------------------------------------------------- */
{
  const cases = [
    ["rate_limited", 429, /one at a time/i],
    ["at_capacity", 503, /hands full/i],
    ["budget_exhausted", 503, /budget/i],
    ["upstream_down", 503, /can't hear/i],
    ["gateway_unreachable_or_gated", 503, /can't hear/i],
    ["gateway_not_configured", 503, /scripted/i],
    ["timeout", 504, /too long/i],
    ["bad_request", 400, /wasn't usable/i],
    ["too_long", 400, /too long/i],
    ["forbidden_origin", 403, /not available/i],
  ];
  for (const [reason, status, copy] of cases) {
    const w = bootMic({
      answer: () => ({ status, json: { ok: false, degraded: true, reason, retry_after_s: 7, transcript: "" } }),
    });
    await w.mic.start();
    await advance(15001);
    await flush();
    await flush();
    eq(w.mic.stats().fallbacks, 1, `${reason}: falls back to a scripted line`);
    eq(w.published.length, 1, `${reason}: …and a child line still reaches the bus — never a dead button`);
    ok(copy.test(w.statusText()), `${reason}: the status says why (got "${w.statusText()}")`);
    ok(!/\b\d{3}\b/.test(w.statusText()), `${reason}: …with no raw status code shown to a visitor`);
    deep(w.notes.map((n) => n.reason), [reason], `${reason}: reported to the mode machine, so the badge follows`);
    eq(w.notes[0].retry_after_s, 7, `${reason}: …with the Retry-After the server sent`);
  }

  // A network failure with no envelope at all: three of those degrade the page (§6.3),
  // and this one turn still answers.
  const dead = bootMic({ answer: () => ({ reject: true }) });
  await dead.mic.start();
  await advance(15001);
  await flush();
  await flush();
  eq(dead.mic.stats().fallbacks, 1, "a network failure still answers from the scripted repertoire");
  deep(dead.notes.map((n) => n.reason), ["transport_error"], "…and is reported as a transport error");

  // A non-2xx with no readable envelope (an old sidecar, a proxy error page).
  const bare = bootMic({ mode: { ears: () => false }, answer: () => ({ status: 500, text: "boom" }) });
  await bare.mic.start();
  await advance(15001);
  await flush();
  await flush();
  eq(bare.mic.stats().fallbacks, 1, "a bare non-2xx still answers");

  // And with no stub loaded at all, it says something rather than nothing.
  const nostub = bootMic({ answer: () => ({ reject: true }) });
  globalThis.window.moxieStub = null;
  await nostub.mic.start();
  await advance(15001);
  await flush();
  await flush();
  ok(nostub.statusText().length > 0, "with no stub either, the status line is still honest, never blank");
}

/* --------------------------------------------------------------------------- *
 * B5b. THE CONSOLATION LINE MAY NOT SPEND A LIVE TURN
 * --------------------------------------------------------------------------- *
 * The scripted child line is a line THE PAGE CHOSE. Published through
 * `moxieBridge.sendUserTurn` it is indistinguishable from a transcript, and on a hosted
 * live page `cloud-transport.js` takes it to `POST /api/chat` and `POST /api/speech` —
 * a full paid turn on words nobody said. Here we prove `mic.js` sends it down the free
 * seam instead; `sim/test_cloud_transport.mjs` block 6b proves the seam costs nothing, and
 * `sim/test_mic_spend.mjs` proves the whole thing in Chrome by counting real requests.
 * --------------------------------------------------------------------------- */
{
  const liveBridge = (rec) => ({ sendScriptedTurn: (t) => rec.scripted.push(t) });
  const LIVE = { canSpendLiveTurn: () => true };

  // Every degraded path a live page can reach, including the two that never even upload.
  const paths = [
    ["a refusal the mode ignores (bad_request)",
     { mode: LIVE, answer: () => ({ status: 400, json: { ok: false, reason: "bad_request" } }) }],
    ["a server too_short",
     { mode: LIVE, answer: () => ({ status: 400, json: { ok: false, reason: "too_short" } }) }],
    ["a server too_long",
     { mode: LIVE, answer: () => ({ status: 400, json: { ok: false, reason: "too_long" } }) }],
    ["a timeout (only the THIRD of which degrades the page)",
     { mode: LIVE, answer: () => ({ status: 504, json: { ok: false, reason: "timeout" } }) }],
    ["a network failure with no envelope",
     { mode: LIVE, answer: () => ({ reject: true }) }],
    ["an unparseable non-2xx",
     { mode: LIVE, answer: () => ({ status: 500, text: "boom" }) }],
    ["a clip over max_audio_bytes, refused CLIENT-side with no upload at all",
     { mode: LIVE, recorder: { size: 900000 } }],
  ];
  for (const [label, opts] of paths) {
    const w = bootMic(Object.assign({ bridge: liveBridge }, opts));
    await w.mic.start();
    await advance(15001);
    await flush();
    await flush();
    eq(w.mic.stats().fallbacks, 1, `${label}: the visitor is still consoled with a scripted line`);
    eq(w.scripted.length, 1, `${label}: …through the FREE scripted seam`);
    deep(w.published, [],
         `${label}: …AND NOT ONE WORD REACHED sendUserTurn — no /api/chat, no /api/speech`);
  }

  // A REAL transcript on the very same live page still spends its turn, exactly as before.
  {
    const w = bootMic({ mode: LIVE, bridge: liveBridge,
                        answer: () => ({ status: 200, json: { transcript: "hi moxie" } }) });
    await w.mic.start();
    await advance(15001);
    await flush();
    deep(w.published, ["hi moxie"], "a real transcript STILL goes through sendUserTurn — the paid path is for words");
    deep(w.scripted, [], "…and never through the scripted seam");
    eq(w.mic.stats().transcripts, 1, "…recorded as a transcript");
  }

  // A page that CANNOT spend takes exactly the path it takes today: sendUserTurn, which is
  // bridge.js's own and answers from stub.js for free. Nothing here needed changing.
  {
    const w = bootMic({ answer: () => ({ reject: true }) });      // no canSpendLiveTurn at all
    await w.mic.start();
    await advance(15001);
    await flush();
    await flush();
    eq(w.published.length, 1, "with nothing spendable the scripted line still goes through sendUserTurn");
    deep(w.routed, [], "…and nothing was routed around it");
  }

  // Belt and braces: a live page whose transport wrapped the bridge WITHOUT offering the
  // seam must still not pay. The line is echoed locally instead.
  {
    const w = bootMic({ mode: LIVE, answer: () => ({ reject: true }) });
    await w.mic.start();
    await advance(15001);
    await flush();
    await flush();
    deep(w.published, [], "a live page with no scripted seam STILL does not reach sendUserTurn");
    eq(w.routed.length, 1, "…the line is echoed locally instead");
    const echo = w.routed[0] || ["", "{}"];
    eq(echo[0], "/devices/d_sim/events/remote-chat", "…on the child-utterance topic");
    ok(String(JSON.parse(echo[1] || "{}").speech || "").length > 0, "…carrying the scripted words");
  }
}

/* --------------------------------------------------------------------------- *
 * B6. The free client-side gates — a doomed upload never happens
 * --------------------------------------------------------------------------- */
{
  const tiny = bootMic({ recorder: { size: 500 } });
  await tiny.mic.start();
  await advance(15001);
  await flush();
  eq(tiny.posts.length, 0, "a clip under min_audio_bytes IS NEVER UPLOADED — no request at all");
  eq(tiny.statusText(), "(too short)", "…and says so");
  eq(tiny.mic.stats().tooShort, 1, "…recorded");

  const huge = bootMic({ recorder: { size: 900000 } });
  await huge.mic.start();
  await advance(15001);
  await flush();
  await flush();
  eq(huge.posts.length, 0, "a clip over max_audio_bytes is never uploaded either");
  eq(huge.mic.stats().tooLong, 1, "…recorded");
  eq(huge.published.length, 1, "…and still answers with a scripted line");

  // With no server-published floor, the historical 800-byte gate is what applies.
  const old = bootMic({ mode: null, recorder: { size: 900 } });
  await old.mic.start();
  await advance(15001);
  await flush();
  eq(old.posts.length, 1, "with no published floor the historical 800-byte gate applies, unchanged");
}

/* --------------------------------------------------------------------------- *
 * B7. Capture failures and the honest button
 * --------------------------------------------------------------------------- */
{
  const denied = bootMic();
  denied.mic.setCapture(() => Promise.reject(new Error("NotAllowedError")));
  await denied.mic.start();
  await flush();
  eq(denied.mic.isRecording(), false, "a denied microphone leaves the page not recording");
  ok(denied.statusText().length > 0, "…and says something");
  eq(pendingTimers(), 0, "…with no cap timer left running");

  // start() twice is one recording; stop() twice is one stop.
  const w = bootMic();
  await w.mic.start();
  await w.mic.start();
  await flush();
  deep(w.rec.log, ["start"], "start() while recording is a no-op");
  w.mic.stop();
  w.mic.stop();
  deep(w.rec.log, ["start", "stop"], "stop() when not recording is a no-op");
}

/* --------------------------------------------------------------------------- *
 * B7b. §10 assumption 15's CONSEQUENCE — the browser encodes WAV for the hosted ear
 * --------------------------------------------------------------------------- *
 * The gateway answers HTTP 500 to webm/Opus, ogg/Opus and mp4/AAC and transcribes a
 * 16 kHz mono RIFF/WAVE word-perfect (probed live, 2026-09-03). A `MediaRecorder` cannot
 * produce a WAV, so the hosted path builds one itself. THE ASSERTION THAT MATTERS is the
 * one that parses `mic.js`'s output with the SERVER's own RIFF walker — one test pinning
 * both halves of the contract with no server and no browser, exactly as
 * `sim/test_wav_decode.mjs` does for the voice.
 * --------------------------------------------------------------------------- */
{
  // ---- the encoder, against functions/api/_lib/wav.js -----------------------
  const w = bootMic();
  const tone = (n, rate) => {
    const f = new Float32Array(n);
    for (let i = 0; i < n; i++) f[i] = Math.sin((2 * Math.PI * 440 * i) / rate) * 0.5;
    return f;
  };
  const frames = tone(48000, 48000);          // one second at a browser's usual rate
  const wav = w.mic.encodeWav([frames], frames.length, 48000);

  eq(wav.length, 44 + 16000 * 2, "one second at 48 kHz becomes 16 000 samples plus a 44-byte header");
  const parsed = wavlib.pcmFromAudio(wav, { sampleRate: 22050, channels: 1 });
  eq(parsed.container, "wav", "THE SERVER'S OWN RIFF WALKER READS IT as a wav");
  eq(parsed.sampleRate, 16000, "…at 16 000 Hz — the rate litellm-stt-setup.md says matters");
  eq(parsed.channels, 1, "…mono");
  eq(parsed.pcm.length, 16000 * 2, "…with the expected PCM length");
  // The same header fields the ffmpeg-produced control clip carried — the one the gateway
  // actually transcribed on 2026-09-03. Matching it is the closest a hermetic test can get
  // to "this is the thing that worked".
  deep({ rate: parsed.sampleRate, ch: parsed.channels, bits: 16, container: parsed.container },
       { rate: 16000, ch: 1, bits: 16, container: "wav" },
       "…identical in shape to the control WAV the gateway accepted live");

  // The route agrees: this is a container it will forward, and it sniffs as one.
  const kind = route.audioKind(wav, null);
  eq(kind.ext, "wav", "the route sniffs the browser's own file as a wav");
  ok(envmod.readConfig(FULL).sttFormats.includes(kind.ext),
     "…and it is inside DEMO_STT_FORMATS, so it is forwarded rather than refused");

  // Never upsample: a header claiming a rate the audio does not have wrecks a transcript.
  const low = w.mic.encodeWav([tone(8000, 8000)], 8000, 8000);
  eq(wavlib.pcmFromAudio(low, { sampleRate: 22050, channels: 1 }).sampleRate, 8000,
     "audio already below 16 kHz keeps its TRUE rate — the header never lies");
  // No frames at all is a bare 44-byte header. The server's parser REFUSES it (a WAV with
  // no data chunk is unreadable) — and it can never get there, because 44 bytes is far
  // under both the client's floor and DEMO_MIN_AUDIO_BYTES. Two independent guards, and
  // the cheap one runs first.
  const empty = w.mic.encodeWav([], 0, 48000);
  eq(empty.length, 44, "no frames is a bare 44-byte RIFF header");
  ok(empty.length < envmod.readConfig(FULL).minAudioBytes,
     "…which is under DEMO_MIN_AUDIO_BYTES, so it is refused for free before any parser sees it");
  let refused = null;
  try { wavlib.pcmFromAudio(empty, { sampleRate: 22050, channels: 1 }); } catch (e) { refused = e.kind; }
  eq(refused, "unreadable", "…and the server's parser would refuse it anyway");
  // Out-of-range samples clamp rather than wrapping into noise.
  const hot = w.mic.encodeWav([new Float32Array([2, -2, NaN, 0])], 4, 16000);
  const dv = new DataView(hot.buffer, hot.byteOffset);
  deep([dv.getInt16(44, true), dv.getInt16(46, true), dv.getInt16(48, true)], [32767, -32767, 0],
       "samples outside [-1,1] and NaN clamp instead of wrapping");

  // ---- and it is what actually goes on the wire ----------------------------
  const live = bootMic({ realCapture: true });
  await live.mic.start();
  await flush();
  eq(live.mic.isRecording(), true, "the hosted path opens its own capture");
  deep(live.gum[0], { audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } },
       "…asking getUserMedia for mono, which is what the encoder writes");
  ok(live.audioCtx.processors.length === 1, "…through exactly one ScriptProcessor");

  // Feed it a second of audio, the way a browser would.
  const node = live.audioCtx.processors[0];
  for (let i = 0; i < 12; i++) {
    node.onaudioprocess({ inputBuffer: { getChannelData: () => tone(4096, 48000) } });
  }
  await advance(15001);                        // the hard stop fires and encodes
  await flush();
  eq(live.audioCtx.closed, true, "the AudioContext is CLOSED on stop — no mic left running");
  eq(live.posts.length, 1, "one POST");
  eq(live.posts[0].url, ORIGIN + "/api/transcribe", "…to the same-origin route");
  eq(live.posts[0].init.headers["Content-Type"], "audio/wav",
     "…AS audio/wav, not the webm the gateway answers 500 to");
  const blob = live.posts[0].init.body;
  ok(blob.bytes && blob.bytes.length > 44, "…carrying real encoded bytes");
  const onWire = wavlib.pcmFromAudio(blob.bytes, { sampleRate: 22050, channels: 1 });
  eq(onWire.container, "wav", "THE BYTES ON THE WIRE PARSE AS A WAV on the server side");
  eq(onWire.sampleRate, 16000, "…at 16 000 Hz");

  // ---- while the local sidecar still gets a MediaRecorder ------------------
  const home = bootMic({ realCapture: true, mode: { ears: () => false } });
  let threw = null;
  await home.mic.start().catch((e) => { threw = e; });
  await flush();
  // `MediaRecorder` in this harness throws on construction, which is exactly how we prove
  // the local path still reaches for it rather than the WAV encoder.
  eq(home.audioCtx.processors.length, 0,
     "the LOCAL path does not build an AudioContext — it still uses MediaRecorder, unchanged");
  eq(home.mic.isRecording(), false, "…and a MediaRecorder that will not construct fails safely");
  ok(home.statusText().length > 0, "…with an honest status line, never a silent dead button");
}

/* --------------------------------------------------------------------------- *
 * B8. The source-level guards the other suites expect to keep holding
 * --------------------------------------------------------------------------- */
{
  for (const m of ["start", "stop", "toggle", "setSttBase"]) {
    ok(MIC_SRC.includes(m + ":") || MIC_SRC.includes("function " + m), `mic.js still exposes ${m}`);
  }
  ok(MIC_SRC.includes("events/remote-chat"),
     "mic.js still publishes the transcript as a child utterance on events/remote-chat");
  ok(!/graphlings|mattvalancy|pages\.dev/i.test(MIC_SRC),
     "mic.js names no deployment hostname — the base comes from the mode machine (C3)");
  ok(/\bsk-[A-Za-z0-9_-]{16,}/.test(MIC_SRC) === false, "mic.js carries no key-shaped literal");
}

/* --------------------------------------------------------------------------- */
if (fails.length) {
  console.error(`✗ test_demo_ears: ${fails.length} failure(s) of ${asserts}`);
  for (const f of fails) console.error("  - " + f);
  process.exit(1);
}
console.log(
  `✓ test_demo_ears: the ears hold their contract (${asserts} assertions, ${sweeps} secret sweeps, 0 leaks) — ` +
  `both byte caps with the floor costing nothing, the per-IP windows, our own AbortSignal timeout, ` +
  `an unset DEMO_STT_MODEL making zero upstream calls, a hostile upstream degrading per-turn instead of ` +
  `taking the page down, the container allowlist keeping a webm/Opus 500 from degrading the whole page ` +
  `(assumption 15, settled live 2026-09-03), the browser's own 16 kHz WAV read back by the SERVER's RIFF ` +
  `walker, and the 15 s hard stop proven to stop a recorder`,
);
