/* check_live_turn.mjs — ONE real turn with the DEPLOYED brain: the daily canary. SPENDS ONE
 * CHAT TURN (one gateway completion, 3 of the deployment's 4,000 daily units).
 *
 *   node sim/check_live_turn.mjs                    # the site's own canonical origin — SPENDS
 *   node sim/check_live_turn.mjs https://host/sim   # any deployment — SPENDS
 *   MOXIE_DEPLOYED_URL=https://host/sim node sim/check_live_turn.mjs
 *   node sim/check_live_turn.mjs --selftest         # hermetic: loopback only, spends nothing
 *
 *   MOXIE_CANARY_MAX_MS=10000   the round-trip ceiling (clause 6)
 *
 * Why it exists: every free instrument passes with the brain dead. `/api/health` reads
 * configuration only, so it answers `live` while every turn fails `upstream_down`, and
 * `check_deployed.mjs` aborts every spending route by design — measured 2026-10-07 with the
 * real checker on five fixtures (healthy, dead brain, lost secrets, kill switch, no
 * Functions): all passed 24/24, and `GET /api/health` was the only request any of them saw.
 * A real turn is the one instrument that sees the brain, so `deployed.yml` spends one a day
 * on it. Not a `test_*.mjs`: it spends and needs a deployment; the fast tier runs
 * `--selftest`.
 *
 * Clauses, over one `POST /api/chat {"text":"hi moxie"}` sent the way the page sends it — a
 * browser user agent (Cloudflare refuses node's own at the edge) and the deployment's own
 * Origin (the route's origin pin): 1 HTTP 200; 2 the body is the JSON envelope (an origin
 * with no Functions answers its HTML); 3 `reason` is null — a live answer, not a refusal;
 * 4 she said something; 5 a voice ticket for THAT line came back, and is never redeemed (no
 * TTS is spent); 6 the round trip took under 10 s.
 *
 * THE LEDGER: at most two POSTs, the second only after a `rate_limited` answer asking for at
 * most 90 s — a refusal the route makes before any upstream call — so at most ONE call ever
 * reaches the gateway. The log carries at most 40 characters of her reply and never the
 * ticket or the context blob (both carry the whole line).
 */
import { spawn } from "node:child_process";
import http from "node:http";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { makeChecks, finish, deployedTarget, IOS_UA, repo, web } from "./browser_harness.mjs";

const LABEL = "live-turn canary";
const argv = process.argv.slice(2);
const SELFTEST = argv.includes("--selftest");
const cliUrl = argv.find((a) => !a.startsWith("-"));

/** What a child says first. */
const LINE = "hi moxie";
/** Clause 6. Production turns measured 1.8-3.5 s (2026-10-07); the route gives up at 20 s. */
const MAX_MS = Math.max(1, Number(process.env.MOXIE_CANARY_MAX_MS) || 10000);
/** THE LEDGER: two POSTs at most, the second only after a free `rate_limited` refusal. */
const MAX_POSTS = 2;
/** The longest `retry_after_s` worth waiting for: the per-minute window. An hourly or daily
 *  window is a red now, not a job asleep for an hour. */
const MAX_WAIT_S = 90;
/** A stuck connection is a result, not a hung job. */
const FETCH_TIMEOUT_MS = 30000;
/** How much of her reply a log may carry. */
const SHOW = 40;

/** One `POST /api/chat`, sent the way the page sends it. Never throws: a network failure is a
 *  RESULT (status 0) the clauses report, not a skip. */
