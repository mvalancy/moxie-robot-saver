/* probe_demo_gateway.mjs — post the bodies the Pages Functions BUILD to a real gateway.
 *
 * WHY THIS EXISTS, and why it is not a test. Cloudflare Pages Functions do not run under a
 * plain static server, so `sim/serve.py` cannot exercise `/api/chat` and `/api/speech` end
 * to end, and `npx wrangler pages dev` is not part of this repo's toolchain. That leaves
 * one claim genuinely unproven by the hermetic suite: **that the request bodies these
 * Functions construct are accepted by the real API.** Every other property — the caps, the
 * tickets, the WAV decode, the no-leak sweep, the voice-first ordering — is proven with a
 * stubbed `fetch` and no credential, and must stay that way.
 *
 * So this file imports the REAL body builders (`chat.js::buildUpstreamBody`,
 * `speech.js::buildSpeechBody`) and the REAL header builder
 * (`_lib/env.js::upstreamHeaders`), POSTs what they produce, and reports the SHAPES that
 * came back. It is deliberately a hand-run tool under `sim/tools/`, NOT a test:
 *
 *   * it spends real gateway calls, and the budget for this slice was four;
 *   * it needs a credential, and no test in this repo may ever need one;
 *   * a green CI run must never depend on a third party being up.
 *
 * IT NEVER PRINTS A CREDENTIAL. The key, the base URL and the two optional Cloudflare
 * Access halves are read from the git-ignored `mqtt/.env` and used only in the outbound
 * request. What is printed is what came back: status, content type, byte counts, the JSON
 * key set, the RIFF header fields — and the completion text, which is Moxie's own line.
 *
 *   node sim/tools/probe_demo_gateway.mjs            # 2 chat + 2 speech, ~4 calls
 *   node sim/tools/probe_demo_gateway.mjs --dry-run  # print the bodies, call nothing
 *
 * THE EARS (`--only=stt`). §10 assumption 15 — *does the gateway's
 * `/v1/audio/transcriptions` accept webm/Opus, which is what a browser's `MediaRecorder`
 * actually produces?* — cannot be settled by any hermetic test, because it is a fact about
 * a third party's decoder. `--stt-file=<path>` posts a real local file through the REAL
 * `transcribe.js::buildTranscribeForm`, and reports what came back. Pass a WAV as the
 * control and a webm/Opus clip as the question:
 *
 *   S=sim/web/audio/moxie/03e31950df81e786.mp3    # "Hi! I am Moxie. It is nice to meet you."
 *   ffmpeg -i $S -ac 1 -ar 16000 -c:a pcm_s16le /tmp/control.wav
 *   ffmpeg -i $S -ac 1 -ar 48000 -c:a libopus -b:a 32k -f webm /tmp/opus.webm
 *   node sim/tools/probe_demo_gateway.mjs --only=stt --stt-file=/tmp/control.wav \
 *        --stt-file=/tmp/opus.webm
 *
 * The clip is a repo asset with a KNOWN utterance, so the transcript can be checked
 * without a microphone and without spending a TTS call to make one. No audio is committed.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..", "..");

const { readConfig, upstreamHeaders } = await import(join(repo, "functions", "api", "_lib", "env.js"));
const { buildUpstreamBody } = await import(join(repo, "functions", "api", "chat.js"));
const { buildSpeechBody } = await import(join(repo, "functions", "api", "speech.js"));
const { buildTranscribeForm, audioKind, cleanTranscript, reasonForUpstreamStatus } =
  await import(join(repo, "functions", "api", "transcribe.js"));
const { joinUrl } = await import(join(repo, "functions", "api", "_lib", "wire.js"));
const { pcmFromAudio } = await import(join(repo, "functions", "api", "_lib", "wav.js"));

const DRY = process.argv.includes("--dry-run");
/** `--only=chat|speech1|speech2` limits the run. The gateway-call budget for a slice is
 *  small and deliberate, so re-verifying ONE fixed body must not cost four calls. */
const ONLY = (process.argv.find((a) => a.startsWith("--only=")) || "").slice(7);
const want = (name) => !ONLY || ONLY === name;
/** Repeatable. Each one is ONE gateway call, so the slice's budget is the caller's to spend. */
const STT_FILES = process.argv.filter((a) => a.startsWith("--stt-file=")).map((a) => a.slice(11));

/* --------------------------------------------------------------------------- *
 * Read the local credentials. Nothing from here is ever printed.
 * --------------------------------------------------------------------------- */
