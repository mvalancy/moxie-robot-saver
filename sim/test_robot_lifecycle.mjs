/* test_robot_lifecycle.mjs — the PARENT CONSOLE's Unpair and Factory reset (server/static,
 * js/robot.js), in a real browser. The invariants, each over the intercepted requests:
 *
 *   S1 nothing is sent until the confirmation word is typed (a disabled button, Enter and
 *      Escape all send nothing; the sheet is a named modal that takes focus)
 *   S2 a plain unpair is exactly one DELETE /api/robots/{id}; the child is kept
 *   S3 the erase choice is the console's EXISTING calls, in the doc's order: memory and
 *      history erased before the unpair, the child's profile deleted after it
 *   S4 a failed erase stops the unpair (the erase controls vanish with the robot)
 *   S5 a factory reset needs RESET plus "cannot be undone", sends ?rfs=1, and shows the
 *      restore_factory code with every step's basis and the honest limit
 *   S6 with no robot record, the reset code is shown and NOTHING is sent
 *   S7 at phone width the sheet fits the screen and its button is not covered
 *
 * No FastAPI: `serveStatic` serves server/static and every `/local/*` and `/api/*` call is
 * answered at the browser. The DELETE answers, the reset view and the fleet come out of the
 * REAL server modules (`moxie_server.lifecycle`, `moxie_server.fleet`, both dependency-free)
 * in a python3 subprocess, so a fixture cannot drift from what the route returns.
 * TEETH: three mutated copies of js/robot.js (no typed gate; the child deleted whatever the
 * box says; an erase failure ignored) must each redden the scenario that guards it.
 *
 *   node sim/test_robot_lifecycle.mjs
 */
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { requireBrowser, serveStatic, makeChecks, finish, repo, watchPage, notable, PHONE }
  from "./browser_harness.mjs";

const LABEL = "robot-lifecycle test";
const { puppeteer, chrome, skip } = await requireBrowser(LABEL);
const { fails, ok, eq, count } = makeChecks();

const DEV = "d_lifecycle_01", RID = "r-lifecycle", CID = "c-ada";
const STATIC = join(repo, "server", "static");
const ROBOT_JS = "/js/robot.js";
const SRC = readFileSync(join(STATIC, ROBOT_JS), "utf8");
/* 1x1 transparent PNG: every QR image the console asks for. */
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
  "base64");

const PY = `
import json, sys
repo, dev, rid, cid = sys.argv[1:5]
sys.path.insert(0, repo + "/server")
from moxie_server import fleet, lifecycle as L

access = L.access_view(dev, revoked=True)
child = {"id": cid, "name": "Ada"}
snap = {"ok": True, "app": "moxie-supervisor", "uptime_s": 5,
        "robots": [{"device_id": dev, "permitted": True, "pending": False,
                    "battery_level": 80, "audio_volume": 0.5, "wifi_ssid": "Home",
                    "mode": "awake", "firmware": "v24.10.803", "telemetry_count": 0,
                    "config_overrides": {}, "config_effective": {}}],
        "schedule_modules": [], "recent": []}
print(json.dumps({
    "unpair": L.unpair_result(rid, unpaired=True, factory_reset=False, child=child,
                              codes_voided=1, access=access),
    "reset": L.unpair_result(rid, unpaired=True, factory_reset=True, child=child,
                             codes_voided=0, access=access),
    "view": L.reset_view(),
    "fleet": fleet.normalize_fleet(snap),
}))
`;
let FIX;
try {
  FIX = JSON.parse(execFileSync("python3", ["-c", PY, repo, DEV, RID, CID], { encoding: "utf8" }));
} catch (e) {
  skip("python3 could not build the fixtures from server/moxie_server — " + e.message);
}
ok(FIX.unpair.unpaired === true && FIX.unpair.reset === null && FIX.unpair.details.length >= 3,
   "fixture: the real unpair_result produced a populated unpair answer");
ok(FIX.reset.reset && FIX.reset.reset.qr_payload === '{"debug":{"command":"restore_factory"}}',
   "fixture: the real unpair_result carried the restore_factory code on a reset");
ok(FIX.view.steps.length >= 3 && FIX.fleet.robots.length === 1,
   "fixture: the real reset_view and normalize_fleet produced populated payloads");

const ROBOT = { id: RID, name: "Moxie", serial: "SN-1", "pairing-status": "paired",
                "wifi-ssid": "Home", "mqtt-device-id": DEV, child_id: CID };