async function postTurn(origin) {
  const t0 = performance.now();
  let res = null, raw = "", error = "";
  try {
    res = await fetch(origin + "/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json",
                 "User-Agent": IOS_UA, Origin: origin, "Sec-Fetch-Site": "same-origin" },
      body: JSON.stringify({ text: LINE }),
      redirect: "manual",
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    });
    raw = await res.text();
  } catch (e) {
    error = `${(e && e.name) || "Error"}: ${(e && e.message) || e}`;
  }
  let body = null;
  try { body = JSON.parse(raw); } catch { body = null; }
  return {
    status: res ? res.status : 0, ms: Math.round(performance.now() - t0), error,
    body: envelopeOf(body),
    ctype: (res && res.headers.get("content-type")) || "",
    // Cloudflare's mark on an answer it gave INSTEAD of the deployment (a challenge, a block).
    mitigated: (res && res.headers.get("cf-mitigated")) || "",
    retryAfter: (res && Number(res.headers.get("retry-after"))) || 0,
  };
}

/** The body, when it is the route's envelope (every outcome has `reason` and `messages`). */
function envelopeOf(b) {
  return b && typeof b === "object" && !Array.isArray(b) && "reason" in b && Array.isArray(b.messages)
    ? b : null;
}

/** Her line and its event id, read the way `bridge.js` reads a `remote_chat` payload. */
function replyOf(body) {
  const msg = body && Array.isArray(body.messages) ? body.messages[0] : null;
  let p = null;
  try { p = msg ? JSON.parse(msg.payload) : null; } catch { p = null; }
  return { text: String((p && p.output && p.output.text) || "").trim(),
           eventId: String((p && p.event_id) || "") };
}

/** The first voice ticket and the event it is for. */
function ticketOf(body) {
  const s = body && Array.isArray(body.speech) ? body.speech[0] : null;
  return { ticket: s && typeof s.ticket === "string" ? s.ticket : "",
           eventId: String((s && s.event_id) || "") };
}

/** The turn, under THE LEDGER. */
async function ask(origin) {
  const posts = [await postTurn(origin)];
  const first = posts[0].body;
  if (first && first.reason === "rate_limited") {
    const waitS = Number(first.retry_after_s) || posts[0].retryAfter || 20;
    if (waitS <= MAX_WAIT_S && posts.length < MAX_POSTS) {
      console.log(`  rate_limited — a refusal that spent nothing upstream; waiting ${waitS} s ` +
                  `(retry_after_s) to ask once more`);
      await new Promise((r) => setTimeout(r, waitS * 1000 + 500));
      posts.push(await postTurn(origin));
    } else {
      console.log(`  rate_limited for ${waitS} s — longer than the ${MAX_WAIT_S} s this canary ` +
                  `waits, so it does not ask again`);
    }
  }
  return posts;
}

/** One line per measurement, so a run leaves numbers behind rather than a verdict. */
function report(origin, posts) {
  console.log(`\n  POST ${origin}/api/chat  ${JSON.stringify({ text: LINE })}`);
  posts.forEach((r, i) => {
    const b = r.body;
    console.log(`    #${i + 1}   HTTP ${r.status}   ${r.ms} ms   ` +
                (b ? `reason ${JSON.stringify(b.reason)}   mode ${JSON.stringify(b.mode)}`
                   : `not the envelope (${r.ctype || "no Content-Type"})`) +
                (r.error ? `   ${r.error}` : "") + (r.mitigated ? `   cf-mitigated ${r.mitigated}` : ""));
  });
  const last = posts[posts.length - 1].body;
  const said = replyOf(last), voice = ticketOf(last);
  console.log(`    reply    ${said.text
    ? `${JSON.stringify(said.text.slice(0, SHOW))}${said.text.length > SHOW ? "…" : ""}  ` +
      `(${said.text.length} chars, at most ${SHOW} shown)`
    : "(none)"}`);
  console.log(`    ticket   ${voice.ticket ? `present, ${voice.ticket.length} chars — NOT redeemed` : "(none)"}`);
  console.log(`    LEDGER   ${posts.length} POST /api/chat of at most ${MAX_POSTS} ` +
              `(the second only after rate_limited), 0 /api/speech`);
}

