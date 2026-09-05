/* test_console_insights.mjs — the PARENT CONSOLE's 📈 Insights card, in a real browser.
 *
 * WHY THIS FILE EXISTS. Until it landed, `grep -rln "server/static" sim/test_*.mjs` came
 * back EMPTY. Every one of the ~28 headless suites in this repo drives `sim/web` — the
 * public simulator — and not one of them had ever loaded `server/static/index.html`, the
 * ~2,470 lines a parent actually uses. So 📈 Insights, 📦 Content, 🧠 Brain, 🛡️ Safety and
 * the robot list were asserted only through Python ROUTE tests, which can prove what the
 * server answers and can prove nothing whatsoever about whether a button in the page ever
 * wires itself up. PR #136 shipped the two-click-armed **Erase history** button on this
 * card and had to report, honestly, that no headless click had ever touched it.
 *
 * THE BUG THE AUTHOR POINTED AT. `server/static/app.js`:337 says, above `refreshInsights`:
 *
 *     // Wiring the 🧽 button inside `render` rather than after each call: this function
 *     // returns early from four branches, and an erase button that works in three of them
 *     // is worse than none — a parent would learn it sometimes does nothing.
 *
 * That is the author naming the defect class and the mitigation in the same breath, with
 * nothing asserting either. Counted today the function has FIVE early returns and a sixth
 * terminal render (the comment's "four" predates one of them — see the table below), and
 * the erase button appears in exactly two of the six. The invariant this suite holds is
 * therefore stated over ALL SIX render paths, which is strictly stronger than the comment:
 *
 *     in every render path, EITHER the button is absent, OR it is present AND armed AND a
 *     second click issues exactly one DELETE.
 *
 *   ┌────────────────────────┬──────────────────────────────────┬────────┐
 *   │ render path            │ reached by                       │ button │
 *   ├────────────────────────┼──────────────────────────────────┼────────┤
 *   │ 1 !deviceId            │ fleet with no permitted robot    │ absent │
 *   │ 2 telemetry threw      │ GET /telemetry → 503             │ absent │
 *   │ 3 !t.ok                │ GET /telemetry → 200 {ok:false}  │ absent │
 *   │ 4 t.persisted===false  │ NO_DATA policy, count > 0        │ SHOWN  │
 *   │ 5 !count && !total     │ nothing recorded yet             │ absent │
 *   │ 6 terminal render      │ a normal history                 │ SHOWN  │
 *   └────────────────────────┴──────────────────────────────────┴────────┘
 *
 * NO FASTAPI, ANYWHERE. CI's hermetic environment cannot boot `server/moxie_server`, so
 * the console's assets are served by the harness's own static server (`serveStatic`, added
 * for this suite) and every `/local/*` XHR is answered AT THE BROWSER with
 * `page.setRequestInterception` — the idiom `test_mic_spend.mjs` and `test_ambient_guard.mjs`
 * established for `/api/*`.
 *
 * THE FIXTURES ARE BUILT BY THE REAL NORMALIZERS. `server/moxie_server/fleet.py` is
 * deliberately dependency-free ("Pure + dependency-free (no fastapi/network here)"), and
 * `/local/robots/{id}/telemetry` is literally `normalize_telemetry(supervisor_json)`. So
 * this suite hands SUPERVISOR payloads to the real `normalize_telemetry` /
 * `normalize_connection` / `normalize_fleet` in a python3 subprocess and serves whatever
 * comes back. A hand-written fixture would let the suite pass forever against a response
 * shape the route stopped sending — the same class of lie as PR #82's 770 assertions that
 * read a file while Web Audio was stubbed.
 *
 * NOTHING IS ASSERTED ON A COUNTER THE PAGE KEEPS ABOUT ITSELF. Every claim is either an
 * INTERCEPTED REQUEST (method, path and the millisecond it arrived, so "the DELETE came
 * from the SECOND click" is a fact about the wire and not about a label) or a DOM fact read
 * out of Chrome (`textContent`, element counts, `data-armed`, `disabled`).
 *
 * TEETH. Three mutations of `app.js` are served to the browser and the whole branch sweep
 * is re-run against each; a mutation that reddens nothing would mean the sweep proves
 * nothing, so each one asserts BOTH that the edit applied and that named assertions failed:
 *   · `arming`   — one click fires the DELETE (the arming removed).
 *   · `refresh`  — `eraseTelemetry` stops re-reading, so the card keeps its stale rows.
 *   · `branch`   — the wiring moves OUT of `render` and back to the terminal branch only:
 *                  EXACTLY the "works in three of them" shape the author's comment feared.
 *                  Its signature is asymmetric on purpose — path 6 keeps passing while
 *                  path 4 reddens — which is what makes the per-branch sweep meaningful
 *                  rather than a single click test wearing six hats.
 *
 *   node sim/test_console_insights.mjs
 */
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { requireBrowser, serveStatic, makeChecks, finish, repo } from "./browser_harness.mjs";