const STATE = (robots) => ({ user: { id: "u1", email: "parent@home.lan" },
                             children: [{ id: CID, "child-first-name": "Ada" }], robots });
const MEM = `DELETE /local/robots/${DEV}/memory`, TEL = `DELETE /local/robots/${DEV}/telemetry`;
const UNPAIR = `DELETE /api/robots/${RID}`, KID = `DELETE /api/children/${CID}`;

const site = await serveStatic(STATIC, { extIsHtml: false });
const browser = await puppeteer.launch({
  executablePath: chrome, headless: "new",
  defaultViewport: { width: 1280, height: 1000 },
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** A fresh first visit, logged in, on the Moxie tab. `robots` is what /local/state lists
 *  until an unpair succeeds; every DELETE lands in `st.calls`, in arrival order. */
async function drive({ mutate = null, robots = [ROBOT], viewport = null, failMemory = false } = {}) {
  const st = { calls: [], unpaired: false, qrLoads: 0 };
  const page = await browser.newPage();
  if (viewport) await page.setViewport(viewport);
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  const { errs, aborted } = watchPage(page);
  await page.setRequestInterception(true);
  page.on("request", (r) => {
    if (r.isInterceptResolutionHandled()) return;
    const u = new URL(r.url()), p = u.pathname, m = r.method();
    const J = (o, status = 200) =>
      r.respond({ status, contentType: "application/json", body: JSON.stringify(o) });
    if (p === ROBOT_JS && mutate)
      return r.respond({ status: 200, contentType: "text/javascript; charset=utf-8",
                         body: mutate(SRC) });
    if (/\.png$/.test(p)) {
      if (p === FIX.view.qr_png) st.qrLoads++;
      return r.respond({ status: 200, contentType: "image/png", body: PNG });
    }
    if (p === "/local/quicklogin") return J({ token: "t-fixture", email: "parent@home.lan" });
    if (p === "/local/state") return J(STATE(st.unpaired ? [] : robots));
    if (p === "/local/fleet") return J(FIX.fleet);
    if (p === "/local/factory-reset/payload") return J(FIX.view);
    if (m === "DELETE") {
      st.calls.push(`${m} ${p}${u.search}`);
      if (`${m} ${p}` === MEM) {
        if (failMemory) { aborted.refused++; return J({ ok: false, error: "supervisor not reachable" }, 503); }
        return J({ ok: true, erased: true });
      }
      if (`${m} ${p}` === TEL) return J({ ok: true, erased: true });
      if (`${m} ${p}` === UNPAIR) {
        st.unpaired = true;
        return J(u.search === "?rfs=1" ? FIX.reset : FIX.unpair);
      }
      if (`${m} ${p}` === KID) return r.respond({ status: 204, body: "" });
    }
    /* Every other card fires its own XHR on entry; {ok:false} renders each one's honest
     * "unavailable" branch. An unexpected DELETE also lands here, and in `st.calls`. */
    if (p.startsWith("/local/") || p.startsWith("/api/"))
      return J({ ok: false, error: "not in this fixture" });
    /* server/static ships no favicon; the real console 404s it too (see the insights suite). */
    if (p === "/favicon.ico") aborted.refused++;
    return r.continue();
  });
  await page.goto(site.url + "/", { waitUntil: "domcontentloaded", timeout: 20000 });
  await page.waitForSelector("#btn-login", { timeout: 10000 });
  await page.click("#btn-login");
  await page.waitForFunction(
    "!document.querySelector('#tabs').classList.contains('hidden')", { timeout: 10000 });
  await page.click('.tab[data-tab="moxie"]');
  await page.waitForFunction(
    (sel) => !document.querySelector(sel).classList.contains("hidden"), { timeout: 10000 },
    robots.length ? "#moxie-card" : "#moxie-none");
  /* Wait for the tab's RECORDED state, never a live sample: the fleet answer unhides Robot
   * access above #moxie-none, and live state, insights and safety fill #moxie-card above
   * the lifecycle buttons. A shift landing between puppeteer's click-point and its click
   * taps whatever moved underneath (measured: S6 opened nothing, 2 runs of 2). */
  await page.waitForFunction(() => ["#robot-live", "#robot-insights", "#robot-safety"]
    .every((s) => document.querySelector(s).innerHTML.trim().length > 0), { timeout: 10000 });
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
  return { page, st, errs, aborted };
}

const sheet = (page) => page.evaluate(() => {
  const $ = (s) => document.querySelector(s);
  const d = $("#lc-sheet");
  const shown = (s) => { const e = $(s); return !!e && !e.closest(".hidden") && e.getClientRects().length > 0; };
  const texts = (s) => [...document.querySelectorAll(s)].map((e) => e.textContent);
  const r = d.getBoundingClientRect();
  return {
    open: d.open, title: $("#lc-title").textContent,
    named: (document.getElementById(d.getAttribute("aria-labelledby")) || {}).textContent,
    focusInside: d.contains(document.activeElement),
    go: $("#lc-go").disabled, ask: shown("#lc-ask"), done: shown("#lc-done"),
    code: shown("#lc-code"), erase: shown("#lc-erase"), ack: shown("#lc-ack-row"),
    what: texts("#lc-what li"), memLabel: $("#lc-erase-memory-text").textContent,
    message: $("#lc-message").textContent, details: texts("#lc-details li"),
    steps: texts("#lc-steps li"), basis: texts("#lc-steps .lc-basis"),
    limit: $("#lc-limit").textContent, status: $("#lc-status").textContent,
    qr: $("#lc-qr").getAttribute("src"), qrLoaded: $("#lc-qr").naturalWidth > 0,
    box: { left: r.left, right: r.right, width: r.width },
    viewport: innerWidth, scrollWidth: document.documentElement.scrollWidth,
  };
});
const isOpen = (page) => page.waitForFunction(
  () => document.querySelector("#lc-sheet").open, { timeout: 5000 }).catch(() => {});
const isDone = (page) => page.waitForFunction(
  () => !document.querySelector("#lc-done").classList.contains("hidden"),
  { timeout: 8000 }).catch(() => {});
async function retype(page, text) {
  await page.$eval("#lc-confirm", (e) => { e.value = ""; });
  await page.type("#lc-confirm", text);
}

/* ---- the scenarios, each into a checks collector `C` ------------------------------ */
const SCENARIOS = {
  async S1(C, o) {
    const { page, st, errs, aborted } = await drive(o);
    try {
      await page.click("#btn-unpair");
      await isOpen(page);
      let s = await sheet(page);
      C.ok(s.open, "S1: Unpair opens the confirmation sheet");
      C.eq(s.named, "Unpair this robot", "S1: the dialog is named by its title");
      C.ok(s.focusInside, "S1: focus moves into the sheet");
      C.ok(s.memLabel.includes("Ada"), "S1: the erase choice names the robot's child");
      C.eq(s.go, true, "S1: the confirm button starts disabled");
      await page.click("#lc-go").catch(() => {});
      await page.focus("#lc-confirm");
      await page.keyboard.press("Enter");
      await retype(page, "unpai");
      await page.keyboard.press("Enter");
      await sleep(300);
      C.eq((await sheet(page)).go, true, "S1: a partial word keeps the button disabled");
      C.eq(st.calls.length, 0, "S1: nothing may be sent before the word is typed " +
           `(a disabled click, Enter twice) — sent ${JSON.stringify(st.calls)}`);
      await page.type("#lc-confirm", "r");
      C.eq((await sheet(page)).go, false, "S1: typing unpair enables the button");
      await page.keyboard.press("Escape");
      await page.waitForFunction(() => !document.querySelector("#lc-sheet").open,
                                 { timeout: 5000 }).catch(() => {});
      C.eq((await sheet(page)).open, false, "S1: Escape closes the sheet");
      C.eq(st.calls.length, 0, "S1: cancelling sends nothing");
      C.eq(notable(errs, aborted).length, 0, `S1: no page errors — ${notable(errs, aborted).slice(0, 3)}`);
    } finally { await page.close(); }
  },

  async S2(C, o) {
    const { page, st, errs, aborted } = await drive(o);
    try {
      await page.click("#btn-unpair");
      await isOpen(page);
      await retype(page, "moxie");               // the robot's name, any case
      C.eq((await sheet(page)).go, false, "S2: the robot's name confirms too");
      await page.click("#lc-go");
      await isDone(page);
      const s = await sheet(page);
      C.eq(JSON.stringify(st.calls), JSON.stringify([UNPAIR]),
           "S2: a plain unpair is exactly one DELETE of the robot");
      C.eq(s.message, FIX.unpair.message, "S2: the server's headline is shown");
      C.eq(JSON.stringify(s.details), JSON.stringify(FIX.unpair.details.map((d) => d.text)),
           "S2: every line the server sent is shown, in order (the child kept)");
      C.ok(!s.code, "S2: no reset code after a plain unpair");
      await page.click("#lc-close");
      await page.waitForFunction(
        () => !document.querySelector("#moxie-none").classList.contains("hidden"),
        { timeout: 8000 }).catch(() => {});
      C.ok(await page.$eval("#moxie-none", (e) => !e.classList.contains("hidden")),
           "S2: afterwards the tab shows no paired robot");
      C.eq(notable(errs, aborted).length, 0, `S2: no page errors — ${notable(errs, aborted).slice(0, 3)}`);
    } finally { await page.close(); }
  },

  async S3(C, o) {
    const { page, st, errs, aborted } = await drive(o);
    try {
      await page.click("#btn-unpair");
      await isOpen(page);
      await page.click("#lc-erase-memory");
      await page.click("#lc-erase-child");
      await retype(page, "UNPAIR");
      await page.click("#lc-go");
      await isDone(page);
      const s = await sheet(page);
      C.eq(JSON.stringify(st.calls), JSON.stringify([MEM, TEL, UNPAIR, KID]),
           "S3: erase memory and history, then unpair, then delete the child — the " +
           "console's existing calls, in the doc's order");
      C.ok(s.details.some((t) => /erased from this server/.test(t)),
           "S3: the parent is told what was erased");
      C.ok(s.details.some((t) => /profile was deleted/.test(t)) &&
           !s.details.some((t) => /profile was kept/.test(t)),
           "S3: the child line says deleted, never also kept");
      C.eq(notable(errs, aborted).length, 0, `S3: no page errors — ${notable(errs, aborted).slice(0, 3)}`);
    } finally { await page.close(); }
  },

  async S4(C, o) {
    const { page, st, errs, aborted } = await drive({ ...o, failMemory: true });
    try {
      await page.click("#btn-unpair");
      await isOpen(page);
      await page.click("#lc-erase-memory");
      await retype(page, "unpair");
      await page.click("#lc-go");
      await page.waitForFunction(
        () => /Nothing was unpaired/.test(document.querySelector("#lc-status").textContent),
        { timeout: 8000 }).catch(() => {});
      await sleep(300);
      const s = await sheet(page);
      C.eq(JSON.stringify(st.calls), JSON.stringify([MEM]),
           "S4: a failed erase must stop the unpair — no DELETE of the robot");
      C.ok(/Nothing was unpaired/.test(s.status), "S4: the parent is told nothing was unpaired");
      C.ok(s.ask && !s.done && s.go === false, "S4: the sheet stays, ready to retry");
      C.eq(notable(errs, aborted).length, 0, `S4: no page errors — ${notable(errs, aborted).slice(0, 3)}`);
    } finally { await page.close(); }
  },

  async S5(C, o) {
    const { page, st, errs, aborted } = await drive(o);
    try {
      await page.click("#btn-factory-reset");
      await isOpen(page);
      let s = await sheet(page);
      C.eq(s.named, "Factory reset this robot", "S5: the reset sheet is named for what it does");
      C.ok(s.ack, "S5: the reset asks for 'cannot be undone' as well as the word");
      C.ok(s.what.includes(FIX.view.limit) && s.what.includes(FIX.view.effect.text),
           "S5: before anything is sent, the sheet states the effect and the honest limit");
      await retype(page, "unpair");
      C.eq((await sheet(page)).go, true, "S5: UNPAIR does not confirm a reset");
      await retype(page, "reset");
      C.eq((await sheet(page)).go, true, "S5: RESET alone is not enough");
      await page.click("#lc-ack");
      C.eq((await sheet(page)).go, false, "S5: RESET plus the acknowledgement enables it");
      await page.click("#lc-go");
      await isDone(page);
      await page.waitForFunction(() => document.querySelector("#lc-qr").naturalWidth > 0,
                                 { timeout: 5000 }).catch(() => {});
      s = await sheet(page);
      C.eq(JSON.stringify(st.calls), JSON.stringify([UNPAIR + "?rfs=1"]),
           "S5: a factory reset is exactly one DELETE ?rfs=1");
      C.ok(s.code, "S5: the reset code is shown");
      C.eq(s.qr, FIX.view.qr_png, "S5: the code image is the server-rendered restore_factory PNG");
      C.ok(s.qrLoaded && st.qrLoads >= 1, "S5: the code image actually loaded");
      C.eq(s.steps.length, FIX.view.steps.length, "S5: one line per instruction");
      C.eq(JSON.stringify(s.basis), JSON.stringify(FIX.view.steps.map((x) => x.basis)),
           "S5: every instruction is labelled with where it comes from");
      C.eq(s.limit, FIX.view.limit, "S5: the honest limit is stated beside the code");
      C.eq(notable(errs, aborted).length, 0, `S5: no page errors — ${notable(errs, aborted).slice(0, 3)}`);
    } finally { await page.close(); }
  },

  async S6(C, o) {
    const { page, st, errs, aborted } = await drive({ ...o, robots: [] });
    try {
      await page.click("#moxie-none details.lc-entry summary");
      await page.click("#btn-reset-code");
      await isOpen(page);
      let s = await sheet(page);
      C.eq(s.named, "Factory reset code", "S6: with no record, the sheet only offers the code");
      C.ok(!s.erase && s.ack, "S6: no erase choice (no robot to erase for), but the acknowledgement");
      await retype(page, "reset");
      await page.click("#lc-ack");
      await page.click("#lc-go");
      await isDone(page);
      s = await sheet(page);
      C.eq(st.calls.length, 0, "S6: showing the code changes nothing on this server");
      C.ok(s.code && s.qr === FIX.view.qr_png, "S6: the reset code is shown");
      C.eq(notable(errs, aborted).length, 0, `S6: no page errors — ${notable(errs, aborted).slice(0, 3)}`);
    } finally { await page.close(); }
  },

  async S7(C, o) {
    const { page, errs, aborted } = await drive({ ...o, viewport: PHONE });
    try {
      await page.click("#btn-factory-reset");
      await isOpen(page);
      await retype(page, "reset");
      await page.click("#lc-ack");
      const s = await sheet(page);
      C.ok(s.box.left >= 0 && s.box.right <= s.viewport + 0.5,
           `S7: the sheet fits a ${s.viewport}px phone screen (${s.box.left}..${s.box.right})`);
      C.ok(s.scrollWidth <= s.viewport, "S7: no sideways scrolling at phone width");
      const hit = await page.$eval("#lc-go", (b) => {
        b.scrollIntoView({ block: "center" });
        const r = b.getBoundingClientRect();
        return document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2) === b;
      });
      C.ok(hit, "S7: nothing covers the confirm button at phone width");
      C.eq(notable(errs, aborted).length, 0, `S7: no page errors — ${notable(errs, aborted).slice(0, 3)}`);
    } finally { await page.close(); }
  },
};

/** One scenario into collector `C`; an interaction that throws (a control that never
 *  became clickable) is that scenario's failure, not the end of the run. */
async function run(C, name, o = {}) {
  try { await SCENARIOS[name](C, o); }
  catch (e) { C.ok(false, `${name} threw: ${e.message}`); }
}

/* ---- the honest run ------------------------------------------------------------- */
for (const name of Object.keys(SCENARIOS)) await run({ ok, eq }, name);

/* ---- TEETH: each mutation must redden the scenario that guards it ------------------- */
const TEETH = [
  ["no typed gate", "S1", (s) => s.replace("function lcConfirmed(){", "function lcConfirmed(){ return true;")],
  ["child deleted whatever the box says", "S2",
   (s) => s.replace("if($('#lc-erase-child').checked && res.child_id){", "if(res.child_id){")],
  ["erase failure ignored", "S4",
   (s) => s.replace("catch(e){ return stop(oops(e,'erase failed')+' Nothing was unpaired; you can try again.'); }",
                    "catch(e){}")],
];
for (const [what, scenario, mutate] of TEETH) {
  ok(mutate(SRC) !== SRC, `teeth: the "${what}" mutation must actually change js/robot.js`);
  const C = makeChecks();
  await run(C, scenario, { mutate });
  ok(C.fails.length > 0, `teeth: ${scenario} must redden when js/robot.js has "${what}"`);
  console.log(`   teeth "${what}": ${scenario} reddened with ${C.fails.length} failure(s); ` +
              `first: ${C.fails[0]}`);
}
await browser.close();
site.close();
finish(LABEL, { fails, count });
