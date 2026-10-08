/* §13: a line the PAGE chose is never shown as the visitor's. When the ears fail, mic.js
 * consoles with a scripted child line, and production logged it as "YOU: Guess what, it's my
 * birthday today!" (measured 2026-10-08): words the visitor never said.
 *
 * End to end on the REAL mic.js, stub.js, bridge/ and cloud-transport.js, under a fake DOM
 * that records every transcript row with its class and its label, on a virtual clock: an STT
 * failure leaves no `.turn.user` row, one "Pretend line" row, the line played from its child
 * clip, Moxie's answer from stub.js, and not one request beyond the upload.
 */
import { BRIDGE_SRC } from "../../../bridge_harness.mjs";
import { eq, join, manifest, notes, ok, readFileSync, stubSrc, web, withGlobals } from "./harness.mjs";

const MIC_SRC = readFileSync(join(web, "mic.js"), "utf8");
const TRANSPORT_SRC = readFileSync(join(web, "cloud-transport.js"), "utf8");
const ORIGIN = "https://demo.invalid.test";
const GLOBALS = ["window", "document", "localStorage", "location", "fetch", "AbortSignal",
                 "setTimeout", "clearTimeout", "setInterval", "clearInterval", "mqtt"];

/** Boot the page's scripts in sim.html's order and run `fn(page)` against them. `live` is
 *  what the mode machine answers to `canSpendLiveTurn()`; `transcribe(n)` answers the n-th
 *  upload with `{status, json}` or `{reject: true}`. */
async function onPage({ live, transcribe }, fn) {
  return withGlobals(GLOBALS, async (g) => {
    // A virtual clock: the 450 ms stub beat, the 15 s cap and every gesture timer.
    let now = 0, seq = 0, timers = [];
    g.setTimeout = g.setInterval = (f, ms) => { timers.push({ id: ++seq, at: now + (Number(ms) || 0), f }); return seq; };
    g.clearTimeout = g.clearInterval = (id) => { timers = timers.filter((t) => t.id !== id); };
    const flush = () => new Promise((r) => setImmediate(r));
    const advance = async (ms) => {
      const end = now + ms;
      for (await flush(); ;) {
        timers.sort((a, b) => a.at - b.at || a.id - b.id);
        const t = timers.find((x) => x.at <= end);
        if (!t) break;
        timers = timers.filter((x) => x !== t);
        now = t.at;
        t.f();
        await flush();
      }
      now = end;
      await flush();
    };

    // Elements by id; a row keeps its class, its innerHTML (the label) and its `.msg` text.
    const els = {};
    const el = (id) => {
      const e = {
        id: id || "", value: "", textContent: "", innerHTML: "", className: "", hidden: false,
        scrollTop: 0, scrollHeight: 0, clientHeight: 0, children: [],
        addEventListener() {}, setAttribute() {}, removeAttribute() {}, getAttribute: () => null,
        appendChild(c) { e.children.push(c); },
        querySelector: (sel) => (sel === ".msg"
          ? { set textContent(v) { e.msg = v; }, get textContent() { return e.msg || ""; } } : null),
        querySelectorAll: (sel) => (sel === ".turn.moxie"
          ? e.children.filter((c) => /\bmoxie\b/.test(c.className)) : []),
      };
      if (id) els[id] = e;
      return e;
    };
    for (const id of ["transcript", "mic-status", "bus-status", "chat-status", "mic-btn"]) el(id);
    g.document = {
      readyState: "complete", hidden: false,
      getElementById: (id) => els[id] || null,
      createElement: () => el(""),
      addEventListener() {},
      body: { setAttribute() {}, removeAttribute() {}, appendChild() {} },
    };
    g.localStorage = { getItem: () => null, setItem() {} };
    g.location = { protocol: "https:", hostname: "demo.invalid.test", origin: ORIGIN };
    g.AbortSignal = { timeout: () => undefined };
    g.mqtt = { connect: () => { throw new Error("this page never goes on the bus"); } };

    const page = { requests: [], said: [], clips: [], sfx: [], live };
    let uploads = 0;
    g.fetch = (url, init) => {
      url = String(url);
      page.requests.push(url.replace(ORIGIN, ""));
      if (url === "audio/index.json") return Promise.resolve({ ok: true, json: () => Promise.resolve(manifest) });
      if (url === ORIGIN + "/api/transcribe") {
        const a = transcribe(++uploads);
        if (a.reject) return Promise.reject(new Error("network"));
        return Promise.resolve(new Response(JSON.stringify(a.json), { status: a.status || 200 }));
      }
      return Promise.reject(new Error("nothing else answers on this page: " + url));
    };
    g.window = {
      addEventListener() {}, removeEventListener() {}, dispatchEvent: () => true,
      moxie: { setFace() {}, setSpeech() {}, setMotor() {}, getMotor: () => 16384, showIcons() {},
               clearIcons() {}, setHeartLED() {} },
      // What the page asked the voice for: Moxie's lines, child clips, sounds.
      moxieAudio: {
        speak: (t) => { page.said.push(t); },
        speakClipOnly: (t, group) => { page.clips.push([t, group]); return Promise.resolve(true); },
        sfx: (n) => page.sfx.push(n),
        stop() {}, isSpeaking: () => false, isMoxieSpeaking: () => false, isMoxieBusy: () => false,
      },
      moxieMode: {
        apiBase: () => ORIGIN, ears: () => true, canSpendLiveTurn: () => live,
        state: () => (live ? "live" : "degraded"), message: () => "", note() {}, noteTransportError() {},
        limits: () => ({ max_record_ms: 15000, max_audio_bytes: 500000, min_audio_bytes: 2000 }),
      },
    };

    // sim.html's order: stub.js, bridge/, (mode.js: the fake above), cloud-transport.js, mic.js.
    for (const src of [stubSrc, BRIDGE_SRC, TRANSPORT_SRC, MIC_SRC]) (0, eval)(src);
    const mic = g.window.moxieMic;
    // A recorder in place of the microphone: one 40 KB clip whenever it is stopped.
    mic.setCapture(() => Promise.resolve({
      stream: { getTracks: () => [] },
      recorder: {
        state: "inactive", mimeType: "audio/wav", ondataavailable: null, onstop: null,
        start() { this.state = "recording"; },
        stop() {
          this.state = "inactive";
          this.ondataavailable({ data: new Blob([new Uint8Array(40000)], { type: "audio/wav" }) });
          this.onstop();
        },
      },
    }));

    page.speakOnce = async () => {           // tap Listen, speak, tap again; let it all land
      await mic.start();
      await flush();
      mic.stop();
      await advance(2000);
    };
    page.rows = () => els.transcript.children.map((r) => ({
      cls: r.className,
      who: (/<span class="who">([^<]*)<\/span>/.exec(r.innerHTML) || [])[1] || "",
      msg: r.msg || "",
    }));
    page.status = () => els["mic-status"].textContent;
    page.stubReply = (s) => g.window.moxieStub.reply(s).text;
    return fn(page);
  });
}