function readDotEnv(path) {
  const out = {};
  let text;
  try {
    text = readFileSync(path, "utf8");
  } catch {
    return out;
  }
  for (const line of text.split("\n")) {
    const m = /^\s*(?:export\s+)?([A-Z0-9_]+)\s*=\s*(.*)$/.exec(line);
    if (!m) continue;
    out[m[1]] = m[2].trim().replace(/^["']|["']$/g, "");
  }
  return out;
}

/* `mqtt/.env` is git-ignored, so it lives in the main checkout and NOT in a worktree.
 * `MOXIE_ENV_FILE` overrides the path for exactly that case. */
const envPath = process.env.MOXIE_ENV_FILE || join(repo, "mqtt", ".env");
const local = readDotEnv(envPath);
console.log("reading local config from:", envPath.replace(process.env.HOME || "~", "~"),
            Object.keys(local).length ? `(${Object.keys(local).length} variables)` : "(not found or empty)");

/** The DEMO_* surface, filled from the local stack's own variables. */
const env = {
  DEMO_GATEWAY_BASE_URL: local.MOXIE_VOICE_BASE_URL || local.MOXIE_LLM_BASE_URL || "",
  DEMO_GATEWAY_API_KEY: local.MOXIE_LLM_API_KEY || "",
  DEMO_CHAT_MODEL: local.MOXIE_LLM_MODEL || "graphling-medium",
  DEMO_TTS_MODEL: local.MOXIE_VOICE_MODEL || "piper-amy",
  DEMO_TTS_FORMAT: "wav",
  // `MOXIE_STT_BASE_URL` defaults to the voice base and then to the LLM base — one
  // gateway, one key (docs/guides/litellm-stt-setup.md). `stt-whisper` is the gateway's
  // own default model, recorded there as live since 2026-09-02.
  DEMO_STT_MODEL: local.MOXIE_STT_MODEL || "stt-whisper",
};
if (local.MOXIE_STT_BASE_URL) env.DEMO_GATEWAY_BASE_URL = local.MOXIE_STT_BASE_URL;
// A Cloudflare Access service token, if the local stack has one configured for the tunnel.
if (local.MOXIE_GATEWAY_ACCESS_CLIENT_ID) env.DEMO_GATEWAY_ACCESS_CLIENT_ID = local.MOXIE_GATEWAY_ACCESS_CLIENT_ID;
if (local.MOXIE_GATEWAY_ACCESS_CLIENT_SECRET) env.DEMO_GATEWAY_ACCESS_CLIENT_SECRET = local.MOXIE_GATEWAY_ACCESS_CLIENT_SECRET;

const cfg = readConfig(env);

console.log("configured:", cfg.configured, "| voice:", cfg.voice, "| ears:", cfg.ears,
            "| access token:", cfg.accessToken);
console.log("missing:", cfg.missing.length ? cfg.missing.join(", ") : "(nothing)");
if (cfg.notes.length) for (const n of cfg.notes) console.log("note:", n);
if (!cfg.configured) {
  console.log("\nNot configured — nothing was called. Put MOXIE_LLM_API_KEY and " +
              "MOXIE_VOICE_BASE_URL in the git-ignored mqtt/.env.");
  process.exit(0);
}

/* --------------------------------------------------------------------------- *
 * The four calls
 * --------------------------------------------------------------------------- */
const HISTORY = [
  { role: "user", content: "hi moxie" },
  { role: "assistant", content: "Hi there! It's so good to see you." },
  { role: "user", content: "i got a puppy" },
  { role: "assistant", content: "A puppy! What did you name them?" },
];

/** Redact anything that could be a credential before printing a header set. */
const safeHeaders = (h) => Object.keys(h).sort();

async function chatCall(label, turns, text) {
  const body = buildUpstreamBody(cfg, turns, text);
  console.log(`\n── ${label} ──`);
  console.log("  body keys:", Object.keys(body).sort().join(", "));
  console.log("  model:", body.model, "| max_tokens:", body.max_tokens, "| temperature:", body.temperature,
              "| n:", body.n, "| stream:", body.stream);
  console.log("  messages:", body.messages.map((m) => m.role).join(" → "), `(${body.messages.length})`);
  console.log("  header names:", safeHeaders(upstreamHeaders(cfg, "application/json")).join(", "));
  if (DRY) return;

  const t0 = Date.now();
  const res = await fetch(joinUrl(cfg.baseUrl, "chat/completions"), {
    method: "POST",
    headers: Object.assign(upstreamHeaders(cfg, "application/json"), { Accept: "application/json" }),
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(cfg.chatTimeoutMs),
  });
  const ms = Date.now() - t0;
  console.log("  → status:", res.status, "| content-type:", res.headers.get("Content-Type"), `| ${ms} ms`);
  const raw = await res.text();
  if (!res.ok) {
    console.log("  → NOT OK. body length:", raw.length, "(not printed: an error body can name a key)");
    return;
  }
  let json = null;
  try { json = JSON.parse(raw); } catch {}
  if (!json) {
    console.log("  → the body did not parse as JSON. length:", raw.length,
                "| starts with:", JSON.stringify(raw.slice(0, 24)));
    console.log("  → THIS IS THE `gateway_unreachable_or_gated` SHAPE (an Access login page looks like this).");
    return;
  }
  console.log("  → top-level keys:", Object.keys(json).sort().join(", "));
  const choice = (json.choices || [])[0] || {};
  console.log("  → choices[0] keys:", Object.keys(choice).sort().join(", "),
              "| finish_reason:", choice.finish_reason);
  const content = ((choice.message || {}).content || "").trim();
  console.log("  → completion:", JSON.stringify(content));
  console.log("  → usage:", json.usage ? JSON.stringify(json.usage) : "(absent)");
  console.log("  → within max_tokens:", json.usage ? json.usage.completion_tokens <= body.max_tokens : "(unknown)");
}

async function speechCall(label, text) {
  const body = buildSpeechBody(cfg, text);
  console.log(`\n── ${label} ──`);
  console.log("  body keys:", Object.keys(body).sort().join(", "));
  console.log("  model:", body.model, "| response_format:", body.response_format,
              "| input chars:", body.input.length);
  console.log("  header names:", safeHeaders(upstreamHeaders(cfg, "application/json")).join(", "));
  if (DRY) return;

  const t0 = Date.now();
  const res = await fetch(joinUrl(cfg.baseUrl, "audio/speech"), {
    method: "POST",
    headers: upstreamHeaders(cfg, "application/json"),
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(cfg.speechTimeoutMs),
  });
  const ms = Date.now() - t0;
  const bytes = new Uint8Array(await res.arrayBuffer());
  console.log("  → status:", res.status, "| content-type:", res.headers.get("Content-Type"),
              `| ${bytes.length} B | ${ms} ms`);
  if (!res.ok) {
    console.log("  → NOT OK (body not printed)");
    return;
  }
  // THE LIE §2.2 RECORDS: a valid RIFF/WAVE body labelled `audio/mpeg`.
  const magic = String.fromCharCode(...bytes.subarray(0, 4)) + "/" + String.fromCharCode(...bytes.subarray(8, 12));
  console.log("  → first bytes:", JSON.stringify(magic),
              "| content-type says:", res.headers.get("Content-Type"),
              magic === "RIFF/WAVE" && !/wav/i.test(res.headers.get("Content-Type") || "")
                ? "  ⇐ THE CONTENT-TYPE IS A LIE, exactly as tts.py:110-125 records" : "");
  try {
    // The format is passed for the same reason the route passes it: without it the
    // parser cannot tell headerless PCM from a proxy error body (`_lib/wav.js`).
    const out = pcmFromAudio(bytes, { sampleRate: cfg.ttsSampleRate, channels: 1, format: cfg.ttsFormat });
    console.log("  → pcmFromAudio:", out.container, "|", out.sampleRate, "Hz |", out.channels, "ch |",
                out.pcm.length, "B PCM |", (out.pcm.length / 2 / out.channels / out.sampleRate).toFixed(2), "s");
    console.log("  → base64 buffer would be:", Math.ceil(out.pcm.length / 3) * 4, "B");
  } catch (e) {
    console.log("  → pcmFromAudio REFUSED it:", e.kind, "—", e.message);
  }
}

/**
 * ONE `/audio/transcriptions` call, built by the REAL route helpers.
 *
 * This is the probe that settles §10 assumption 15. What it reports is deliberately more
 * than "did it work": the container the sniffer identified, the filename and mime the
 * route would send, the status, and — on a refusal — the reason `transcribe.js` would map
 * that status to, because THAT is the number that decides whether the whole page degrades
 * or just this one turn.
 *
 * The response body is printed ONLY on success (it is the transcript, which is our own
 * clip read back). An error body is never printed: an OpenAI-compatible one names the
 * model and sometimes a key prefix.
 */
async function sttCall(label, path) {
  console.log(`\n── ${label} ──`);
  let bytes;
  try {
    bytes = new Uint8Array(readFileSync(path));
  } catch (e) {
    console.log("  ! cannot read", path, "—", e.code || e.message);
    return;
  }
  const kind = audioKind(bytes, null);
  console.log("  file:", path.replace(process.env.HOME || "~", "~"), "|", bytes.length, "B");
  if (!kind) {
    console.log("  → the route's own sniffer does not recognise this container: it would answer " +
                "`bad_request` and MAKE NO CALL. Nothing was sent.");
    return;
  }
  console.log("  sniffed:", kind.ext, "|", kind.mime, "| from the magic number:", kind.sniffed);
  const form = buildTranscribeForm(cfg, bytes, kind);
  console.log("  form fields:", [...form.keys()].sort().join(", "),
              "| model:", form.get("model"), "| filename:", form.get("file").name);
  const headers = upstreamHeaders(cfg, "multipart/form-data");
  delete headers["Content-Type"];        // fetch owns the multipart boundary
  console.log("  header names:", safeHeaders(headers).join(", "));
  if (DRY) return;

  const t0 = Date.now();
  let res;
  try {
    res = await fetch(joinUrl(cfg.baseUrl, "audio/transcriptions"), {
      method: "POST", headers, body: form, signal: AbortSignal.timeout(cfg.sttTimeoutMs),
    });
  } catch (e) {
    console.log("  → threw:", e.name, `| ${Date.now() - t0} ms`,
                "| the route would answer:", e.name === "TimeoutError" ? "timeout" : "upstream_down");
    return;
  }
  const ms = Date.now() - t0;
  const raw = await res.text();
  console.log("  → status:", res.status, "| content-type:", res.headers.get("Content-Type"),
              `| ${raw.length} B | ${ms} ms`);
  if (!res.ok) {
    console.log("  → NOT OK. The route would answer reason:", reasonForUpstreamStatus(res.status),
                res.status >= 400 && res.status < 500 && ![401, 403, 407, 413].includes(res.status)
                  ? "  ⇐ per-turn: the page stays LIVE and this one turn falls back to a scripted line"
                  : "  ⇐ the page degrades");
    console.log("  → body length:", raw.length, "(not printed: an error body can name a model or a key)");
    return;
  }
  let json = null;
  try { json = JSON.parse(raw); } catch {}
  if (!json) {
    console.log("  → the body did not parse as JSON | starts with:", JSON.stringify(raw.slice(0, 24)));
    console.log("  → THIS IS THE `gateway_unreachable_or_gated` SHAPE (an Access login page looks like this).");
    return;
  }
  console.log("  → top-level keys:", Object.keys(json).sort().join(", "));
  console.log("  → text is a string:", typeof json.text === "string",
              "| the route would answer:", typeof json.text === "string" ? "ok" : "upstream_down");
  if (typeof json.text === "string") {
    console.log("  → transcript:", JSON.stringify(cleanTranscript(json.text, cfg.maxInputChars).text));
  }
}

console.log(DRY ? "\n[dry run — building bodies, calling nothing]"
                : ONLY ? `\n[real gateway calls, limited to --only=${ONLY}]`
                       : "\n[4 real gateway calls]");

if (want("chat")) {
  await chatCall("chat 1 of 2 · a first turn (persona first AND last, one user turn)", [], "hi moxie, tell me a joke");
  await chatCall("chat 2 of 2 · a fourth turn (4 history turns from a signed context blob)",
                 HISTORY, "what should i teach them first?");
}
if (want("stt")) {
  if (!cfg.ears) {
    console.log("\n── ears ── DEMO_STT_MODEL is unset, so the route would answer " +
                "`gateway_not_configured` and call nothing. Nothing was sent.");
  } else if (!STT_FILES.length) {
    console.log("\n── ears ── no --stt-file=<path> given; see this file's header for the " +
                "two ffmpeg commands that make a control WAV and a webm/Opus clip.");
  } else {
    for (let i = 0; i < STT_FILES.length; i++) {
      await sttCall(`stt ${i + 1} of ${STT_FILES.length}`, STT_FILES[i]);
    }
  }
}
if (want("speech1")) await speechCall("speech 1 of 2 · a short line", "Hi there! Want to hear a joke?");
if (want("speech2")) {
  await speechCall("speech 2 of 2 · DEMO_MAX_TTS_CHARS worst case (300 chars)",
                   ("I love hearing about your day, and I want to know everything about it. " +
                    "Tell me what happened at school, and what you played, and who you sat with at lunch, " +
                    "and whether anything made you laugh out loud today, because that is my favourite part."
                   ).slice(0, 300));
}

console.log("\ndone.");
