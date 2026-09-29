/* test_demo_proxy §10–11: POST /api/speech, the Tunnel/Access path, the closed envelope.
 * Run via the entry file. */
import {
  BASE, FORBIDDEN, FULL, KEY, P, assertClean, call,
  chat, deep, eq, fails, fresh, hmac, join,
  ok, pcmBytes, repo, sent, speech, upstreamCalls, wav, wire2,
} from "./harness.mjs";

/** A chat turn under `env`, then its ticket redeemed with the gateway answering `plan`. */
async function spoken(plan, env = FULL) {
  const c = await call(chat, "/api/chat", { text: "hi" }, null, env);
  if (plan) P.plan = { speech: plan };
  return call(speech, "/api/speech", { ticket: c.body.speech[0].ticket }, null, env);
}
const withMagic = (magic, n) => {
  const b = new Uint8Array(magic.length + n);
  b.set(magic);
  for (let i = 0; i < n; i++) b[magic.length + i] = (i * 7) & 0xff;
  return b;
};

/* 10. §3.2 — POST /api/speech and its own caps. */
{
  fresh();
  const c = await call(chat, "/api/chat", { text: "hi moxie" });
  const ticket = c.body.speech[0].ticket;
  deep([c.body.speech.length, c.body.speech[0].chunk_num], [1, 0], "one speech ticket (chunk 0) per single-chunk turn");
  eq(c.body.speech[0].event_id, JSON.parse(c.body.messages[0].payload).event_id, "the ticket's event_id matches the chat reply's");

  const s = await call(speech, "/api/speech", { ticket });
  eq(`${s.res.status} ${s.body.reason} ${s.body.messages.length}`, "200 null 1", "a valid ticket is redeemed for one TTS message");
  ok(s.body.messages[0].topic.endsWith("/commands/tts"), "the topic suffix route() dispatches on");
  const p = JSON.parse(s.body.messages[0].payload);
  deep(Object.keys(p).sort(), ["audio", "chunk_num", "event_id", "marks", "request_source"],
       "the CloudTTSResponse field set (tts.py:369-382)");
  deep([p.request_source, p.audio.sample_rate, p.audio.channels, p.marks], ["ROBOT_TTS_REQUEST", 22050, 1, []],
       "request_source, the WAV header's own rate/channels, marks []");
  ok(p.audio.buffer.length > 100 && !/[^A-Za-z0-9+/=]/.test(p.audio.buffer), "the buffer is plain base64 PCM");

  // SNIFF THE BYTES: a 16 kHz WAV from a deployment configured for 22050 reports 16000.
  fresh();
  const s2 = await spoken({ audio: wav.writeWav(pcmBytes(100), { sampleRate: 16000, channels: 1, bitsPerSample: 16 }) });
  eq(JSON.parse(s2.body.messages[0].payload).audio.sample_rate, 16000, "SNIFF THE BYTES: the header's 16000 wins over the configured 22050");

  // The upstream TTS body is server-built too.
  eq(sent[1].url, BASE + "/audio/speech", "the upstream path is /audio/speech");
  const up = JSON.parse(sent[1].opt.body);
  deep(Object.fromEntries(Object.entries(up).sort()), { input: "Hi there! Want to hear a joke?", model: "test-voice-model", response_format: "wav", voice: "model" },
       "the TTS body is exactly model/input(the text WE wrote)/voice/format — `voice` is ALWAYS sent (omitting it is an upstream 500)");
  for (const [model, voice] of [["piper-amy", "amy"], ["piper-ryan", "ryan"], ["tts-1", "alloy"], ["", "alloy"]]) {
    eq(wire2.voiceForModel(model), voice, `voiceForModel(${JSON.stringify(model)}) is ${voice} (tts.py:80-90)`);
  }
  eq(wire2.readConfig({ ...FULL, DEMO_TTS_MODEL: "" }).ttsVoice, "", "no TTS model => no derived voice");
  fresh();
  await spoken(null, { ...FULL, DEMO_TTS_VOICE: "amy" });
  eq(JSON.parse(sent[1].opt.body).voice, "amy", "an explicit DEMO_TTS_VOICE overrides the derivation");

  /* 10c. A 200 that is not the format we asked for is NEVER played. `_lib/wav.js` once passed
   * any unrecognised body through as raw PCM — an upstream body verbatim at 200, or static.
   * The hostile text is the leak half (assertClean decodes the buffer). */
  const HOSTILE = "model test-voice-model missing at " + BASE + " key " + KEY;
  const refusedUnderWav = [
    ["a JSON body", JSON.stringify({ error: { message: "model test-voice-model not found at " + BASE } }), {}],
    ["an 8-bit WAV", wav.writeWav(pcmBytes(100), { sampleRate: 22050, channels: 1, bitsPerSample: 8 }), {}],
    ["a text/plain 200", HOSTILE, { "Content-Type": "text/plain" }],
    ["an SSE error frame", 'data: {"error":{"message":"' + HOSTILE + '"}}\n\n', { "Content-Type": "text/event-stream" }],
    ["an mp3 (ID3) body", withMagic([0x49, 0x44, 0x33, 0x03], 400), { "Content-Type": "audio/mpeg" }],
    ["a webm (EBML) body", withMagic([0x1a, 0x45, 0xdf, 0xa3], 400), { "Content-Type": "audio/webm" }],
    ["an Ogg body", withMagic([0x4f, 0x67, 0x67, 0x53], 400), { "Content-Type": "audio/ogg" }],
    // ONLY THE FORMAT GATE CATCHES THIS: even-length, high-entropy, no magic — the audio
    // ordered under `pcm` and an opaque blob under `wav`.
    ["an opaque even-length binary", withMagic([0x00, 0x01, 0xfe, 0xff], 512), {}],
  ];
  for (const [label, body, headers] of refusedUnderWav) {
    fresh();
    const r = await spoken({ status: 200, body, headers });
    eq(`${r.res.status} ${r.body.reason} ${r.body.degraded}`, "503 upstream_down true", `${label} where wav was requested DEGRADES`);
    deep(r.body.messages, [], `${label}: NO message, so nothing is base64'd to a visitor`);
  }

  // DEMO_TTS_FORMAT=pcm is supported (§3.2 "anything else → raw PCM"): the same opaque bytes
  // are then the audio we ordered, verbatim, at the CONFIGURED rate.
  const pcmEnv = { ...FULL, DEMO_TTS_FORMAT: "pcm", DEMO_TTS_SAMPLE_RATE: "16000" };
  for (const body of [pcmBytes(300), withMagic([0x00, 0x01, 0xfe, 0xff], 512)]) {
    fresh();
    const r = await spoken({ status: 200, body, headers: { "Content-Type": "application/octet-stream" } }, pcmEnv);
    eq(JSON.parse(sent[1].opt.body).response_format, "pcm", "the gateway is asked for pcm");
    eq(r.res.status, 200, "DEMO_TTS_FORMAT=pcm accepts a headerless body");
    const pp = JSON.parse(r.body.messages[0].payload);
    eq(pp.audio.sample_rate, 16000, "…at the CONFIGURED rate — the one case where that is right");
    ok(Buffer.from(pp.audio.buffer, "base64").equals(Buffer.from(body)), "…carrying the bytes verbatim");
  }
  // …with two cheap guards even there, since "is this audio?" is otherwise undecidable.
  for (const [label, body] of [
    ["a text/plain body", HOSTILE + " ".repeat(40)],
    ["an odd byte length", withMagic([0x00], 300)],
    ["an mp3, from a gateway ignoring response_format", withMagic([0x49, 0x44, 0x33, 0x03], 400)],
  ]) {
    fresh();
    const r = await spoken({ status: 200, body }, pcmEnv);
    eq(`${r.body.reason} ${r.body.messages.length}`, "upstream_down 0", `${label} is refused even under DEMO_TTS_FORMAT=pcm`);
  }
  // The parser's own contract: ABSENT MEANS STRICT.
  for (const fb of [{ sampleRate: 22050 }, { sampleRate: 22050, format: "wav" }]) {
    let kind = "PASSED IT THROUGH";
    try { wav.pcmFromAudio(new TextEncoder().encode(HOSTILE), fb); } catch (e) { kind = e.kind; }
    eq(kind, "unreadable", `pcmFromAudio(${JSON.stringify(fb)}) reads STRICT — a caller that does not say gets wav`);
  }
  eq(wav.pcmFromAudio(pcmBytes(50), { sampleRate: 8000, format: "pcm" }).container, "raw", "…and `pcm` still opens the raw branch");

  /* 10d. THE SPENT SET KEYS ON BYTES, NOT A SPELLING. A 43-char base64url HMAC has two unread
   * bits in its last char, so four spellings verify; keyed on the string, one paid chat turn
   * bought four TTS calls. */
  fresh();
  const t0 = (await call(chat, "/api/chat", { text: "hi" })).body.speech[0].ticket;
  const [ver, payloadSeg, macSeg] = t0.split(".");
  const cfgRep = wire2.readConfig(FULL);
  const spellings = [];
  for (const chx of "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_") {
    const cand = ver + "." + payloadSeg + "." + macSeg.slice(0, -1) + chx;
    if (cand !== t0 && (await hmac.verifyTicket(cfgRep, cand)).ok) spellings.push(cand);
  }
  eq(spellings.length, 3, "THREE other spellings of the same MAC verify — the malleability is real");
  ok(!(await hmac.verifyTicket(cfgRep, t0 + "=")).ok, "…while a padded MAC does not verify at all");
  const before = upstreamCalls();
  eq((await call(speech, "/api/speech", { ticket: t0 })).res.status, 200, "the ticket is redeemed ONCE");
  for (const cand of spellings) {
    const r = await call(speech, "/api/speech", { ticket: cand });
    eq(`${r.body.reason} ${r.body.messages.length}`, "bad_ticket 0", "a RE-SPELLED ticket is a replay, not a second turn");
  }
  eq(upstreamCalls() - before, 1, "one paid chat turn buys exactly ONE TTS call, whatever the ticket is spelled like");

  /* 10e. THE SWEEP ITSELF: feed the real assertClean a buffer that decodes to a secret and
   * check it fails (the key pins the FORBIDDEN list; an unlisted host pins the URL regex). */
  const poison = (s) => JSON.stringify({
    messages: [{ topic: "t", payload: JSON.stringify({ audio: { buffer: Buffer.from(s, "latin1").toString("base64") } }) }],
  });
  for (const [what, secret] of [["the API key", KEY], ["a URL nobody listed", "https://exfil.invalid/leak"]]) {
    const at = fails.length;
    await assertClean(new Response(poison("junk " + secret + " junk"), { status: 200 }), "SELF-TEST");
    const caught = fails.length - at;
    fails.length = at;                    // that failure was the point
    ok(caught > 0, `assertClean DECODES the buffer — ${what} hidden in base64 is CAUGHT`);
  }
  // …and a body with no usable audio neither throws nor false-positives.
  for (const b of ["not json at all", "", JSON.stringify({ messages: "nope" }),
    JSON.stringify({ messages: [null, 42, { payload: "not json" }] }),
    ...[null, { buffer: 42 }, { buffer: "" }, { buffer: "!!! not base64 !!!" }].map((audio) =>
      JSON.stringify({ messages: [{ payload: JSON.stringify({ audio }) }] }))]) {
    const n = fails.length;
    await assertClean(new Response(b, { status: 200 }), "SELF-TEST benign");
    eq(fails.length, n, `a body with no usable audio neither throws nor false-positives: ${b.slice(0, 40)}`);
  }

  // Its own rate limit.
  fresh();
  const speechEnv = { ...FULL, DEMO_SPEECH_PER_MIN: "2", DEMO_CHAT_PER_MIN: "50" };
  const tickets = [];
  for (let i = 0; i < 3; i++) tickets.push((await call(chat, "/api/chat", { text: "hi " + i }, null, speechEnv)).body.speech[0].ticket);
  const statuses = [];
  for (const t of tickets) statuses.push((await call(speech, "/api/speech", { ticket: t }, null, speechEnv)).body.reason);
  deep(statuses, [null, null, "rate_limited"], "DEMO_SPEECH_PER_MIN=2: the third speech turn is rate_limited");

  // THERE IS NO TEXT FIELD: /api/speech cannot be driven without a ticket.
  fresh();
  for (const [label, payload] of [["no ticket key", { text: "say this for free" }], ["an empty ticket", { ticket: "" }],
    ["a non-string ticket", { ticket: 42 }]]) {
    const r = await call(speech, "/api/speech", payload);
    eq(`${r.res.status} ${r.body.reason}`, "400 bad_ticket", label);
  }
  eq(upstreamCalls(), 0, "a ticketless /api/speech makes ZERO upstream calls");
}