const CHILD_LINES = Object.keys(manifest.child || {});
const refused = (reason, status) => () => ({ status, json: { ok: false, degraded: true, reason, retry_after_s: 0 } });

for (const [label, opts] of [
  ["a live page whose ears are down (upstream_down)", { live: true, transcribe: refused("upstream_down", 503) }],
  ["a live page whose upload never arrives", { live: true, transcribe: () => ({ reject: true }) }],
  ["a degraded page (nothing spendable) whose ears are down", { live: false, transcribe: refused("upstream_down", 503) }],
]) {
  await onPage(opts, async (page) => {
    await page.speakOnce();
    const rows = page.rows();
    const line = (rows.find((r) => CHILD_LINES.includes(r.msg)) || {}).msg || "";
    ok(line.length > 0, `${label}: a scripted child line was logged (rows ${JSON.stringify(rows)})`);
    const theirs = rows.filter((r) => /\buser\b/.test(r.cls));
    eq(theirs.length, 0,
       `${label}: NO ".turn.user" ROW: the visitor said nothing that reached the page, so nothing is ` +
       `logged under "You" — got ${JSON.stringify(theirs)}`);
    const pretend = rows.filter((r) => r.msg === line);
    eq(pretend.length, 1, `${label}: …the scripted line is logged once`);
    eq(pretend[0] && pretend[0].who, "Pretend line", `${label}: …under the label "Pretend line"`);
    ok(pretend[0] && /\bturn\b/.test(pretend[0].cls) && /\bpretend\b/.test(pretend[0].cls),
       `${label}: …as a ".turn.pretend" row (a turn: the openers step aside) — got ${JSON.stringify(pretend[0])}`);
    ok(/pretend line/i.test(page.status()) && !/\bheard\b/i.test(page.status()),
       `${label}: …and the status calls it a pretend line, never something the page heard — got ${JSON.stringify(page.status())}`);
    eq(JSON.stringify(page.clips), JSON.stringify([[line, "child"]]),
       `${label}: …it is played from its child clip, the way a scripted child turn is`);
    eq(page.sfx.filter((n) => n === "listen").length, 2,
       `${label}: …with the listen sound a child's turn has (one for the tap, one for the line)`);

    const answer = rows.filter((r) => /\bmoxie\b/.test(r.cls));
    eq(answer.length, 1, `${label}: Moxie answers it once`);
    eq(answer[0] && answer[0].msg, page.stubReply(line), `${label}: …with stub.js's own answer to that line`);
    ok(answer[0] && page.said.includes(answer[0].msg), `${label}: …out loud, in her local voice (from the clips)`);
    const paid = page.requests.filter((u) => /\/api\/(chat|speech)\b/.test(u));
    eq(JSON.stringify(paid), "[]", `${label}: …and NOT ONE /api/chat or /api/speech request`);
  });
}

// CONTROL: the same rig shows a REAL transcript as the visitor's own words, so "no `.turn.user`
// row" above is a finding and not a rig that cannot draw one.
await onPage({ live: false, transcribe: () => ({ status: 200, json: { transcript: "hi moxie" } }) }, async (page) => {
  await page.speakOnce();
  const rows = page.rows();
  ok(rows.some((r) => /\buser\b/.test(r.cls) && r.who === "You" && r.msg === "hi moxie"),
     `control: a real transcript IS logged under "You" on this rig — got ${JSON.stringify(rows)}`);
  ok(!rows.some((r) => /\bpretend\b/.test(r.cls)), "control: …and nothing is called a pretend line");
  ok(rows.some((r) => /\bmoxie\b/.test(r.cls) && /^Hi there/.test(r.msg)), "control: …and she answers it");
});

notes.push("pretend line: an STT failure logs the scripted child line as a \"Pretend line\" row, never under " +
           "\"You\", on a live and a degraded page; plays its child clip; no /api/chat or /api/speech");
