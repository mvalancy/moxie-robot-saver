/* Voice-loop tests — the optional local engines: TTS out (Piper) and STT in (faster-whisper),
 * against the REAL services' wire shapes. Skips (exit 0) a service that is not running; the
 * browser side of voice/ and mic.js is driven by test_audio, test_typed_turn and test_mic_spend.
 *
 * Run: node sim/test_voice.mjs   [TTS_BASE=http://127.0.0.1:8081] [STT_BASE=...]
 */
const TTS = process.env.TTS_BASE || "http://127.0.0.1:8081";
const STT = process.env.STT_BASE || "http://127.0.0.1:8082";
const fails = [];
const notes = [];
const ok = (cond, msg) => { if (!cond) fails.push(msg); };

async function reachable(base) {
  try {
    const r = await fetch(base + "/health", { signal: AbortSignal.timeout(2500) });
    return r.ok ? await r.json() : null;
  } catch { return null; }
}

// ---- 1. TTS: synthesizes real audio ----------------------------------------
const ttsHealth = await reachable(TTS);
if (!ttsHealth) {
  notes.push(`TTS not running at ${TTS} (skipped) — start: python3 sim/tts/server.py 8081`);
} else {
  ok(ttsHealth.ok === true, "TTS /health reports ok");
  ok(!!ttsHealth.voice, "TTS reports a loaded voice");
  const r = await fetch(`${TTS}/tts?text=${encodeURIComponent("Hello, I am Moxie.")}`,
                        { signal: AbortSignal.timeout(60000) });
  ok(r.ok, `TTS /tts returned ${r.status}`);
  const buf = new Uint8Array(await r.arrayBuffer());
  ok(buf.length > 2000, `TTS audio too small (${buf.length}b)`);
  // RIFF/WAVE header
  const tag = String.fromCharCode(...buf.slice(0, 4));
  ok(tag === "RIFF", `TTS audio is not RIFF/WAVE (got ${JSON.stringify(tag)})`);
}

// ---- 2. STT: returns the robot's DeepgramResponse shape ---------------------
const sttHealth = await reachable(STT);
if (!sttHealth) {
  notes.push(`STT not running at ${STT} (skipped) — start: python3 sim/stt/server.py 8082`);
} else if (!ttsHealth) {
  notes.push("STT reachable but TTS is not — skipping the round-trip (needs audio input)");
} else {
  // full loop: synthesize a known phrase, transcribe it back
  const phrase = "hello Moxie how are you today";
  const audio = await (await fetch(`${TTS}/tts?text=${encodeURIComponent(phrase)}`,
                                   { signal: AbortSignal.timeout(60000) })).arrayBuffer();
  const r = await fetch(`${STT}/stt`, {
    method: "POST", body: audio,
    headers: { "Content-Type": "audio/wav" },
    signal: AbortSignal.timeout(180000),
  });
  ok(r.ok, `STT /stt returned ${r.status}`);
  const dg = await r.json();
  // the exact shape the robot parses (perception-pipeline.md)
  ok(typeof dg.is_final === "boolean", "DeepgramResponse.is_final missing");
  ok(typeof dg.speech_final === "boolean", "DeepgramResponse.speech_final missing");
  ok(dg.channel && Array.isArray(dg.channel.alternatives),
     "DeepgramResponse.channel.alternatives missing");
  const alt = (dg.channel?.alternatives || [])[0] || {};
  ok(typeof alt.transcript === "string" && alt.transcript.length > 0,
     "alternative.transcript empty");
  ok(typeof alt.confidence === "number", "alternative.confidence missing");
  ok(Array.isArray(alt.words) && alt.words.length > 0, "alternative.words empty");
  if (alt.words?.length) {
    const w = alt.words[0];
    ok(typeof w.word === "string" && typeof w.start === "number" &&
       typeof w.end === "number" && typeof w.confidence === "number",
       "word entries must have word/start/end/confidence");
  }
  // round-trip fidelity: most words of the phrase should come back
  const heard = (alt.transcript || "").toLowerCase();
  const hits = phrase.split(" ").filter((w) => heard.includes(w.toLowerCase())).length;
  ok(hits >= 4, `TTS→STT round-trip lost too much: heard "${alt.transcript}"`);
}

// ---- report -----------------------------------------------------------------
for (const n of notes) console.log("ℹ️ ", n);
if (fails.length) {
  console.log("❌ voice tests FAILED:");
  for (const f of fails) console.log("   -", f);
  process.exit(1);
}
console.log(`✅ voice tests OK — TTS ${ttsHealth ? "live" : "skipped"}, STT ${sttHealth ? "live" : "skipped"}`);
