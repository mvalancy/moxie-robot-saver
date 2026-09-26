/* test_demo_proxy — §10–11: POST /api/speech, the Tunnel/Access path, the closed envelope. Run via the entry file, never alone. */
import {
  BASE, C, FORBIDDEN, FULL, KEY, P, assertClean, call,
  chat, deep, eq, fails, fresh, hmac, join, limits,
  ok, pcmBytes, readFileSync, repo, sent, speech, upstreamCalls, wav,
  wire, wire2,
} from "./harness.mjs";

/* =========================================================================== *
 * 10. §3.2 — POST /api/speech, and its own caps
 * =========================================================================== */
{
  fresh();
  const c = await call(chat, "/api/chat", { text: "hi moxie" });
  const ticket = c.body.speech[0].ticket;
  eq(c.body.speech.length, 1, "one speech ticket per single-chunk turn");
  eq(c.body.speech[0].chunk_num, 0, "chunk 0");
  eq(c.body.speech[0].event_id, JSON.parse(c.body.messages[0].payload).event_id,
     "the ticket's event_id matches the chat reply's");
  ok(!ticket.includes(KEY), "the ticket does not contain the key");

  const s = await call(speech, "/api/speech", { ticket });
  eq(s.res.status, 200, "a valid ticket is redeemed");
  eq(s.body.reason, null, "…with no reason");
  eq(s.body.messages.length, 1, "…and one TTS message");
  ok(s.body.messages[0].topic.endsWith("/commands/tts"), "the topic suffix route() dispatches on");

  const p = JSON.parse(s.body.messages[0].payload);
  deep(Object.keys(p).sort(), ["audio", "chunk_num", "event_id", "marks", "request_source"],
       "the CloudTTSResponse field set (tts.py:369-382)");
  eq(p.request_source, "ROBOT_TTS_REQUEST", "request_source");
  eq(p.audio.sample_rate, 22050, "the WAV HEADER's own rate, not the configured one");
  eq(p.audio.channels, 1, "the WAV header's own channel count");
  deep(p.marks, [], "marks is [] in P0 — the mouth follows the audio envelope");
  ok(p.audio.buffer.length > 100, "there is base64 PCM in the buffer");
  ok(!/[^A-Za-z0-9+/=]/.test(p.audio.buffer), "the buffer is plain base64");

  // The header's rate really is carried, not the configured one: a 16 kHz WAV from a
  // deployment configured for 22050 must report 16000.
  fresh();
  P.plan = { speech: { audio: wav.writeWav(pcmBytes(100), { sampleRate: 16000, channels: 1, bitsPerSample: 16 }) } };
  const c2 = await call(chat, "/api/chat", { text: "hi again" });
  const s2 = await call(speech, "/api/speech", { ticket: c2.body.speech[0].ticket });
  eq(JSON.parse(s2.body.messages[0].payload).audio.sample_rate, 16000,
     "SNIFF THE BYTES: the header's 16000 wins over the configured 22050");

  // The upstream body is server-built here too.
  eq(sent[1].url, BASE + "/audio/speech", "the upstream path is /audio/speech");
  const up = JSON.parse(sent[1].opt.body);
  deep(Object.keys(up).sort(), ["input", "model", "response_format", "voice"], "the server-built TTS body");
  eq(up.model, "test-voice-model", "the TTS model comes from DEMO_TTS_MODEL");
  eq(up.response_format, "wav", "the format comes from DEMO_TTS_FORMAT");
  eq(up.input, "Hi there! Want to hear a joke?", "the input is the text WE wrote");

  // `voice` IS ALWAYS SENT: the gateway REQUIRES the field and ignores its value
  // (`mqtt/moxie_sdk/tts.py`), and omitting it was measured to answer HTTP 500.
  // `test-voice-model` → tail `model`, which is a word, so that is the derived voice.
  eq(up.voice, "model", "a `voice` field is ALWAYS sent — omitting it is an upstream 500");
  eq(wire2.voiceForModel("piper-amy"), "amy", "piper-amy derives the voice `amy` (tts.py:80-90)");
  eq(wire2.voiceForModel("piper-ryan"), "ryan", "piper-ryan derives `ryan`");
  eq(wire2.voiceForModel("tts-1"), "alloy", "a non-word suffix falls back to OpenAI's default voice");
  eq(wire2.voiceForModel(""), "alloy", "…as does an empty model name");
  eq(wire2.readConfig({ ...FULL }).ttsVoice, "model", "the derived voice lands on the config");
  eq(wire2.readConfig({ ...FULL, DEMO_TTS_MODEL: "" }).ttsVoice, "",
     "…and stays empty with no TTS model, since the route cannot run then anyway");

  fresh();
  const withVoice = { ...FULL, DEMO_TTS_VOICE: "amy" };
  const c3 = await call(chat, "/api/chat", { text: "hi" }, null, withVoice);
  await call(speech, "/api/speech", { ticket: c3.body.speech[0].ticket }, null, withVoice);
  eq(JSON.parse(sent[1].opt.body).voice, "amy", "an explicit DEMO_TTS_VOICE overrides the derivation");

  // A JSON body where audio was expected is upstream_down, NEVER noise in a child's ear —
  // and the model name inside that JSON does not reach the visitor (assertClean).
  fresh();
  const c4 = await call(chat, "/api/chat", { text: "hi" });
  P.plan = { speech: { status: 200, body: JSON.stringify({ error: { message: "model test-voice-model not found at " + BASE } }) } };
  const bad = await call(speech, "/api/speech", { ticket: c4.body.speech[0].ticket });
  eq(bad.res.status, 503, "a JSON body where audio was expected is 503");
  eq(bad.body.reason, "upstream_down", "…with reason upstream_down");
  deep(bad.body.messages, [], "…and no message");

  // An 8-bit WAV is refused rather than played as garbage.
  fresh();
  const c5 = await call(chat, "/api/chat", { text: "hi" });
  P.plan = { speech: { audio: wav.writeWav(pcmBytes(100), { sampleRate: 22050, channels: 1, bitsPerSample: 8 }) } };
  const eight = await call(speech, "/api/speech", { ticket: c5.body.speech[0].ticket });
  eq(eight.body.reason, "upstream_down", "an 8-bit WAV is upstream_down, not garbage audio");

  /* ------------------------------------------------------------------------- *
   * 10c. THE RAW-BODY PASSTHROUGH — a 200 that is not the format we asked for
   * ------------------------------------------------------------------------- *
   * `_lib/wav.js` once passed any unrecognised body through as `container:"raw"`, which
   * shipped an upstream body verbatim at status 200 (or seconds of static, for an mp3).
   * Every case carries the model id and base URL INSIDE the body, so `assertClean` (which
   * decodes the buffer) is the leak half and the `reason` checks the correctness half.
   * The `data: ` frame is what a streaming LiteLLM front end emits; its prefix is why a
   * `{` sniff alone never fired.
   * ------------------------------------------------------------------------- */
  const HOSTILE = "model test-voice-model missing at " + BASE + " key " + KEY;
  const withMagic = (magic, n) => {
    const b = new Uint8Array(magic.length + n);
    for (let i = 0; i < magic.length; i++) b[i] = magic[i];
    for (let i = 0; i < n; i++) b[magic.length + i] = (i * 7) & 0xff;
    return b;
  };
  for (const [label, body, headers] of [
    ["a text/plain 200", HOSTILE, { "Content-Type": "text/plain" }],
    ["an SSE error frame", 'data: {"error":{"message":"' + HOSTILE + '"}}\n\n',
     { "Content-Type": "text/event-stream" }],
    ["an mp3 (ID3) body", withMagic([0x49, 0x44, 0x33, 0x03], 400), { "Content-Type": "audio/mpeg" }],
    ["a webm (EBML) body", withMagic([0x1a, 0x45, 0xdf, 0xa3], 400), { "Content-Type": "audio/webm" }],
    ["an Ogg body", withMagic([0x4f, 0x67, 0x67, 0x53], 400), { "Content-Type": "audio/ogg" }],
  ]) {
    fresh();
    const cN = await call(chat, "/api/chat", { text: "hi" });
    P.plan = { speech: { status: 200, body, headers } };
    const r = await call(speech, "/api/speech", { ticket: cN.body.speech[0].ticket });
    eq(r.res.status, 503, `${label} where wav was requested is 503`);
    eq(r.body.reason, "upstream_down", `${label} -> upstream_down`);
    eq(r.body.degraded, true, `${label}: the page DEGRADES rather than playing it`);
    deep(r.body.messages, [], `${label}: NO message, so nothing is base64'd to a visitor`);
  }

  // …and the raw branch is PRESERVED, because `DEMO_TTS_FORMAT=pcm` is a supported
  // configuration (spec §3.2 "anything else → treat as raw PCM", §5). The bug was never
  // that the branch existed — it was that a branch correct only under `pcm` was live
  // under the default `wav`.
  fresh();
  const pcmEnv = { ...FULL, DEMO_TTS_FORMAT: "pcm", DEMO_TTS_SAMPLE_RATE: "16000" };
  const cPcm = await call(chat, "/api/chat", { text: "hi" }, null, pcmEnv);
  const headerless = pcmBytes(300);
  P.plan = { speech: { status: 200, body: headerless, headers: { "Content-Type": "application/octet-stream" } } };
  const sPcm = await call(speech, "/api/speech", { ticket: cPcm.body.speech[0].ticket }, null, pcmEnv);
  eq(JSON.parse(sent[1].opt.body).response_format, "pcm", "the gateway is asked for pcm");
  eq(sPcm.res.status, 200, "DEMO_TTS_FORMAT=pcm STILL accepts a headerless body");
  const pPcm = JSON.parse(sPcm.body.messages[0].payload);
  eq(pPcm.audio.sample_rate, 16000, "…at the CONFIGURED rate — the one case where that is right");
  ok(Buffer.from(pPcm.audio.buffer, "base64").equals(Buffer.from(headerless)),
     "…carrying the bytes verbatim, byte for byte");

  // ONLY THE FORMAT GATE CATCHES THIS: an even-length, high-entropy body with no magic
  // number is exactly the audio ordered under `pcm` and an opaque blob under `wav`. Only
  // the format we asked for tells them apart, so this fails if `speech.js` stops passing
  // `format`; the magic-number and printable-text guards are defence in depth.
  const opaque = withMagic([0x00, 0x01, 0xfe, 0xff], 512);
  fresh();
  const cOp = await call(chat, "/api/chat", { text: "hi" });
  P.plan = { speech: { status: 200, body: opaque } };
  const rOp = await call(speech, "/api/speech", { ticket: cOp.body.speech[0].ticket });
  eq(rOp.res.status, 503, "an opaque binary 200 under DEMO_TTS_FORMAT=wav is 503");
  eq(rOp.body.reason, "upstream_down", "…reason upstream_down");
  deep(rOp.body.messages, [], "…and it is NEVER base64'd to a visitor as PCM");

  fresh();
  const cOp2 = await call(chat, "/api/chat", { text: "hi" }, null, pcmEnv);
  P.plan = { speech: { status: 200, body: opaque } };
  const rOp2 = await call(speech, "/api/speech", { ticket: cOp2.body.speech[0].ticket }, null, pcmEnv);
  eq(rOp2.res.status, 200, "…while THE SAME BYTES under DEMO_TTS_FORMAT=pcm are the audio we ordered");
  ok(Buffer.from(JSON.parse(rOp2.body.messages[0].payload).audio.buffer, "base64").equals(Buffer.from(opaque)),
     "…and arrive verbatim — one gate, two configurations, not a new denylist");

  // Even under `pcm` there are two cheap guards, because a headerless body has nothing to
  // sniff and "is this audio?" is otherwise undecidable.
  for (const [label, body] of [
    ["a text/plain body", HOSTILE + " ".repeat(40)],
    ["an odd byte length", withMagic([0x00], 300)],
    ["an mp3, from a gateway ignoring response_format", withMagic([0x49, 0x44, 0x33, 0x03], 400)],
  ]) {
    fresh();
    const cQ = await call(chat, "/api/chat", { text: "hi" }, null, pcmEnv);
    P.plan = { speech: { status: 200, body } };
    const r = await call(speech, "/api/speech", { ticket: cQ.body.speech[0].ticket }, null, pcmEnv);
    eq(r.body.reason, "upstream_down", `${label} is refused even under DEMO_TTS_FORMAT=pcm`);
    deep(r.body.messages, [], `${label}: …with no message`);
  }

  // The parser's own contract, exercised directly: ABSENT MEANS STRICT.
  {
    const plain = new TextEncoder().encode(HOSTILE);
    let kinds = [];
    for (const fb of [{ sampleRate: 22050 }, { sampleRate: 22050, format: "wav" }]) {
      try { wav.pcmFromAudio(plain, fb); kinds.push("PASSED IT THROUGH"); }
      catch (e) { kinds.push(e.kind); }
    }
    deep(kinds, ["unreadable", "unreadable"],
         "pcmFromAudio with no format reads STRICT — a caller that does not say gets wav");
    eq(wav.pcmFromAudio(pcmBytes(50), { sampleRate: 8000, format: "pcm" }).container, "raw",
       "…and `pcm` still opens the raw branch");
  }

  /* ------------------------------------------------------------------------- *
   * 10d. THE SPENT SET KEYS ON BYTES, NOT ON A SPELLING
   * ------------------------------------------------------------------------- *
   * A 32-byte HMAC is 43 base64url characters, so the last character carries two unread
   * bits and four spellings of one ticket verify. Keyed on the raw string, one paid chat
   * turn bought four TTS calls per isolate.
   * ------------------------------------------------------------------------- */
  fresh();
  const cRep = await call(chat, "/api/chat", { text: "hi" });
  const t0 = cRep.body.speech[0].ticket;
  const [ver, payloadSeg, macSeg] = t0.split(".");
  eq(macSeg.length, 43, "an HMAC-SHA-256 is 43 base64url characters");
  const cfgRep = wire2.readConfig(FULL);
  const spellings = [];
  for (const chx of "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_") {
    const cand = ver + "." + payloadSeg + "." + macSeg.slice(0, -1) + chx;
    if (cand === t0) continue;
    if ((await hmac.verifyTicket(cfgRep, cand)).ok) spellings.push(cand);
  }
  eq(spellings.length, 3, "THREE other spellings of the same MAC verify — the malleability is real");
  // A `+`/`/`/`=` re-encoding is NOT one of them, and saying so is the honest half.
  ok(!(await hmac.verifyTicket(cfgRep, ver + "." + payloadSeg + "." + macSeg + "=")).ok,
     "…while a padded MAC does not verify at all: the alphabet gate already refused it");

  const before = upstreamCalls();
  eq((await call(speech, "/api/speech", { ticket: t0 })).res.status, 200, "the ticket is redeemed ONCE");
  for (const cand of spellings) {
    const r = await call(speech, "/api/speech", { ticket: cand });
    eq(r.body.reason, "bad_ticket", "a RE-SPELLED ticket is a replay, not a second turn");
    deep(r.body.messages, [], "…and produces no audio");
  }
  eq(upstreamCalls() - before, 1,
     "one paid chat turn buys exactly ONE TTS call, whatever the ticket is spelled like");

  /* ------------------------------------------------------------------------- *
   * 10e. THE SWEEP ITSELF — does `assertClean` actually see inside base64?
   * ------------------------------------------------------------------------- *
   * Feed the REAL sweep a buffer that decodes to the key, check it fails, then drop that
   * expected failure from the ledger.
   * ------------------------------------------------------------------------- */
  {
    const poison = (s) => JSON.stringify({
      messages: [{ topic: "t", payload: JSON.stringify({ audio: { buffer: Buffer.from(s, "latin1").toString("base64") } }) }],
    });
    // Two separate poisons, so neither half of the decoded sweep can hide behind the
    // other: the KEY alone (no URL in it) pins the FORBIDDEN list, the base URL alone pins
    // the URL regex.
    for (const [what, secret] of [
      ["the API key", KEY],
      // Deliberately a host that is NOT in FORBIDDEN, so this pins the URL regex rather
      // than being caught a second time by the list above.
      ["a URL nobody listed", "https://exfil.invalid/leak"],
    ]) {
      const at = fails.length;
      await assertClean(new Response(poison("junk " + secret + " junk"), { status: 200 }), "SELF-TEST");
      const caught = fails.length - at;
      fails.length = at;                  // that failure was the point; it is not a failure
      ok(caught > 0, `assertClean DECODES the buffer — ${what} hidden in base64 is CAUGHT`);
    }

    // …and every shape that is not audio must neither throw nor false-positive, or the
    // sweep would fail on the ~1000 refusals that carry no audio at all.
    for (const b of [
      "not json at all",
      "",
      JSON.stringify({ messages: "nope" }),
      JSON.stringify({ messages: [null, 42, { payload: "not json" }] }),
      JSON.stringify({ messages: [{ payload: JSON.stringify({ audio: null }) }] }),
      JSON.stringify({ messages: [{ payload: JSON.stringify({ audio: { buffer: 42 } }) }] }),
      JSON.stringify({ messages: [{ payload: JSON.stringify({ audio: { buffer: "" } }) }] }),
      JSON.stringify({ messages: [{ payload: JSON.stringify({ audio: { buffer: "!!! not base64 !!!" } }) }] }),
    ]) {
      const n = fails.length;
      await assertClean(new Response(b, { status: 200 }), "SELF-TEST benign");
      eq(fails.length, n, `a body with no usable audio neither throws nor false-positives: ${b.slice(0, 40)}`);
    }
  }

  // Its own rate limit and its own unit cost.
  fresh();
  const speechEnv = { ...FULL, DEMO_SPEECH_PER_MIN: "2", DEMO_CHAT_PER_MIN: "50" };
  const tickets = [];
  for (let i = 0; i < 3; i++) {
    tickets.push((await call(chat, "/api/chat", { text: "hi " + i }, null, speechEnv)).body.speech[0].ticket);
  }
  eq((await call(speech, "/api/speech", { ticket: tickets[0] }, null, speechEnv)).res.status, 200, "speech 1 of 2");
  eq((await call(speech, "/api/speech", { ticket: tickets[1] }, null, speechEnv)).res.status, 200, "speech 2 of 2");
  const third = await call(speech, "/api/speech", { ticket: tickets[2] }, null, speechEnv);
  eq(third.res.status, 429, "speech 3 is 429 (DEMO_SPEECH_PER_MIN=2)");
  eq(third.body.reason, "rate_limited", "…with reason rate_limited");

  // A missing / non-string / empty ticket, and any other key, is refused for free.
  fresh();
  for (const [label, payload] of [
    ["no ticket key", { text: "say this for free" }],
    ["an empty ticket", { ticket: "" }],
    ["a non-string ticket", { ticket: 42 }],
    ["a text field instead", { text: "say this for free please" }],
  ]) {
    const r = await call(speech, "/api/speech", payload);
    eq(r.res.status, 400, `${label} is 400`);
    eq(r.body.reason, "bad_ticket", `${label} reason`);
  }
  eq(upstreamCalls(), 0, "THERE IS NO TEXT FIELD: /api/speech cannot be driven without a ticket");
}