/* 10b. Cloudflare Tunnel / Access. An Access-gated tunnel answers an unauthenticated fetch
 * with an HTML LOGIN PAGE at 200, so a service token can be configured, half a token is a
 * refusal, and a login page has its own diagnosable reason. */
{
  const ID = "abcdef0123456789.access";
  const SECRET = "access-secret-testonly-0123456789abcdef";
  const WITH_TOKEN = { ...FULL, DEMO_GATEWAY_ACCESS_CLIENT_ID: ID, DEMO_GATEWAY_ACCESS_CLIENT_SECRET: SECRET };
  /** The sweep, widened to the service token: neither half may ever leave, nor any CF-Access name. */
  async function assertCleanGated(res, label) {
    const text = await res.clone().text();
    const headerText = [...res.headers.entries()].map(([k, v]) => k + ": " + v).join("\n");
    const hit = [...FORBIDDEN, ID, SECRET].filter((x) => text.includes(x) || headerText.includes(x));
    ok(!hit.length && !/CF-Access/i.test(text + headerText), `${label}: the service token leaked (${hit.map((x) => x.slice(0, 12))})`);
  }

  // (a) NEITHER half: a plain public tunnel is completely unaffected by the feature.
  fresh();
  await call(chat, "/api/chat", { text: "hi" });
  deep(Object.keys(sent[0].opt.headers).sort(), ["Accept", "Authorization", "Content-Type"],
       "with no service token, the upstream headers are EXACTLY what they were");

  // (b) BOTH halves ride every upstream call, on both routes, beside the gateway key.
  fresh();
  const c = await call(chat, "/api/chat", { text: "hi" }, null, WITH_TOKEN);
  await assertCleanGated(c.res, "/api/chat with a service token");
  const s = await call(speech, "/api/speech", { ticket: c.body.speech[0].ticket }, null, WITH_TOKEN);
  await assertCleanGated(s.res, "/api/speech with a service token");
  eq(s.res.status, 200, "the speech turn still succeeds");
  for (const [i, path] of [[0, "/chat/completions"], [1, "/audio/speech"]]) {
    const h = sent[i].opt.headers;
    deep([h["CF-Access-Client-Id"], h["CF-Access-Client-Secret"], h.Authorization], [ID, SECRET, "Bearer " + KEY],
         `the Access pair is sent on ${path}, alongside the gateway key`);
  }

  // (c) EXACTLY ONE half is a MISCONFIGURATION: calling half-credentialled would fetch the very
  // login page this exists to avoid. gateway_not_configured, zero calls, operator told which half.
  for (const [label, env] of [
    ["a client id with no secret", { ...FULL, DEMO_GATEWAY_ACCESS_CLIENT_ID: ID }],
    ["a secret with no client id", { ...FULL, DEMO_GATEWAY_ACCESS_CLIENT_SECRET: SECRET }],
  ]) {
    fresh();
    const r = await call(chat, "/api/chat", { text: "hi" }, null, env);
    await assertCleanGated(r.res, "half a token: " + label);
    const sp = await call(speech, "/api/speech", { ticket: "v1.a.b" }, null, env);
    eq(`${r.res.status} ${r.body.reason} ${sp.body.reason} ${upstreamCalls()}`,
       "503 gateway_not_configured gateway_not_configured 0", `${label}: both routes gateway_not_configured, ZERO upstream calls`);
    ok(wire2.readConfig(env).notes.some((n) => /BOTH halves/.test(n)) && !JSON.stringify(r.body).includes("BOTH halves"),
       `${label}: readConfig explains it in its notes, which stay off the wire`);
  }

  // (d) THE LOGIN PAGE. Not upstream_down (the brain may be healthy behind a locked door).
  const LOGIN_PAGE = "<!DOCTYPE html><html><head><title>Sign in · Cloudflare Access</title></head>" +
    "<body><h1>Sign in to continue</h1><form action=\"/cdn-cgi/access/login\"></form></body></html>";
  for (const [label, headers, reason] of [
    ["a text/html login page", { "Content-Type": "text/html; charset=utf-8" }, "gateway_unreachable_or_gated"],
    ["an HTML page with no Content-Type", {}, "upstream_down"],
  ]) {
    fresh();
    P.plan = { chat: { status: 200, body: LOGIN_PAGE, headers } };
    const r = await call(chat, "/api/chat", { text: "hi" });
    await assertCleanGated(r.res, "gated: " + label);
    eq(`${r.res.status} ${r.body.reason} ${r.res.headers.get("Retry-After")}`, `503 ${reason} 60`,
       `${label} is diagnosed as ${reason}, with upstream_down's Retry-After`);
    ok(!r.body.messages.length && !/Sign in|cdn-cgi|Cloudflare/i.test(JSON.stringify(r.body)),
       `${label}: NOTHING from the login page reaches the visitor`);
  }
  // At /audio/speech it would otherwise be played to a child as seconds of static.
  fresh();
  const sGated = await spoken({ status: 200, body: LOGIN_PAGE, headers: { "Content-Type": "text/html" } });
  eq(`${sGated.res.status} ${sGated.body.reason} ${sGated.body.messages.length}`, "503 gateway_unreachable_or_gated 0",
     "an HTML login page at /audio/speech is diagnosed as gated, with NO audio message");
}