/* ---- the assertions, over the last answer ------------------------------------- */
function assertTurn(c, posts, maxMs) {
  const { ok, eq } = c;
  const r = posts[posts.length - 1];
  const b = r.body;
  const said = replyOf(b), voice = ticketOf(b);
  eq(r.status, 200, `HTTP 200 from POST /api/chat` +
     (r.error ? ` (no answer: ${r.error})` : "") + (r.mitigated ? ` (cf-mitigated: ${r.mitigated})` : ""));
  ok(!!b, `the answer is the JSON envelope — got ${JSON.stringify(r.ctype || "no Content-Type")}; ` +
          `an origin with no Functions answers with its HTML page`);
  ok(!!b && b.reason === null, `reason is null — a LIVE answer, not a refusal — got ` +
     (b ? JSON.stringify(b.reason) + (b.retry_after_s ? ` (retry_after_s ${b.retry_after_s})` : "")
        : "no envelope"));
  ok(said.text.length > 0, `she said something — the reply is ${said.text.length} characters`);
  ok(!!voice.ticket && !!said.eventId && voice.eventId === said.eventId,
     `a voice ticket for that line came back (never redeemed) — ` +
     (voice.ticket ? `${voice.ticket.length} chars, for event ${JSON.stringify(voice.eventId)}, ` +
                     `the line is ${JSON.stringify(said.eventId)}` : "none"));
  ok(r.ms < maxMs, `the turn came back in under ${maxMs} ms — it took ${r.ms} ms`);
}

/* ═══════════════════════════ selftest: the teeth ══════════════════════════════════ *
 * The REAL chat route (functions/api/chat.js) behind a loopback server, its gateway stubbed
 * in THIS process, and this very file run as a CHILD against it: what is tested is the exit
 * code, the log and the requests deployed.yml's run would produce. The controls must exit 0;
 * each mutant must exit non-zero AND fire its own clause. Every case must make exactly the
 * POSTs THE LEDGER allows, redeem nothing, send a browser user agent and the deployment's own
 * Origin, and log no ticket, no context blob and no more than 40 characters of her reply.
 */