const LABEL = "console-insights test";
const { puppeteer, chrome, skip } = await requireBrowser(LABEL);
const { fails, ok, eq, count } = makeChecks();

const DEV = "d_console_insights_01";
const STATIC = join(repo, "server", "static");
const APPJS = readFileSync(join(STATIC, "app.js"), "utf8");
const TELE = `/local/robots/${DEV}/telemetry`;
/* 1×1 transparent PNG — the console asks for four QR images the fixture has no server for. */
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
  "base64");

/* ---- fixtures, through the real server-side normalizers ------------------------------
 * Inputs here are SUPERVISOR payloads (what `moxie_server` fetches); outputs are exactly
 * what the console route returns, because the same function computes them. */
const PY = `
import importlib.util, json, sys
repo, dev = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location("fleet", repo + "/server/moxie_server/fleet.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

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
  skip("python3 could not build the fixtures from server/moxie_server/fleet.py — " + e.message);
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

/* A REAL click, not `el.click()` inside `evaluate`: puppeteer scrolls the element into
 * view, resolves a CLICKABLE POINT and dispatches a genuine mouse event at it, so a
 * control that is in the DOM but covered, zero-sized or off-screen throws here instead of
 * quietly "working". `evaluate(e => e.click())` would pass on all three.
 *
 * The 1280×1000 viewport above is load-bearing for the same reason: at puppeteer's 800×600
 * default the 🤖 Moxie tab sits at x=766–883 and is not clickable at all. */
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
  const errs = [];
  const page = await browser.newPage();
  /* EVERY drive is a first visit. `app.js`:3 reads `localStorage.moxie_token` at parse
   * time and its last line auto-enters the app when one is there — so the second drive in
   * a run would land already logged in, `#s-login` hidden and `#btn-login` collapsed to a
   * 0×0 box. All six paths share one loopback origin, so without this the sweep would be
   * ORDER-DEPENDENT: path 1 through the login button, paths 2–6 through the returning-
   * parent path. `evaluateOnNewDocument` runs before any page script, so the clear beats
   * that read. (The returning-parent path is real and worth its own coverage; it is not
   * this slice, and it is named as a gap in the report.) */
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  page.on("pageerror", (e) => errs.push(String(e)));
  await page.setRequestInterception(true);
  page.on("request", (r) => {
    if (r.isInterceptResolutionHandled()) return;
    const p = new URL(r.url()).pathname;
    const J = (o, status = 200) =>
      r.respond({ status, contentType: "application/json", body: JSON.stringify(o) });
    if (p === "/app.js" && mutate)
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
      if (mode === "offline") return J(FIX.notok, 503);
      if (mode === "notok") return J(FIX.notok);
      if (mode === "nodata") return J(FIX.nodata);
      if (mode === "empty") return J(FIX.empty);
      return J(FIX.full);
    }
    /* Every other card on the page (🛡️ Safety, 🧠 Brain, 📦 Content, 🎚️ Voice…) fires its
     * own XHR on entry. Answering them {ok:false} renders each one's "unavailable" branch,
     * which is honest and inert — this suite makes no claim about those cards. */
    if (p.startsWith("/local/") || p.startsWith("/api/")) return J({ ok: false, error: "not in this fixture" });
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
  return { page, state, errs };
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

/**
 * One render path, end to end. `C` is a checks collector — the real one for the honest
 * run, a throwaway one for each mutation, so "how many reddened" is a measured number.
 */
async function sweepPath(C, mode, { mutate = null, deep = false } = {}) {
  const spec = PATHS[mode];
  const { page, state, errs } = await drive(mode, mutate);
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
      await sleep(400);
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
        await clickReal(p2.page, "#btn-telemetry-forget");
        await sleep(300);
        C.eq((await readCard(p2.page)).btn.armed, "1", "disarm: armed by the first click");
        await sleep(6400);
        const cooled = (await readCard(p2.page)).btn;
        C.eq(cooled && cooled.armed, "", "disarm: the arm must expire after ~6 s");
        C.eq(cooled && cooled.label, "Erase history",
             "disarm: the label must return to rest when the arm expires");
        await clickReal(p2.page, "#btn-telemetry-forget");
        await sleep(400);
        C.eq(p2.state.deletes.length, 0,
             "disarm: a click after the arm expired must RE-ARM, never erase");
      } finally { await p2.page.close(); }
    }

    C.eq(errs.length, 0, `${tag}: the page must raise no uncaught errors — ${errs.join(" | ")}`);
  } finally {
    await page.close();
  }
}

