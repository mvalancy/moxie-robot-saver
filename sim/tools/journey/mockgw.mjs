/* journey/mockgw.mjs — a zero-spend OpenAI-compatible stand-in for the gateway, so a local
 * `wrangler pages dev` can be driven through every failure mode without a single real call.
 *   node sim/tools/journey/mockgw.mjs <port> <logfile>
 * Control:  GET /__ctl?chat=ok|500|hang|429|html|empty&speech=ok|500|hang&stt=ok|500|hang
 *                   &chatDelay=ms&speechDelay=ms&sttDelay=ms&stt_text=...
 *           GET /__log  -> the request log (JSON lines)
 */
import http from "node:http";
import { appendFileSync, writeFileSync } from "node:fs";

const port = Number(process.argv[2] || 19129);
const logfile = process.argv[3] || "/tmp/mockgw.jsonl";
writeFileSync(logfile, "");
const ctl = { chat: "ok", speech: "ok", stt: "ok", chatDelay: 300, speechDelay: 250, sttDelay: 300,
              stt_text: "What is your favorite animal?" };
const log = (o) => appendFileSync(logfile, JSON.stringify({ at: new Date().toISOString(), ...o }) + "\n");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* Two sentences each, as her real replies usually are (two voice tickets). */
const OTHER = [
  "Ooh, I love that! Tell me more about it. I am all ears, well, all microphones.",
  "Wow, that sounds amazing! What happened next?",
  "Hmm, that is a great thing to wonder about. What do you think?",
  "I like that a lot! Can you tell me one more thing about it?",
  "Oh, how fun! That makes my circuits feel all sparkly.",
  "That is so interesting. I never thought about it that way!",
];
const norm = (s) => String(s || "").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();

/** Her line for the child's `lastUser`. Never one she already said in this conversation: a
 *  reply that repeats an earlier line word for word is re-rolled by the chat route
 *  (`DEMO_REROLL`), a second call that would double this mock's delay — and a real model
 *  rarely repeats itself. */
function reply(lastUser, history) {
  const u = String(lastUser || "").toLowerCase();
  const said = new Set((history || []).filter((m) => m && m.role === "assistant").map((m) => norm(m.content)));
  let keyed = null;
  if (/\b(bye|goodbye|good night|see you|gotta go|have to go)\b/.test(u)) keyed = "Bye bye! That was so much fun. Come back soon, okay?";
  else if (/joke/.test(u)) keyed = "Why did the robot go to school? To get a little brighter! Beep.";
  else if (/remember|my name/.test(u)) keyed = "Hmm, let me check my memory chip. I think you told me your name is Sam!";
  else if (/happy/.test(u)) keyed = "You make me happy! Also sunshine on my sensors. Tell me what makes you happy.";
  for (const line of [keyed, ...OTHER]) if (line && !said.has(norm(line))) return line;
  return OTHER[0].replace("!", ` ${said.size + 1} times!`);
}

/** 16-bit mono WAV at 22050 Hz: a syllable-like tone, ~62 ms per character (cap 12 s). */
function wavFor(text) {
  const rate = 22050;
  const secs = Math.min(12, Math.max(0.6, String(text || "").length * 0.062));
  const n = Math.floor(secs * rate);
  const b = Buffer.alloc(44 + n * 2);
  b.write("RIFF", 0); b.writeUInt32LE(36 + n * 2, 4); b.write("WAVE", 8); b.write("fmt ", 12);
  b.writeUInt32LE(16, 16); b.writeUInt16LE(1, 20); b.writeUInt16LE(1, 22); b.writeUInt32LE(rate, 24);
  b.writeUInt32LE(rate * 2, 28); b.writeUInt16LE(2, 32); b.writeUInt16LE(16, 34); b.write("data", 36); b.writeUInt32LE(n * 2, 40);
  for (let i = 0; i < n; i++) {
    const env = 0.5 + 0.5 * Math.sin(2 * Math.PI * 4 * i / rate);          // 4 Hz "syllables"
    const v = Math.sin(2 * Math.PI * 220 * i / rate) * 0.5 * env;
    b.writeInt16LE(Math.round(v * 32767), 44 + i * 2);
  }
  return b;
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, "http://x");
  if (url.pathname === "/__ctl") {
    for (const [k, v] of url.searchParams) ctl[k] = /Delay$/.test(k) ? Number(v) : v;
    log({ ctl: { ...ctl } });
    res.writeHead(200, { "Content-Type": "application/json" }); res.end(JSON.stringify(ctl)); return;
  }
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const body = Buffer.concat(chunks);
  const path = url.pathname.replace(/^\/v1/, "");
  const entry = { path, bytes: body.length };
  const mode = path.startsWith("/chat") ? ctl.chat : path.startsWith("/audio/speech") ? ctl.speech :
               path.startsWith("/audio/transcriptions") ? ctl.stt : "ok";
  const delay = path.startsWith("/chat") ? ctl.chatDelay : path.startsWith("/audio/speech") ? ctl.speechDelay : ctl.sttDelay;
  entry.mode = mode;
  if (mode === "hang") { log({ ...entry, hung: true }); req.on("close", () => {}); return; }   // never answers
  await sleep(delay || 0);
  if (mode === "500") { log({ ...entry, status: 500 }); res.writeHead(500, { "Content-Type": "application/json" }); res.end('{"error":"mock 500"}'); return; }
  if (mode === "429") { log({ ...entry, status: 429 }); res.writeHead(429, { "Retry-After": "30", "Content-Type": "application/json" }); res.end('{"error":"mock 429"}'); return; }
  if (mode === "html") { log({ ...entry, status: 200, html: true }); res.writeHead(200, { "Content-Type": "text/html" }); res.end("<html>login</html>"); return; }
  if (path.startsWith("/chat/completions")) {
    let j = {}; try { j = JSON.parse(body.toString()); } catch (e) {}
    const msgs = Array.isArray(j.messages) ? j.messages : [];
    const last = [...msgs].reverse().find((m) => m.role === "user");
    const text = mode === "empty" ? "" : reply(last && last.content, msgs);
    entry.user = last && String(last.content).slice(-160); entry.reply = text; entry.n_messages = msgs.length;
    log({ ...entry, status: 200 });
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ id: "mock", object: "chat.completion", model: j.model,
      choices: [{ index: 0, message: { role: "assistant", content: text }, finish_reason: "stop" }] }));
    return;
  }
  if (path.startsWith("/audio/speech")) {
    let j = {}; try { j = JSON.parse(body.toString()); } catch (e) {}
    entry.input = String(j.input || "").slice(0, 160); entry.format = j.response_format;
    log({ ...entry, status: 200 });
    res.writeHead(200, { "Content-Type": "audio/wav" }); res.end(wavFor(j.input)); return;
  }
  if (path.startsWith("/audio/transcriptions")) {
    log({ ...entry, status: 200, text: ctl.stt_text });
    res.writeHead(200, { "Content-Type": "application/json" }); res.end(JSON.stringify({ text: ctl.stt_text })); return;
  }
  log({ ...entry, status: 404 });
  res.writeHead(404); res.end("nope");
});
server.listen(port, "127.0.0.1", () => console.log("mockgw on", port, "log", logfile));