/* 11. §3.2 / §4.2 — every response is one envelope shape with a closed key set. */
{
  const envelope = await import(join(repo, "functions", "api", "_lib", "envelope.js"));
  fresh();
  const responses = [
    await call(chat, "/api/chat", { text: "hi" }),
    await call(chat, "/api/chat", { text: "x".repeat(9000) }),
    await call(chat, "/api/chat", { text: "" }),
    await call(chat, "/api/chat", { text: "hi" }, null, {}),
    await call(speech, "/api/speech", { ticket: "v1.a.b" }),
  ];
  fresh();
  P.plan = { chat: { status: 500, body: "model test-brain-model key " + KEY } };
  responses.push(await call(chat, "/api/chat", { text: "hi" }));

  for (const { res, body } of responses) {
    const tag = `${res.status} ${body.reason}`;
    deep(Object.keys(body), [...envelope.PUBLIC_KEYS], `${tag}: exactly PUBLIC_KEYS, in order`);
    ok(body.reason === null || envelope.REASONS.includes(body.reason), `${tag}: reason is in the closed set`);
    deep(["Cache-Control", "X-Content-Type-Options", "Access-Control-Allow-Origin"].map((h) => res.headers.get(h)),
         ["no-store", "nosniff", null], `${tag}: no-store, nosniff, and NO Access-Control-Allow-Origin, ever (§4.3)`);
    ok(["live", "degraded"].includes(body.mode) && res.headers.get("X-Moxie-Mode") !== null, `${tag}: a mode, in body and header`);
    deep(Object.keys(body.limits).sort(),
         ["chat_per_min", "max_audio_bytes", "max_input_chars", "max_record_ms", "max_tokens", "max_tts_chars", "min_audio_bytes"],
         `${tag}: limits carries exactly the public caps — no model id, no URL`);
  }
}