/* ---- the honest run ------------------------------------------------------------- */
for (const mode of Object.keys(PATHS)) await sweepPath({ ok, eq }, mode, { deep: true });

/* ---- TEETH: the same sweep against a mutated console --------------------------- *
 * A suite that cannot fail proves nothing, so each mutation is applied to `app.js` on its
 * way to the browser and the whole sweep re-run. Both halves are asserted: that the edit
 * really landed (a no-op mutation would make the teeth vacuous — the exact failure this
 * repo has been bitten by), and that named assertions went red because of it. */
const MUTATIONS = {
  arming: {
    why: "one click fires the DELETE (the two-click arming removed)",
    modes: ["nodata", "full"],
    apply: (s) => s.replace(
      "if(btn.dataset.armed==='1'){ btn.dataset.armed=''; btn.textContent=original;",
      "if(true){ btn.dataset.armed=''; btn.textContent=original;"),
    expect: /ONE click must NOT erase|must ARM the button/,
  },
  refresh: {
    why: "eraseTelemetry stops re-reading, so the card keeps its stale rows",
    modes: ["nodata", "full"],
    apply: (s) => s.replace(
      "  await refreshInsights(deviceId);\n  const box=$('#robot-insights');",
      "  await Promise.resolve();\n  const box=$('#robot-insights');"),
    expect: /NO stale event rows|button must be gone/,
  },
  branch: {
    why: "the wiring moves out of `render` back to the terminal branch — the author's " +
         "'works in three of them' shape, exactly",
    modes: ["nodata", "full"],
    apply: (s) => s
      .replace("    const b=box.querySelector('#btn-telemetry-forget');\n" +
               "    if(b) armErase(b, 'Click again to erase', ()=>eraseTelemetry(deviceId));",
               "    void 0;")
      .replace("    +`<div class=\"evlog\">${rows}</div><p class=\"tnote\">${note}</p>`);",
               "    +`<div class=\"evlog\">${rows}</div><p class=\"tnote\">${note}</p>`);\n" +
               "  { const bb=box.querySelector('#btn-telemetry-forget');\n" +
               "    if(bb) armErase(bb, 'Click again to erase', ()=>eraseTelemetry(deviceId)); }"),
    expect: /path 4 \(nodata\)/,
  },
};
const teeth = {};
for (const [name, mut] of Object.entries(MUTATIONS)) {
  const mutated = mut.apply(APPJS);
  ok(mutated !== APPJS,
     `teeth/${name}: the mutation must actually change app.js — ${mut.why}`);
  const C = makeChecks();
  for (const mode of mut.modes) await sweepPath(C, mode, { mutate: mut.apply });
  teeth[name] = { red: C.fails.length, of: C.count(), modes: mut.modes.join("+") };
  ok(C.fails.length > 0,
     `teeth/${name}: a mutated console must REDDEN this suite (${mut.why})`);
  ok(C.fails.some((f) => mut.expect.test(f)),
     `teeth/${name}: the failures must be the ones the mutation causes, not collateral — ` +
     `expected ${mut.expect} in: ${C.fails.join(" | ")}`);
}
/* The `branch` mutation's signature is the whole reason the sweep is per-path: the wiring
 * still works where it was moved to (path 6) and is dead where it was removed from
 * (path 4). If both reddened, a single click test would have been enough. */
{
  const C6 = makeChecks();
  await sweepPath(C6, "full", { mutate: MUTATIONS.branch.apply });
  eq(C6.fails.length, 0,
     "teeth/branch: path 6 must still PASS under the branch mutation — the wiring merely " +
     `moved there. Got: ${C6.fails.join(" | ")}`);
  const C4 = makeChecks();
  await sweepPath(C4, "nodata", { mutate: MUTATIONS.branch.apply });
  ok(C4.fails.length > 0,
     "teeth/branch: path 4 must REDDEN under the branch mutation — that is the defect " +
     "the author's comment at app.js:337 predicted");
  teeth.branch.asym = `path6 ${C6.fails.length} red / path4 ${C4.fails.length} red`;
}

console.log("   teeth:", JSON.stringify(teeth));
await browser.close();
site.close();
finish(LABEL, { fails, count });