/* =========================================================================== *
 * 10b. The Cloudflare Tunnel / Cloudflare Access path
 * =========================================================================== *
 * A tunnel behind Cloudflare Access answers an unauthenticated server fetch with an HTML
 * LOGIN PAGE AT STATUS 200 — indistinguishable from a broken gateway. So a service token
 * can be configured, half a token is a refusal, and a non-JSON reply has its own reason.
 */
{
  const ID = "abcdef0123456789.access";
  const SECRET = "access-secret-testonly-0123456789abcdef";
  const WITH_TOKEN = { ...FULL, DEMO_GATEWAY_ACCESS_CLIENT_ID: ID, DEMO_GATEWAY_ACCESS_CLIENT_SECRET: SECRET };
  const gatedForbidden = [...FORBIDDEN, ID, SECRET];

  /** The sweep, widened to the service token: neither half may ever leave either. */
  async function assertCleanGated(res, label) {
    const text = await res.clone().text();
    let headerText = "";
    for (const [k, v] of res.headers.entries()) headerText += k + ": " + v + "\n";
    for (const secret of gatedForbidden) {
      ok(!text.includes(secret), `${label}: the BODY leaked ${JSON.stringify(secret.slice(0, 12))}…`);
      ok(!headerText.includes(secret), `${label}: a HEADER leaked ${JSON.stringify(secret.slice(0, 12))}…`);
    }
    ok(!/CF-Access/i.test(text), `${label}: the body names a CF-Access header`);
    ok(!/CF-Access/i.test(headerText), `${label}: a response header is a CF-Access header`);
  }

  // (a) NEITHER half set: nothing changes. This is the property that matters most — a
  // plain public tunnel must be completely unaffected by the feature existing.
  fresh();
  await call(chat, "/api/chat", { text: "hi" });
  deep(Object.keys(sent[0].opt.headers).sort(), ["Accept", "Authorization", "Content-Type"],
       "with no service token, the upstream headers are EXACTLY what they were");

  // (b) BOTH halves set: the two headers ride every upstream call, on both routes, in the
  // shape Cloudflare Access expects for a non-interactive client.
  fresh();
  const c = await call(chat, "/api/chat", { text: "hi" }, null, WITH_TOKEN);
  await assertCleanGated(c.res, "/api/chat with a service token");
  const h = sent[0].opt.headers;
  eq(h["CF-Access-Client-Id"], ID, "CF-Access-Client-Id is sent on /chat/completions");
  eq(h["CF-Access-Client-Secret"], SECRET, "CF-Access-Client-Secret is sent on /chat/completions");
  eq(h.Authorization, "Bearer " + KEY, "…alongside the gateway key, not instead of it");
  const s = await call(speech, "/api/speech", { ticket: c.body.speech[0].ticket }, null, WITH_TOKEN);
  await assertCleanGated(s.res, "/api/speech with a service token");
  eq(s.res.status, 200, "the speech turn still succeeds");
  eq(sent[1].opt.headers["CF-Access-Client-Id"], ID, "CF-Access-Client-Id is sent on /audio/speech too");
  eq(sent[1].opt.headers["CF-Access-Client-Secret"], SECRET, "…and its secret");

  // (c) EXACTLY ONE half set is a MISCONFIGURATION, not a partial credential: calling
  // upstream half-credentialled would produce the very login page this exists to avoid,
  // while looking configured. So it is `gateway_not_configured` with ZERO upstream calls.
  for (const [label, env] of [
    ["a client id with no secret", { ...FULL, DEMO_GATEWAY_ACCESS_CLIENT_ID: ID }],
    ["a secret with no client id", { ...FULL, DEMO_GATEWAY_ACCESS_CLIENT_SECRET: SECRET }],
  ]) {
    fresh();
    const r = await call(chat, "/api/chat", { text: "hi" }, null, env);
    await assertCleanGated(r.res, "half a token: " + label);
    eq(r.res.status, 503, `${label} is 503`);
    eq(r.body.reason, "gateway_not_configured", `${label} answers gateway_not_configured`);
    eq(upstreamCalls(), 0, `${label} MAKES ZERO UPSTREAM CALLS`);
    const sp = await call(speech, "/api/speech", { ticket: "v1.a.b" }, null, env);
    eq(sp.body.reason, "gateway_not_configured", `${label}: /api/speech too`);
    eq(upstreamCalls(), 0, `${label}: still zero upstream calls`);
    // …and the operator is told WHICH half, server-side only. `notes` is never on the wire.
    const cfgHalf = (await import(join(repo, "functions", "api", "_lib", "env.js"))).readConfig(env);
    ok(cfgHalf.notes.some((n) => /BOTH halves/.test(n)),
       `${label}: readConfig explains the misconfiguration in its notes`);
    ok(!JSON.stringify(r.body).includes("BOTH halves"), `${label}: …and that note stays off the wire`);
  }

  // (d) THE LOGIN PAGE ITSELF. An Access-gated tunnel answers 200 + text/html. That is not
  // `upstream_down` — the brain may be perfectly healthy behind a locked door — and the
  // two have completely different fixes.
  const LOGIN_PAGE =
    "<!DOCTYPE html><html><head><title>Sign in · Cloudflare Access</title></head>" +
    "<body><h1>Sign in to continue</h1><form action=\"/cdn-cgi/access/login\"></form></body></html>";

  for (const [label, plan_] of [
    ["a 200 HTML login page", { chat: { status: 200, body: LOGIN_PAGE, headers: { "Content-Type": "text/html; charset=utf-8" } } }],
    ["a 302-shaped HTML body", { chat: { status: 200, body: LOGIN_PAGE, headers: { "Content-Type": "text/html" } } }],
    ["HTML with no Content-Type", { chat: { status: 200, body: LOGIN_PAGE } }],
  ]) {
    fresh();
    P.plan = plan_;
    const r = await call(chat, "/api/chat", { text: "hi" }, null, FULL);
    await assertCleanGated(r.res, "gated: " + label);
    eq(r.res.status, 503, `${label} is 503`);
    ok(["gateway_unreachable_or_gated", "upstream_down"].includes(r.body.reason),
       `${label} is a 503 reason, got ${JSON.stringify(r.body.reason)}`);
    deep(r.body.messages, [], `${label} produces no message`);
    ok(!/Sign in|cdn-cgi|Cloudflare/i.test(JSON.stringify(r.body)),
       `${label}: NOTHING from the login page reaches the visitor`);
  }

  // The one that carries the Content-Type a real Access page carries gets the DISTINCT
  // reason, which is the whole point of the addition: it is diagnosable.
  fresh();
  P.plan = { chat: { status: 200, body: LOGIN_PAGE, headers: { "Content-Type": "text/html; charset=utf-8" } } };
  const gated = await call(chat, "/api/chat", { text: "hi" });
  eq(gated.body.reason, "gateway_unreachable_or_gated",
     "AN HTML LOGIN PAGE IS DIAGNOSED, not folded into upstream_down");
  eq(gated.res.headers.get("Retry-After"), "60", "…with upstream_down's Retry-After, so the page behaves identically");

  // /api/speech: an HTML page where AUDIO was expected would otherwise fall through the
  // RIFF check and be played to a child as several seconds of loud static.
  fresh();
  const c2 = await call(chat, "/api/chat", { text: "hi" });
  P.plan = { speech: { status: 200, body: LOGIN_PAGE, headers: { "Content-Type": "text/html" } } };
  const sGated = await call(speech, "/api/speech", { ticket: c2.body.speech[0].ticket });
  eq(sGated.res.status, 503, "an HTML login page at /audio/speech is 503");
  eq(sGated.body.reason, "gateway_unreachable_or_gated", "…and diagnosed as gated");
  deep(sGated.body.messages, [], "…with NO audio message — never static in a child's ear");

  // A gated turn still degrades the page rather than erroring it: the reason is in the
  // closed set that `sim/web/mode.js` understands, or the client would read it as healthy.
  const envelopeMod = await import(join(repo, "functions", "api", "_lib", "envelope.js"));
  ok(envelopeMod.REASONS.includes("gateway_unreachable_or_gated"),
     "the new reason is in the closed set");
  const modeSrc = readFileSync(join(repo, "sim", "web", "mode.js"), "utf8");
  ok(modeSrc.includes("gateway_unreachable_or_gated"),
     "…AND in sim/web/mode.js's matching list — an unknown reason there is coerced to null " +
     "and would be misread as a healthy turn");
}

