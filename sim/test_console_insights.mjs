/* test_console_insights.mjs — the PARENT CONSOLE's 📈 Insights card (server/static), in a real
 * browser. `refreshInsights` in js/insights.js has six render paths; the invariant held over
 * ALL of them is: EITHER the 🧽 erase button is absent, OR it is present, starts unarmed, the
 * first click only ARMS it (nothing on the wire), the second issues exactly one DELETE, and the
 * card re-renders empty.
 *
 *   path 1 no permitted robot · 2 GET /telemetry 503 · 3 {ok:false} · 4 NO_DATA with count>0
 *   (button SHOWN) · 5 nothing recorded · 6 a normal history (button SHOWN)
 *
 * No FastAPI: the console's assets come from the harness's static server and every `/local/*`
 * XHR is answered at the browser with payloads built by the REAL server normalizers
 * (server/moxie_server/fleet, dependency-free) in a python3 subprocess, so a fixture cannot
 * drift from what the route returns. Claims are intercepted requests (method, path, the
 * millisecond it arrived) or DOM facts, never a counter the page keeps about itself.
 * TEETH: the wiring is moved OUT of `render` back to the terminal branch only — the "works in
 * three of them" shape the author's comment above refreshInsights feared — and path 4 must
 * redden while path 6 stays green, which is what makes the per-path sweep meaningful.
 *
 *   node sim/test_console_insights.mjs
 */
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { requireBrowser, serveStatic, makeChecks, finish, repo, watchPage, notable }
  from "./browser_harness.mjs";

const LABEL = "console-insights test";
const { puppeteer, chrome, skip } = await requireBrowser(LABEL);
const { fails, ok, eq, count } = makeChecks();

const DEV = "d_console_insights_01";
const STATIC = join(repo, "server", "static");
/* The insights card (and armErase) live in js/insights.js — the file the teeth mutate. */
const CARD_JS = "/js/insights.js";
const APPJS = readFileSync(join(STATIC, CARD_JS), "utf8");
const TELE = `/local/robots/${DEV}/telemetry`;
/* 1×1 transparent PNG — the console asks for four QR images the fixture has no server for. */
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
  "base64");

/* ---- fixtures, through the real server-side normalizers ------------------------------
 * Inputs here are SUPERVISOR payloads (what `moxie_server` fetches); outputs are exactly
 * what the console route returns, because the same function computes them. */