async function selftest() {
  const c = makeChecks();
  const api = (...p) => import(join(repo, "functions", "api", ...p));
  const chat = await api("chat.js");
  const limits = await api("_lib", "limits.js");
  const { respond } = await api("_lib", "envelope.js");
  const { buildChatResponse, chatMessage } = await api("_lib", "wire.js");

  // RFC 6761 `.invalid`, and a key no secret grep can mistake for a real one.
  const BASE = "https://gw.invalid.test/v1";
  const KEY = "testonly-canary-selftest-key-abcdefgh";
  const LIVE = { DEMO_GATEWAY_BASE_URL: BASE, DEMO_GATEWAY_API_KEY: KEY,
                 DEMO_CHAT_MODEL: "test-brain-model", DEMO_TTS_MODEL: "test-voice-model" };
  const MUTE = { ...LIVE };
  delete MUTE.DEMO_TTS_MODEL;                       // a brain with no voice configured
  // Longer than SHOW, so the log has something to cut.
  const REPLY = "Hi! I was just counting the bolts in my left arm again. Forty-two, as always.";
  const INDEX = readFileSync(join(web, "index.html"), "utf8");

  // The gateway, as THIS process's `fetch`: the route is its only caller here.
  const gw = { mode: "reply", delayMs: 0 };
  globalThis.fetch = async () => {
    if (gw.delayMs) await new Promise((r) => setTimeout(r, gw.delayMs));
    if (gw.mode === "throw") throw new TypeError("fetch failed");
    return new Response(JSON.stringify({ choices: [{ message: { content: REPLY } }] }),
                        { status: 200, headers: { "Content-Type": "application/json" } });
  };

  // What the "deployment" answers, one per POST (the last repeats).
  const route = (env, plan) => (request) => {
    Object.assign(gw, { mode: "reply", delayMs: 0 }, plan || {});
    return chat.onRequestPost({ request, env });
  };
  const html = () => new Response(INDEX, { status: 200,
                                           headers: { "Content-Type": "text/html; charset=utf-8" } });
  const rateLimited = (s) => () => respond({ reason: "rate_limited", retry_after_s: s, mode: "live" });
  const speechless = () => {
    const eid = "sim-0000000000aa";
    return respond({ reason: null, mode: "live",
                     messages: [chatMessage("d_sim", buildChatResponse({ eventId: eid, text: "" }))],
                     speech: [{ ticket: "v1.SELFTEST.MAC", event_id: eid, chunk_num: 0 }] });
  };

  const CASES = [
    // [name, the answers in POST order, the clause it MUST fire (null = must pass), POSTs, ceiling ms]
    ["control · a live turn", [route(LIVE)], null, 1],
    ["control · rate_limited once, then live (waits retry_after_s, asks once more)",
     [rateLimited(1), route(LIVE)], null, 2],
    ["A · dead brain: the gateway is unreachable while /api/health still says live",
     [route(LIVE, { mode: "throw" })], /reason is null/, 1],
    ["B · secrets lost", [route({})], /reason is null/, 1],
    ["C · the kill switch (DEMO_ENABLED=0)", [route({ ...LIVE, DEMO_ENABLED: "0" })], /reason is null/, 1],
    ["D · no Functions: the origin answers its HTML page, 200", [html], /JSON envelope/, 1],
    ["E · a 200 with nothing to say", [speechless], /said something/, 1],
    ["F · no voice configured: the line comes back without a ticket", [route(MUTE)], /voice ticket/, 1],
    ["G · a slow brain (900 ms against a 300 ms ceiling)",
     [route(LIVE, { delayMs: 900 })], /in under 300 ms/, 1, 300],
    ["H · rate_limited twice: never a third POST", [rateLimited(1)], /reason is null/, 2],
    ["I · rate_limited for an hour: no wait, no second POST", [rateLimited(3600)], /reason is null/, 1],
  ];

  let shown = 0;   // controls whose log showed the first words of a reply longer than SHOW
  for (const [name, answers, wanted, posts, ceilingMs] of CASES) {
    limits.__reset();
    const seen = [];     // every request the "deployment" got
    const bodies = [];   // every body it answered with, for the log sweep
    const server = http.createServer(async (req, res) => {
      const path = (req.url || "/").split("?")[0];
      const chunks = [];
      for await (const ch of req) chunks.push(ch);
      seen.push({ method: req.method, path, ua: req.headers["user-agent"] || "",
                  origin: req.headers.origin || "", at: performance.now() });
      if (req.method !== "POST" || path !== "/api/chat") { res.writeHead(404); res.end(); return; }
      const n = seen.filter((s) => s.method === "POST" && s.path === "/api/chat").length;
      const headers = { "CF-Connecting-IP": "192.0.2.10" };   // what Cloudflare adds
      for (const k of ["content-type", "accept", "origin", "sec-fetch-site", "user-agent"])
        if (req.headers[k]) headers[k] = req.headers[k];
      try {
        const out = await answers[Math.min(n, answers.length) - 1](
          new Request(`http://${req.headers.host}${req.url}`,
                      { method: "POST", headers, body: Buffer.concat(chunks) }));
        const text = await out.text();
        bodies.push(text);
        res.writeHead(out.status, Object.fromEntries(out.headers));
        res.end(text);
      } catch (e) {
        c.ok(false, `${name}: the fixture itself threw — ${(e && e.stack) || e}`);
        res.writeHead(500); res.end();
      }
    });
    await new Promise((r) => server.listen(0, "127.0.0.1", r));
    const origin = `http://127.0.0.1:${server.address().port}`;
    const run = await child(origin + "/sim", ceilingMs);
    server.close();

    const chats = seen.filter((s) => s.method === "POST" && s.path === "/api/chat");
    const other = seen.filter((s) => !(s.method === "POST" && s.path === "/api/chat"));
    console.log(`\n  ${name}\n    exit ${run.code}   POST /api/chat ×${chats.length}   other requests ×${other.length}`);
    for (const l of run.out.trimEnd().split("\n")) console.log(`    | ${l}`);

    c.eq(chats.length, posts, `${name}: exactly ${posts} POST /api/chat (THE LEDGER)`);
    c.eq(other.length, 0, `${name}: no other request — the voice ticket is never redeemed ` +
         JSON.stringify(other.map((o) => o.method + " " + o.path)));
    c.ok(chats.every((s) => /^Mozilla\/5\.0 \(/.test(s.ua)),
         `${name}: a browser user agent on every POST, never node's own — ` +
         JSON.stringify(chats.map((s) => s.ua.slice(0, 30))));
    c.ok(chats.every((s) => s.origin === origin),
         `${name}: the deployment's own Origin on every POST — ${JSON.stringify(chats.map((s) => s.origin))}`);
    if (posts === 2 && chats.length === 2) {
      const gap = Math.round(chats[1].at - chats[0].at);
      c.ok(gap >= 1000, `${name}: the second POST waited out retry_after_s (1 s) — it came ${gap} ms after the first`);
    }

    // The log sweep. A ticket and the context blob each carry her whole line.
    const leaks = [];
    for (const t of bodies) {
      let b = null;
      try { b = JSON.parse(t); } catch { b = null; }
      if (!b) continue;
      for (const s of b.speech || []) if (s.ticket && run.out.includes(s.ticket)) leaks.push("a voice ticket");
      if (b.context && run.out.includes(b.context)) leaks.push("the context blob");
      const said = replyOf(b).text;
      if (said.length > SHOW && run.out.includes(said.slice(0, SHOW + 1))) {
        leaks.push(`more than ${SHOW} characters of her reply`);
      }
      if (!wanted && said.length > SHOW && run.out.includes(said.slice(0, 20))) shown++;
    }
    for (const s of [KEY, BASE, "test-brain-model", "test-voice-model"]) {
      if (run.out.includes(s)) leaks.push(JSON.stringify(s.slice(0, 10)) + "…");
    }
    c.ok(!leaks.length, `${name}: the log carries no ticket, context blob, secret or whole reply — ` +
                        `found ${leaks.join(", ")}`);

    if (!wanted) {
      c.eq(run.code, 0, `${name}: the control must PASS (exit 0)`);
    } else {
      c.ok(run.code !== 0, `${name}: must make the canary FAIL — it exited 0`);
      c.ok(wanted.test(run.out), `${name}: must fire the ${wanted} clause specifically`);
    }
  }
  // Not vacuous: "at most 40 characters" holds trivially for a log that shows nothing.
  c.ok(shown === 2, `both controls' logs show the first words of a reply longer than ${SHOW} ` +
                    `characters (${shown} of 2)`);
  return c;
}

/** This file as a child process aimed at `url` — exactly what deployed.yml runs. */
function child(url, ceilingMs) {
  return new Promise((resolve) => {
    const env = { ...process.env, MOXIE_DEPLOYED_URL: "" };
    if (ceilingMs) env.MOXIE_CANARY_MAX_MS = String(ceilingMs);
    else delete env.MOXIE_CANARY_MAX_MS;
    const p = spawn(process.execPath, [fileURLToPath(import.meta.url), url],
                    { env, stdio: ["ignore", "pipe", "pipe"] });
    let out = "";
    p.stdout.on("data", (d) => { out += d; });
    p.stderr.on("data", (d) => { out += d; });
    const kill = setTimeout(() => p.kill("SIGKILL"), 60000);
    p.on("close", (code) => { clearTimeout(kill); resolve({ code, out }); });
  });
}

/* ═══════════════════════════════════ main ═════════════════════════════════════════ */
if (SELFTEST) finish(LABEL + " (selftest)", await selftest());

const origin = new URL(deployedTarget(cliUrl, LABEL)).origin;
console.log(`${LABEL}: ONE turn with the brain at ${origin} — this SPENDS one chat call ` +
            `(3 units); the voice ticket is never redeemed.`);
const posts = await ask(origin);
report(origin, posts);
const c = makeChecks();
assertTurn(c, posts, MAX_MS);
finish(LABEL, c);