/* =========================================================================== *
 * 11. §3.2 / §4.2 — the envelope is one shape, with a closed key set
 * =========================================================================== */
{
  const envelope = await import(join(repo, "functions", "api", "_lib", "envelope.js"));
  fresh();
  const responses = [];
  responses.push(await call(chat, "/api/chat", { text: "hi" }));
  responses.push(await call(chat, "/api/chat", { text: "x".repeat(9000) }));
  responses.push(await call(chat, "/api/chat", { text: "" }));
  responses.push(await call(chat, "/api/chat", { text: "hi" }, null, {}));
  responses.push(await call(speech, "/api/speech", { ticket: "v1.a.b" }));
  fresh();
  P.plan = { chat: { status: 500, body: "model test-brain-model key " + KEY } };
  responses.push(await call(chat, "/api/chat", { text: "hi" }));

  for (const { res, body } of responses) {
    deep(Object.keys(body), [...envelope.PUBLIC_KEYS], "every response has exactly PUBLIC_KEYS, in order");
    ok(body.reason === null || envelope.REASONS.includes(body.reason),
       `reason ${JSON.stringify(body.reason)} is in the closed set`);
    eq(res.headers.get("Cache-Control"), "no-store", "no-store on every reply");
    eq(res.headers.get("X-Content-Type-Options"), "nosniff", "nosniff on every reply");
    eq(res.headers.get("Access-Control-Allow-Origin"), null,
       "NO Access-Control-Allow-Origin, ever (§4.3 — not the wildcard sim/tts/server.py has)");
    ok(["live", "degraded"].includes(body.mode), "mode is live or degraded");
    ok(res.headers.get("X-Moxie-Mode") !== null, "X-Moxie-Mode rides every response");
    // §4.2: the caps the browser may know, and nothing else.
    deep(Object.keys(body.limits).sort(),
         ["chat_per_min", "max_audio_bytes", "max_input_chars", "max_record_ms", "max_tokens",
          "max_tts_chars", "min_audio_bytes"],
         "limits carries exactly the public caps — no model id, no URL");
  }
  ok(C.sweeps > 100, `assertClean ran on every response (${C.sweeps} sweeps)`);
}