const PY = `
import json, sys
repo, dev = sys.argv[1], sys.argv[2]
sys.path.insert(0, repo + "/server")
from moxie_server import fleet as m

DAYS  = ["2026-08-29","2026-08-30","2026-08-31","2026-09-01","2026-09-02","2026-09-03","2026-09-04"]
COUNT = [0, 4, 2, 0, 9, 3, 5]
EVENTS = [
    {"event_name": "activity_finished", "recorded_at": 1757000000, "moxie_session_id": "s1"},
    {"event_name": "wakeword",          "recorded_at": 1756999000, "moxie_session_id": "s1"},
    {"event_name": "battery_low",       "recorded_at": 1756998000, "moxie_session_id": "s1"},
]
full = {
    "ok": True, "device_id": dev, "connected": True, "persisted": True, "policy": "NO_MEDIA",
    "summary": {"count": 3,
                "by_event": {"activity_finished": 9, "wakeword": 4, "battery_low": 1},
                "last_seen": {"activity_finished": 1757000000}},
    "events": EVENTS,
    "history": [{"day": d, "count": c, "top_event": "activity_finished" if c else None}
                for d, c in zip(DAYS, COUNT)],
    "totals": {"total": 41, "days_kept": 7, "first_day": "2026-08-29",
               "last_day": "2026-09-04", "dropped_days": 2},
    "retention": {"packets": 200, "days": 30},
}
# path 4: recording is OFF, but two packets arrived since the supervisor started. The card
# must still offer the erase — that is the whole point of the privacy contract.
nodata = {
    "ok": True, "device_id": dev, "connected": True, "persisted": False, "policy": "NO_DATA",
    "summary": {"count": 2, "by_event": {"wakeword": 2}},
    "events": EVENTS[:2], "history": [],
    "totals": {"total": 2}, "retention": {"packets": 200, "days": 30},
}
empty = {"ok": True, "device_id": dev, "connected": True, "persisted": True,
         "policy": "NO_MEDIA", "summary": {"count": 0, "by_event": {}}, "events": [],
         "history": [], "totals": {"total": 0}, "retention": {"packets": 200, "days": 30}}
empty_nodata = dict(empty, persisted=False, policy="NO_DATA")
conn = {"ok": True, "connected": True,
        "health": {"state": "recovered", "outages": 2, "refusals": 0,
                   "drops": 1, "lock_timeouts": 0},
        "summary": {"count": 2, "gaps": {"count": 2, "total_s": 93.5,
                                         "max_s": 61.0, "p95_s": 60.0}},
        "events": [{"kind": "disconnect", "at": 1756990000, "reason": "keepalive timeout"},
                   {"kind": "connect", "at": 1756990061, "gap_s": 61.0}],
        "retention": {"events": 200}, "roster": {"known": 1}}
snap = {"ok": True, "app": "moxie-supervisor", "uptime_s": 1234,
        "robots": [{"device_id": dev, "permitted": True, "pending": False,
                    "battery_level": 82, "audio_volume": 0.5, "wifi_ssid": "Home",
                    "mode": "awake", "firmware": "v24.10.803", "telemetry_count": 3,
                    "config_overrides": {}, "config_effective": {}}],
        "schedule_modules": ["MENTOR_BEHAVIOR"], "recent": []}

print(json.dumps({
    "full":         m.normalize_telemetry(full),
    "nodata":       m.normalize_telemetry(nodata),
    "empty":        m.normalize_telemetry(empty),
    "empty_nodata": m.normalize_telemetry(empty_nodata),
    "notok":        m.normalize_telemetry({"ok": False, "device_id": dev,
                                           "error": "unknown device"}),
    "conn":         m.normalize_connection(conn),
    "fleet_served": m.normalize_fleet(snap),
    "fleet_none":   m.normalize_fleet({"ok": True, "app": "moxie-supervisor", "robots": []}),
}))
`;
let FIX;
try {
  FIX = JSON.parse(execFileSync("python3", ["-c", PY, repo, DEV], { encoding: "utf8" }));
} catch (e) {
  skip("python3 could not build the fixtures from server/moxie_server/fleet/ — " + e.message);
}
/* The fixture builder must not be able to hand back a hollow shell. */
ok(FIX.full.ok === true && FIX.full.count === 3 && FIX.full.history.length === 7,
   "fixture: the real normalize_telemetry produced a populated 📈 payload");
ok(FIX.notok.ok === false && FIX.notok.error === "unknown device",
   "fixture: the real normalize_telemetry produced the {ok:false} payload");
ok(FIX.fleet_served.robots.length === 1 && FIX.fleet_served.robots[0].device_id === DEV,
   "fixture: the real normalize_fleet produced one permitted robot");
ok(FIX.fleet_none.robots.length === 0, "fixture: the real normalize_fleet produced an empty fleet");

/* `/local/state` is the parent-app REST shape (children/robots), not a fleet snapshot. */
const STATE = { robots: [{ id: "r1", name: "Moxie", serial: "SN-FIXTURE",
                           "pairing-status": "paired", "wifi-ssid": "Home" }] };

const site = await serveStatic(STATIC, { extIsHtml: false });
const browser = await puppeteer.launch({
  executablePath: chrome, headless: "new",
  defaultViewport: { width: 1280, height: 1000 },
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});

/* A REAL click (puppeteer resolves a clickable point), so a covered, zero-sized or
 * off-screen control throws instead of "working". The 1280x1000 viewport is load-bearing:
 * at 800x600 the 🤖 Moxie tab is not clickable at all. */
async function clickReal(page, sel) {
  const el = await page.$(sel);
  if (!el) return false;
  await el.click();
  return true;
}

