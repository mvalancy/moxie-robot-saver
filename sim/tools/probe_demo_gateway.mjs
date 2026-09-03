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
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..", "..");

const { readConfig, upstreamHeaders } = await import(join(repo, "functions", "api", "_lib", "env.js"));
const { buildUpstreamBody } = await import(join(repo, "functions", "api", "chat.js"));
const { buildSpeechBody } = await import(join(repo, "functions", "api", "speech.js"));
const { joinUrl } = await import(join(repo, "functions", "api", "_lib", "wire.js"));
const { pcmFromAudio } = await import(join(repo, "functions", "api", "_lib", "wav.js"));

const DRY = process.argv.includes("--dry-run");
/** `--only=chat|speech1|speech2` limits the run. The gateway-call budget for a slice is
 *  small and deliberate, so re-verifying ONE fixed body must not cost four calls. */
const ONLY = (process.argv.find((a) => a.startsWith("--only=")) || "").slice(7);
const want = (name) => !ONLY || ONLY === name;

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
};
// A Cloudflare Access service token, if the local stack has one configured for the tunnel.
if (local.MOXIE_GATEWAY_ACCESS_CLIENT_ID) env.DEMO_GATEWAY_ACCESS_CLIENT_ID = local.MOXIE_GATEWAY_ACCESS_CLIENT_ID;
if (local.MOXIE_GATEWAY_ACCESS_CLIENT_SECRET) env.DEMO_GATEWAY_ACCESS_CLIENT_SECRET = local.MOXIE_GATEWAY_ACCESS_CLIENT_SECRET;

const cfg = readConfig(env);

console.log("configured:", cfg.configured, "| voice:", cfg.voice, "| access token:", cfg.accessToken);
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
    const out = pcmFromAudio(bytes, { sampleRate: cfg.ttsSampleRate, channels: 1 });
    console.log("  → pcmFromAudio:", out.container, "|", out.sampleRate, "Hz |", out.channels, "ch |",
                out.pcm.length, "B PCM |", (out.pcm.length / 2 / out.channels / out.sampleRate).toFixed(2), "s");
    console.log("  → base64 buffer would be:", Math.ceil(out.pcm.length / 3) * 4, "B");
  } catch (e) {
    console.log("  → pcmFromAudio REFUSED it:", e.kind, "—", e.message);
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
if (want("speech1")) await speechCall("speech 1 of 2 · a short line", "Hi there! Want to hear a joke?");
if (want("speech2")) {
  await speechCall("speech 2 of 2 · DEMO_MAX_TTS_CHARS worst case (300 chars)",
                   ("I love hearing about your day, and I want to know everything about it. " +
                    "Tell me what happened at school, and what you played, and who you sat with at lunch, " +
                    "and whether anything made you laugh out loud today, because that is my favourite part."
                   ).slice(0, 300));
}

console.log("\ndone.");
