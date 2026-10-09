/* probe_prefix_cache.mjs — does the backend's prompt cache survive the per-turn shape cue?
 *
 * SPENDS MONEY; NOT A TEST. One conversation per cue ORDER through the REAL
 * `functions/api/chat.js`, in this process, against the gateway in `.dev.vars`
 * (`--env-file=`), recording each completion's `usage`: `prompt_tokens`,
 * `prompt_tokens_details.cached_tokens` (the part of the prompt the backend reports it did
 * not have to read again) and `completion_tokens`, beside the cue the route chose for that
 * turn. Refuses to start without `--yes`; every gateway call is written to the ledger
 * (`sim/artifacts/prefix-probe-ledger.jsonl`) BEFORE it is sent; `--max-calls` is a hard
 * cap (default 24), and a call it refuses is never made.
 *
 * WHY: the `single` layout (`_lib/prompt.js`) puts the per-turn cue (`_lib/turnshape.js`)
 * inside the one system message, BEFORE the history. A prefix cache can reuse a prompt only
 * up to its first changed token, so a turn whose cue differs from the previous turn's pays
 * for everything after the cue again, the history included. Moving the cue later helps only
 * if the backend's cache really resumes at the first changed token; this measures whether it
 * does, before `prompt.js` is touched.
 *
 * THE ORDERS. Only the cue moves, and `prompt.js` is not edited to measure them: the REAL
 * body `buildUpstreamBody` built is rewritten in the fetch hook, the cue paragraph cut from
 * where it is and put back elsewhere.
 *   dev   today, the control: system = [persona, anchor, CUE, format].
 *   tail  order (a): system = [persona, anchor, format, CUE].
 *         Bar: cached >= 1,100 on every cue-change turn.
 *   user  order (b): system = [persona, anchor, format]; the CUE is the last paragraph of
 *         the final user message, after the history.
 *         Bar: cached >= prompt - 100 on every turn after the first.
 * The replies decide the cue (the rotation follows what she actually said), so the number of
 * cue changes is REPORTED, never assumed; a verdict needs at least two in that conversation.
 *
 * WHICH WAY IT LIES: `cached_tokens` is what the backend reports. Another visitor's request
 * between two turns can take or evict the cache a turn would have reused, so a LOW number on
 * one turn may be contention, not the order; a turn served by another deployment shows a
 * different `server` hash. Same-cue turns are the in-run control for contention: if they
 * keep their cache while every cue change loses it, the order is what decided.
 *
 * Fixed for the measurement: `DEMO_PROMPT_LAYOUT=single`, `DEMO_TURN_SHAPE=1`,
 * `DEMO_REROLL=0` (exactly one completion per turn), no documentation lookup (every
 * non-gateway fetch is answered 404 here) and lines that never ask for a diagram, so the
 * system message holds exactly the four blocks above (checked on every call). Nothing from
 * the env file is printed; the serving deployment is recorded only as a short hash of the
 * `x-litellm-model-id` header.
 *
 *   node sim/tools/probe_prefix_cache.mjs --dry-run           (no gateway: each order's layout)
 *   node sim/tools/probe_prefix_cache.mjs --yes [--orders=dev,tail,user] [--turns=7]
 *        [--max-calls=24] [--pace=500] [--env-file=.dev.vars] [--out=FILE]
 */
import { appendFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { loadavg } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..", "..");
const argv = process.argv.slice(2);
const flag = (n, d) => {
  const hit = argv.find((a) => a === "--" + n || a.startsWith("--" + n + "="));
  if (!hit) return d;
  return hit.includes("=") ? hit.slice(hit.indexOf("=") + 1) : true;
};
const DRY = !!flag("dry-run", false);
if (!DRY && !flag("yes", false)) {
  console.error("probe_prefix_cache.mjs SPENDS REAL GATEWAY CALLS (one completion per turn per order).\n" +
    "  node sim/tools/probe_prefix_cache.mjs --dry-run\n" +
    "  node sim/tools/probe_prefix_cache.mjs --yes [--orders=dev,tail,user] [--turns=7] [--max-calls=24] [--out=FILE]");
  process.exit(2);
}
const ORDERS = String(flag("orders", "dev,tail,user")).split(",").map((s) => s.trim()).filter(Boolean);
for (const o of ORDERS) if (!["dev", "tail", "user"].includes(o)) { console.error("unknown order: " + o); process.exit(2); }
const TURNS = Math.max(2, Math.min(7, Number(flag("turns", 7)) || 7));
const CAP = Math.max(0, Math.floor(Number(flag("max-calls", 24))) || 0);
const PACE = Math.max(0, Number(flag("pace", 500)) || 0);

/* The bars (W4-S3). */
const TAIL_MIN_CACHED = 1100;
const USER_MAX_UNCACHED = 100;

/** The conversation every order is asked: no line asks for a diagram (`prompt.js::wantsDiagram`)
 *  and none is a goodbye, so the cue is always one of the rotation's three. */
const LINES = [
  "hi moxie! what are you doing right now?",
  "I'm Sam and I'm 7. Do you like dinosaurs?",
  "tell me a silly joke",
  "why is the sky blue?",
  "what's your favorite food?",
  "can you tell me about volcanoes?",
  "what should we play together?",
];

const turnshape = await import(join(repo, "functions", "api", "_lib", "turnshape.js"));
const { readConfig } = await import(join(repo, "functions", "api", "_lib", "env.js"));

/* ---- configuration: the env file, then what the measurement fixes ---- */
const env = {};
if (DRY) {
  Object.assign(env, { DEMO_GATEWAY_BASE_URL: "https://gw.invalid.test/v1", DEMO_GATEWAY_API_KEY: "dry-run-not-a-key",
                       DEMO_CHAT_MODEL: "dry-run-model" });
} else {
  const envFile = resolve(repo, String(flag("env-file", ".dev.vars")));
  for (const raw of readFileSync(envFile, "utf8").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    const i = line.indexOf("=");
    env[line.slice(0, i).trim()] = line.slice(i + 1).trim().replace(/^["']|["']$/g, "");
  }
}
Object.assign(env, { DEMO_PROMPT_LAYOUT: "single", DEMO_TURN_SHAPE: "1", DEMO_REROLL: "0" });
// Generous local limits so the instrument measures the cache, not the limiter.
for (const [k, v] of Object.entries({ DEMO_CHAT_PER_MIN: "600", DEMO_CHAT_PER_HOUR: "5000", DEMO_CHAT_PER_DAY: "20000",
                                      DEMO_UNIT_BUDGET_HOUR: "100000", DEMO_UNIT_BUDGET_DAY: "100000" })) env[k] = v;
const cfg = readConfig(env);
const GW = String(cfg.baseUrl || "").replace(/\/+$/, "");
if (!GW) { console.error("probe_prefix_cache.mjs: no DEMO_GATEWAY_BASE_URL in the env file"); process.exit(2); }

/* ---- the order rewrite ---- */
/** The body `prompt.js` built, with the cue paragraph moved for `order`. Throws (and no call
 *  is made) unless the system message is exactly [persona, anchor, cue, format]-shaped: the
 *  persona first, the cue once, and nothing a turn adds conditionally (a passage, a diagram
 *  cue, a re-roll sentence) that would move with it. */
function reorder(order, body, cue) {
  const msgs = body.messages.map((m) => ({ ...m }));
  const sys = msgs[0] && msgs[0].role === "system" ? String(msgs[0].content) : "";
  const at = sys.indexOf("\n\n" + cue + "\n\n");
  if (!sys.startsWith(cfg.persona) || at < 0 || sys.indexOf("\n\n" + cue, at + 1) >= 0) {
    throw new Error("the system message is not [persona, anchor, cue, format]");
  }
  if (/You already said this|DRAW A DIAGRAM|You just looked this up/.test(sys)) {
    throw new Error("a conditional block is present; the orders would not differ by the cue alone");
  }
  if (order === "dev") return { body, sysCueAt: at + 2 };
  const without = sys.slice(0, at) + sys.slice(at + 2 + cue.length);
  if (order === "tail") {
    msgs[0].content = without + "\n\n" + cue;
    return { body: { ...body, messages: msgs }, sysCueAt: without.length + 2 };
  }
  msgs[0].content = without;
  const last = msgs[msgs.length - 1];
  msgs[msgs.length - 1] = { ...last, content: String(last.content) + "\n\n" + cue };
  return { body: { ...body, messages: msgs }, sysCueAt: -1 };
}

/* ---- the fetch hook: only POST <gateway>/chat/completions goes out, ledgered first ---- */
const outDir = join(repo, "sim", "artifacts");
mkdirSync(outDir, { recursive: true });
const LEDGER = join(outDir, "prefix-probe-ledger.jsonl");
let calls = 0;
let order = "dev";
let last = null;     // what the hook saw for the current turn
const realFetch = globalThis.fetch;
globalThis.fetch = async (input, init) => {
  const url = String((input && input.url) || input || "");
  if (!url.startsWith(GW) || !/\/chat\/completions$/.test(url)) return new Response("not here", { status: 404 });
  last = { error: null };
  let body;
  try {
    body = JSON.parse(init.body);
    const history = body.messages.slice(1, -1);
    const text = String(body.messages[body.messages.length - 1].content);
    const move = turnshape.moveFor(history, text);
    const cue = turnshape.shapeCue(move);
    const r = reorder(order, body, cue);
    body = r.body;
    const lastMsg = body.messages[body.messages.length - 1];
    Object.assign(last, { cue: move, msgs: body.messages.length, sysChars: body.messages[0].content.length,
                          sysCueAt: r.sysCueAt, userChars: String(lastMsg.content).length,
                          cueInUser: String(lastMsg.content).endsWith("\n\n" + cue) });
  } catch (e) {
    last.error = "layout: " + e.message;
    throw e;  // the route answers upstream_down; no call was made
  }
  if (calls >= CAP) { last.error = "cap"; throw new Error("max-calls reached"); }
  calls += 1;
  appendFileSync(LEDGER, JSON.stringify({ at: new Date().toISOString(), call: calls, order, route: "chat/completions", dry: DRY }) + "\n");
  let res;
  if (DRY) {
    // Replies chosen so the rotation moves: a fact, a question, a proposal, in turn.
    const say = ["I like counting rocks.", "What is your favorite rock?", "Let's count rocks together!"][(calls - 1) % 3];
    res = new Response(JSON.stringify({ choices: [{ message: { content: JSON.stringify({ say, mood: "happy", gesture: "talk" }) } }] }),
      { status: 200, headers: { "Content-Type": "application/json" } });
  } else {
    res = await realFetch(input, { ...init, body: JSON.stringify(body) });
  }
  try {
    const j = await res.clone().json();
    const u = (j && j.usage) || {};
    last.prompt = Number.isFinite(u.prompt_tokens) ? u.prompt_tokens : null;
    last.cached = u.prompt_tokens_details && Number.isFinite(u.prompt_tokens_details.cached_tokens) ? u.prompt_tokens_details.cached_tokens : null;
    last.completion = Number.isFinite(u.completion_tokens) ? u.completion_tokens : null;
    // Some backends say how long the prompt took to read; kept as reported when present.
    if (j && j.timings && typeof j.timings === "object") last.timings = j.timings;
    const c = j && j.choices && j.choices[0] && j.choices[0].message ? String(j.choices[0].message.content || "") : "";
    last.raw = c.slice(0, 160);  // the completion as the model wrote it, before the route parses it
  } catch { /* not JSON: the route reports it */ }
  const mid = res.headers.get("x-litellm-model-id");
  last.server = mid ? createHash("sha256").update(mid).digest("hex").slice(0, 10) : null;
  last.status = res.status;
  return res;
};

const chat = await import(join(repo, "functions", "api", "chat.js"));
const ORIGIN = "https://prefix-probe.invalid.test";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const load = () => loadavg().map((x) => Math.round(x * 100) / 100);

async function turn(text, context) {
  const payload = JSON.stringify(context ? { text, context } : { text });
  last = null;
  const t0 = Date.now();
  const res = await chat.onRequestPost({ env, request: new Request(ORIGIN + "/api/chat", {
    method: "POST", body: payload,
    headers: { "Content-Type": "application/json", Origin: ORIGIN, "Sec-Fetch-Site": "same-origin", "CF-Connecting-IP": "203.0.113.77" } }) });
  const body = await res.json().catch(() => ({}));
  let said = "";
  try { said = JSON.parse(body.messages[0].payload).output.text; } catch { /* a refusal */ }
  return { status: res.status, reason: body.reason || null, ms: Date.now() - t0, context: body.context || "", said, hook: last };
}

/* ---- the run ---- */
const result = { at: new Date().toISOString(), tool: "probe_prefix_cache", dry: DRY, chatModel: cfg.chatModel, layout: cfg.promptLayout,
                 turnsPerOrder: TURNS, cap: CAP, bars: { tailMinCached: TAIL_MIN_CACHED, userMaxUncached: USER_MAX_UNCACHED },
                 load: { before: load(), after: null }, orders: {}, verdicts: {} };
for (const o of ORDERS) {
  order = o;
  const rows = [];
  const conv = { loadBefore: load(), loadAfter: null, turns: rows };
  result.orders[o] = conv;
  let context = "";
  let prevCue = null;
  for (let i = 0; i < TURNS; i++) {
    if (calls >= CAP) { console.log(`${o}: max-calls (${CAP}) reached before turn ${i + 1}`); break; }
    let t = await turn(LINES[i], context);
    // One retry of a failed turn (a 5xx, a timeout): the conversation must not lose its place.
    if (t.status !== 200 && calls < CAP && !(t.hook && /layout/.test(String(t.hook.error)))) t = await turn(LINES[i], context);
    const h = t.hook || {};
    const row = {
      order: o, turn: i + 1, line: LINES[i], at: new Date().toISOString(), status: t.status, reason: t.reason, ms: t.ms,
      cue: h.cue || null, cueChanged: i > 0 && !!h.cue && h.cue !== prevCue,
      prompt: h.prompt ?? null, cached: h.cached ?? null,
      uncached: Number.isFinite(h.prompt) && Number.isFinite(h.cached) ? h.prompt - h.cached : null,
      completion: h.completion ?? null, server: h.server || null, timings: h.timings || null,
      msgs: h.msgs ?? null, sysChars: h.sysChars ?? null, sysCueAt: h.sysCueAt ?? null, userChars: h.userChars ?? null,
      cueInUser: !!h.cueInUser, raw: h.raw ?? null, said: t.said, saidShape: t.said ? turnshape.shapeOf(t.said) : null,
      error: h.error || null,
    };
    rows.push(row);
    console.log(`${o} t${row.turn}: ${row.status}${row.reason ? " " + row.reason : ""} cue=${row.cue}${row.cueChanged ? " (changed)" : ""} ` +
                `prompt=${row.prompt} cached=${row.cached} uncached=${row.uncached} completion=${row.completion} ${row.ms} ms server=${row.server}`);
    if (t.status !== 200) break;
    context = t.context || context;
    prevCue = row.cue;
    await sleep(PACE);
  }
  conv.loadAfter = load();
}
result.load.after = load();
result.calls = calls;

/* ---- the verdicts ---- */
for (const [o, conv] of Object.entries(result.orders)) {
  const rows = conv.turns.filter((r) => r.status === 200);
  const later = rows.filter((r) => r.turn > 1);
  const changes = later.filter((r) => r.cueChanged);
  const same = later.filter((r) => !r.cueChanged);
  const v = { turns: rows.length, cueChanges: changes.length,
              cueChangeCached: changes.map((r) => r.cached), cueChangeUncached: changes.map((r) => r.uncached),
              sameCueUncached: same.map((r) => r.uncached), decidable: changes.length >= 2 };
  // `missed`: the turns under the bar. One is a failure even when the order is not decidable.
  if (o === "tail") v.missed = changes.filter((r) => !(Number.isFinite(r.cached) && r.cached >= TAIL_MIN_CACHED)).map((r) => r.turn);
  if (o === "user") v.missed = later.filter((r) => !(Number.isFinite(r.uncached) && r.uncached <= USER_MAX_UNCACHED)).map((r) => r.turn);
  if (v.missed) v.pass = v.decidable && v.missed.length === 0;
  result.verdicts[o] = v;
}
console.log("\n" + JSON.stringify({ calls, cap: CAP, load: result.load, verdicts: result.verdicts }, null, 1));
const OUT = String(flag("out", join(outDir, `prefix-probe-${DRY ? "dry-" : ""}${result.at.replace(/[:.]/g, "-")}.json`)));
writeFileSync(OUT, JSON.stringify(result, null, 1));
console.log("saved " + OUT);