/* ---- the six render paths ------------------------------------------------------- */
const PATHS = {
  norobot: { n: 1, marker: "no robot connected",  button: false },
  offline: { n: 2, marker: "supervisor offline",  button: false },
  notok:   { n: 3, marker: "unknown device",      button: false },
  nodata:  { n: 4, marker: "nothing is being saved", button: true,
             emptyAfter: "nothing is being saved" },
  empty:   { n: 5, marker: "No events yet",       button: false },
  full:    { n: 6, marker: "History since",       button: true,
             emptyAfter: "No events yet" },
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function drive(mode, mutate) {
  const state = { deletes: [], gets: 0, erased: false };
  const page = await browser.newPage();
  /* EVERY drive is a first visit: js/core.js auto-enters the app when localStorage holds a
   * token, which would make the sweep order-dependent (path 1 via login, 2-6 returning). */
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  const { errs, aborted } = watchPage(page);
  await page.setRequestInterception(true);
  page.on("request", (r) => {
    if (r.isInterceptResolutionHandled()) return;
    const p = new URL(r.url()).pathname;
    const J = (o, status = 200) =>
      r.respond({ status, contentType: "application/json", body: JSON.stringify(o) });
    if (p === CARD_JS && mutate)
      return r.respond({ status: 200, contentType: "text/javascript; charset=utf-8",
                         body: mutate(APPJS) });
    if (/\.png$/.test(p)) return r.respond({ status: 200, contentType: "image/png", body: PNG });
    if (p === "/local/quicklogin") return J({ token: "t-fixture", email: "parent@home.lan" });
    if (p === "/local/state") return J(STATE);
    if (p === "/local/fleet")
      return J(mode === "norobot" ? FIX.fleet_none : FIX.fleet_served);
    if (p === "/local/connection") return J(FIX.conn);
    if (p === TELE) {
      if (r.method() === "DELETE") {
        state.deletes.push({ path: p, method: r.method(), at: Date.now() });
        state.erased = true;
        const body = mode === "nodata" ? FIX.empty_nodata : FIX.empty;
        return J({ ...body, erased: true, records: ["packets", "daily", "mentor"] });
      }
      state.gets++;
      if (state.erased) return J(mode === "nodata" ? FIX.empty_nodata : FIX.empty);
      /* A REAL 503 — the only way to reach `refreshInsights`'s "telemetry threw" branch,
       * and something the browser reports as a console error. Counted so `notable()`
       * forgives exactly the refusals this fixture issued. */
      if (mode === "offline") { aborted.refused++; return J(FIX.notok, 503); }
      if (mode === "notok") return J(FIX.notok);
      if (mode === "nodata") return J(FIX.nodata);
      if (mode === "empty") return J(FIX.empty);
      return J(FIX.full);
    }
    /* Every other card on the page (🛡️ Safety, 🧠 Brain, 📦 Content, 🎚️ Voice…) fires its
     * own XHR on entry. Answering them {ok:false} renders each one's "unavailable" branch,
     * which is honest and inert — this suite makes no claim about those cards. */
    if (p.startsWith("/local/") || p.startsWith("/api/")) return J({ ok: false, error: "not in this fixture" });
    /* Everything else is the console's own static assets, served by the harness. The one
     * that is NOT there is `/favicon.ico` — `server/static/` ships none, so Chrome's
     * automatic request for it 404s on the FIRST page this browser opens and is cached as
     * a failure for the rest of the run. Counted here rather than answered, because the
     * real FastAPI console 404s it too and the fixture should not paper over that. */
    if (p === "/favicon.ico") aborted.refused++;
    return r.continue();
  });
  /* `domcontentloaded` + an explicit wait, never `networkidle*`: the console polls. */
  await page.goto(site.url + "/", { waitUntil: "domcontentloaded", timeout: 20000 });
  await page.waitForSelector("#btn-login", { timeout: 10000 });
  await clickReal(page, "#btn-login");
  await page.waitForFunction(
    "!document.querySelector('#tabs').classList.contains('hidden')", { timeout: 10000 });
  await clickReal(page, '.tab[data-tab="moxie"]');
  await page.waitForFunction(
    "(document.querySelector('#robot-insights')||{}).textContent && " +
    "document.querySelector('#robot-insights').textContent.trim().length > 0",
    { timeout: 10000 });
  return { page, state, errs, aborted };
}

const readCard = (page) => page.$eval("#robot-insights", (e) => ({
  text: e.textContent,
  strip: !!e.querySelector(".connstrip"),
  rows: e.querySelectorAll(".evlog:not(.conn) .ev").length,
  rowNames: [...e.querySelectorAll(".evlog:not(.conn) .ev b")].map((b) => b.textContent),
  days: [...e.querySelectorAll(".tweek .tday")].map((d) => d.getAttribute("title")),
  counts: [...e.querySelectorAll(".livegrid .k")].map((k) => [
    k.querySelector("span").textContent, Number(k.querySelector("b").textContent)]),
  btn: (() => {
    const b = e.querySelector("#btn-telemetry-forget");
    return b && { label: b.textContent, armed: b.dataset.armed || "", disabled: b.disabled };
  })(),
}));

/** One render path, end to end, into checks collector `C` (a throwaway one under the teeth). */
async function sweepPath(C, mode, { mutate = null, deep = false } = {}) {
  const spec = PATHS[mode];
  const { page, state, errs, aborted } = await drive(mode, mutate);
  const tag = `path ${spec.n} (${mode})`;
  try {
    await page.waitForFunction(
      (m) => document.querySelector("#robot-insights").textContent.includes(m),
      { timeout: 10000 }, spec.marker).catch(() => {});
    const card = await readCard(page);

    C.ok(card.text.includes(spec.marker),
         `${tag}: the card must render its own state — expected ${JSON.stringify(spec.marker)}`);
    C.ok(card.strip,
         `${tag}: the 🔌 connection strip is promised in EVERY branch and is missing here`);
    C.eq(state.deletes.length, 0,
         `${tag}: merely rendering the card must never issue a DELETE`);
    C.eq(!!card.btn, spec.button,
         `${tag}: erase button presence`);

    if (!spec.button) {
      C.ok(!/Erase history/.test(card.text),
           `${tag}: a branch with nothing to erase must not show the words "Erase history"`);
    } else {
      C.eq(card.btn.label, "Erase history", `${tag}: the button's resting label`);
      C.eq(card.btn.armed, "", `${tag}: the button must start UNARMED`);
      C.eq(card.btn.disabled, false, `${tag}: the erase button must be enabled`);

      const click = () => clickReal(page, "#btn-telemetry-forget");
      /* --- click 1: ARMS, and must put nothing on the wire --- */
      C.ok(await click(), `${tag}: the erase button must be clickable`);
      await page.waitForFunction(() => (document.querySelector("#btn-telemetry-forget") || {}).dataset?.armed === "1",
                                 { timeout: 5000 }).catch(() => {});
      C.eq(state.deletes.length, 0,
           `${tag}: ONE click must NOT erase — no DELETE may reach the wire`);
      const armed = (await readCard(page)).btn;
      C.eq(armed && armed.label, "Click again to erase",
           `${tag}: the first click must ARM the button`);
      C.eq(armed && armed.armed, "1",
           `${tag}: the first click must mark the button armed`);

      /* --- click 2: exactly one DELETE, and it must belong to THIS click --- */
      const t2 = Date.now();
      C.ok(await click(), `${tag}: the armed button must still be clickable`);
      await page.waitForFunction(
        () => /Erased the stored activity history|Nothing was stored/.test(
          document.querySelector("#robot-insights").textContent),
        { timeout: 8000 }).catch(() => {});
      C.eq(state.deletes.length, 1,
           `${tag}: exactly one DELETE must have been issued`);
      C.ok(state.deletes.every((d) => d.path === TELE && d.method === "DELETE"),
           `${tag}: the request must be DELETE ${TELE}`);
      C.ok(state.deletes.length === 1 && state.deletes[0].at >= t2,
           `${tag}: the DELETE must be issued by the SECOND click, not the first`);

      /* --- the card must forget what it was already showing --- */
      const after = await readCard(page);
      C.eq(after.rows, 0,
           `${tag}: after erasing, the card must show NO stale event rows`);
      C.ok(!after.btn,
           `${tag}: with nothing left to erase the button must be gone`);
      C.ok(after.text.includes(spec.emptyAfter),
           `${tag}: after erasing, the card must show the empty history ` +
           `(${JSON.stringify(spec.emptyAfter)})`);
      C.ok(/Erased the stored activity history/.test(after.text),
           `${tag}: the card must tell the parent what was erased`);
      C.ok(after.strip,
           `${tag}: the 🔌 strip must survive the erase re-render`);
    }

    /* The 📈 card's own content, read from the DOM and compared against the payload the
     * interceptor actually served — no literals, so fixture and assertion cannot drift. */
    if (deep && mode === "full") {
      C.ok(card.text.includes(`${FIX.full.count} events kept`),
           "path 6: the header must count the events the payload carried");
      C.ok(card.text.includes(`${FIX.full.totals.total} all time`),
           "path 6: the header must carry the lifetime total");
      C.ok(card.text.includes(FIX.full.totals.first_day),
           "path 6: the note must name the first day the store reaches back to");
      C.ok(card.text.includes(FIX.full.policy),
           "path 6: the note must name the privacy policy the payload reported");
      C.eq(card.days.length, FIX.full.history.length,
           "path 6: one week bar per day in the payload's history");
      C.ok(card.days.every((t, i) => t.startsWith(FIX.full.history[i].day + ": " +
                                                  FIX.full.history[i].count)),
           "path 6: each bar must be titled with its own day and count, oldest→newest");
      C.eq(JSON.stringify(card.counts),
           JSON.stringify(FIX.full.by_event.map((c) => [c.event, c.count])),
           "path 6: the by-event table must be the payload's, in the payload's order");
      C.eq(card.rows, FIX.full.events.length,
           "path 6: one event row per event in the payload");
      C.eq(JSON.stringify(card.rowNames),
           JSON.stringify(FIX.full.events.map((e) => e.event_name)),
           "path 6: the event rows must name the payload's events, newest first");
    }

    /* The 6 s disarm: an armed destructive button that stays armed forever is a trap. */
    if (deep && mode === "full") {
      const p2 = await drive("full", mutate);
      try {
        await p2.page.waitForSelector("#btn-telemetry-forget", { timeout: 8000 });
        const armedNow = () => p2.page.waitForFunction(() =>
          (document.querySelector("#btn-telemetry-forget") || {}).dataset?.armed === "1", { timeout: 5000 }).catch(() => {});
        await clickReal(p2.page, "#btn-telemetry-forget");
        await armedNow();
        C.eq((await readCard(p2.page)).btn.armed, "1", "disarm: armed by the first click");
        await sleep(6400);
        const cooled = (await readCard(p2.page)).btn;
        C.eq(cooled && cooled.armed, "", "disarm: the arm must expire after ~6 s");
        C.eq(cooled && cooled.label, "Erase history",
             "disarm: the label must return to rest when the arm expires");
        await clickReal(p2.page, "#btn-telemetry-forget");
        await armedNow();
        C.eq(p2.state.deletes.length, 0,
             "disarm: a click after the arm expired must RE-ARM, never erase");
      } finally { await p2.page.close(); }
    }

    /* WHAT THE BROWSER ITSELF SAID: uncaught errors AND console errors (a 404'd <script> only
     * surfaces there), forgiving by COUNT exactly path 2's deliberate 503 and the favicon. */
    const left = notable(errs, aborted);
    C.eq(left.length, 0,
         `${tag}: the page must raise no uncaught errors and no unexplained console ` +
         `errors — ${left.length}, first: ${left.slice(0, 3).join(" | ")}`);
  } finally {
    await page.close();
  }
}

/* ---- the honest run ------------------------------------------------------------- */
for (const mode of Object.keys(PATHS)) await sweepPath({ ok, eq }, mode, { deep: true });

/* ---- TEETH: the wiring moved out of `render`, back to the terminal branch only ---------- */
const BRANCH = (src) => src
  .replace("    const b=box.querySelector('#btn-telemetry-forget');\n" +
           "    if(b) armErase(b, 'Click again to erase', ()=>eraseTelemetry(deviceId));",
           "    void 0;")
  .replace("    +`<div class=\"evlog\">${rows}</div><p class=\"tnote\">${note}</p>`);",
           "    +`<div class=\"evlog\">${rows}</div><p class=\"tnote\">${note}</p>`);\n" +
           "  { const bb=box.querySelector('#btn-telemetry-forget');\n" +
           "    if(bb) armErase(bb, 'Click again to erase', ()=>eraseTelemetry(deviceId)); }");
ok(BRANCH(APPJS) !== APPJS, "teeth: the mutation must actually change js/insights.js");
const teeth = {};
for (const mode of ["nodata", "full"]) {
  const C = makeChecks();
  await sweepPath(C, mode, { mutate: BRANCH });
  teeth[mode] = C.fails;
}
ok(teeth.nodata.length > 0 && teeth.nodata.some((f) => /path 4 \(nodata\)/.test(f)),
   "teeth: path 4 must REDDEN with the wiring moved to the terminal branch — the defect the " +
   `comment above refreshInsights predicted. Got: ${teeth.nodata.join(" | ")}`);
eq(teeth.full.length, 0, "teeth: path 6 must still PASS under the same mutation (the wiring merely " +
   `moved there), or a single click test would have been enough. Got: ${teeth.full.join(" | ")}`);
await browser.close();
site.close();
finish(LABEL, { fails, count });
