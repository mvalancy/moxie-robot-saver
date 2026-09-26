/* Part A §A6–A10: a hostile upstream degrades per turn, the bytes are sniffed and the
 * container allowlisted, the upstream body is BUILT not forwarded, the transcript, the envelope.
 */
import {
  BASE, C, FULL, KEY, call, clip, deep, envlib, envmod, eq, fresh, ok, route, sent, setPlan,
  upstreamCalls,
} from "./harness.mjs";

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
    setPlan({ status: upstream, body: hostile });
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
  setPlan({ status: 429, body: hostile, headers: { "Retry-After": "999999" } });
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
    setPlan(p);
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
 * Probed live: the gateway transcribes 16 kHz mono WAV and answers HTTP 500 to webm/Opus,
 * ogg/Opus and mp4/AAC. A 500 is `upstream_down` (503), which degrades the WHOLE PAGE, so
 * without the allowlist one microphone press would take down brain and voice too — after
 * paying for the call.
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
  setPlan({ text: "  Hi Moxie,   tell me a joke.  " });
  const good = await call(clip(4000), null, FULL, "a normal transcript");
  eq(good.res.status, 200, "a transcript is a 200");
  eq(good.body.transcript, "Hi Moxie, tell me a joke.", "whitespace is collapsed and trimmed");
  eq(good.body.reason, null, "…with no reason");
  eq(good.body.ok, true, "…and ok true");
  eq(good.body.degraded, false, "…not degraded");

  // Silence is a SUCCESS, not an error: the visitor simply did not speak.
  fresh();
  setPlan({ text: "" });
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
  setPlan({ text: "y".repeat(600) });
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
  setPlan({ status: 500, body: "boom" });
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
  ok(C.sweeps > 60, `assertClean ran on every response (${C.sweeps} sweeps)`);
}
