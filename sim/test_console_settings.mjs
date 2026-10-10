/* test_console_settings.mjs — the PARENT CONSOLE's ⚙️ Settings card (server/static,
 * js/settings.js) in a real browser, starting with the house's clock: the time zone every
 * robot is told and the appliance keeps bedtime, the day plan and "what time is it" in.
 * The invariants, each over the intercepted requests and the rendered page:
 *
 *   Z1 the Time zone field is prefilled from the robot's `config_effective.timezone_id`
 *      (a house rule, this robot's own zone, or the server's MOXIE_TIMEZONE), and the hint
 *      under it names which; with none, the field is empty and the hint names the default
 *   Z2 saving a zone the parent typed posts `timezone_id` for this robot only
 *   Z3 with "Apply to all robots" ticked it posts `timezone_id` as a house rule
 *   Z4 a save that leaves the field as it was sends no `timezone_id` (a house rule is never
 *      copied into one robot's settings by saving its volume)
 *   Z5 while no zone is chosen and this browser is set to another one, a line outside
 *      Settings names both zones and one click saves the browser's zone as a house rule,
 *      then says so
 *   Z6 no such offer when the browser is in the default zone, or a zone is already chosen
 *   Z7 #309's answer rule holds for the card: a robot save the supervisor applied but could
 *      not write (`saved:false`) says it will be lost on a restart, never "Saved"
 *   Z8 a zone the server refuses (a typo) is said on the card, word for word
 *   Z9 the field's own "use this phone's zone" button names this browser's zone even with a
 *      zone already chosen, puts it in the field and saves nothing by itself; Save settings
 *      then posts it for this robot, or as a house rule with "Apply to all robots" ticked
 *
 * No FastAPI: `serveStatic` serves server/static and every `/local/*` and `/api/*` call is
 * answered at the browser. The fleet views come from the REAL `moxie_server.fleet` over a
 * snapshot whose `config_effective` the REAL `moxie_sdk.cloud_config.merge_config_layers`
 * builds, and the refusal is the REAL whitelist's sentence (`sanitize_config_overrides`), in
 * a python3 subprocess (both dependency-free). The browser's own zone is set per page
 * (`emulateTimezone`). TEETH: mutated copies of js/settings.js (the zone never sent; the
 * field not prefilled; the field reading the robot's own layer only; an untouched zone sent
 * anyway; no offer; the offer saving the default, or saving for one robot; the offer shown
 * over a chosen zone; `saved:false` ignored; a refusal not said; the field's button hidden,
 * or filling in the default) must each redden the scenario that guards it.
 *
 *   node sim/test_console_settings.mjs
 */
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { requireBrowser, serveStatic, makeChecks, finish, repo, watchPage, notable }
  from "./browser_harness.mjs";

const LABEL = "console settings test";
const { puppeteer, chrome, skip } = await requireBrowser(LABEL);
const { fails, ok, eq, count } = makeChecks();

const DEV = "d_bench_01", RID = "r-claimed", TOKEN = "t-fixture";
const STATIC = join(repo, "server", "static");
const SETTINGS_JS = "/js/settings.js";
const SRC = readFileSync(join(STATIC, SETTINGS_JS), "utf8");
const BERLIN = "Europe/Berlin", NEW_YORK = "America/New_York", TYPO = "Mars/Olympus";

const PY = `
import json, sys
repo, dev = sys.argv[1:3]
sys.path.insert(0, repo + "/server")
sys.path.insert(0, repo + "/mqtt")
from moxie_server import fleet
from moxie_sdk import cloud_config as C

def snap(house, own, env):
    house = {"timezone_id": house} if house else {}
    own = {"timezone_id": own} if own else {}
    env = {"timezone_id": env} if env else {}
    return {"ok": True, "app": "content", "uptime_s": 5, "allow_unverified_bots": False,
            "robots": [{"device_id": dev, "child": None, "permitted": True, "pending": False,
                        "permit_label": "added to a parent account", "battery_level": 80,
                        "audio_volume": 0.5, "wifi_ssid": "Home", "mode": "awake",
                        "firmware": "v24.10.803", "telemetry_count": 0,
                        "config_overrides": own,
                        # the supervisor's effective_config: MOXIE_TIMEZONE, house, robot
                        "config_effective": C.merge_config_layers(env, house, own)}],
            "fleet_config": house, "schedule_modules": [], "recent": []}

views = {f"{h}|{o}|{e}": fleet.normalize_fleet(snap(h, o, e)) for h, o, e in (
    ("", "", ""), ("Europe/Berlin", "", ""), ("", "America/New_York", ""),
    ("Europe/Berlin", "America/New_York", ""), ("", "", "Europe/Berlin"),
    ("America/New_York", "", ""))}
try:
    C.sanitize_config_overrides({"timezone_id": "Mars/Olympus"})
    refusal = None
except ValueError as e:
    refusal = str(e)
print(json.dumps({"views": views, "default": C.DEFAULT_TIMEZONE_ID, "refusal": refusal}))
`;
let FIX;
try {
  FIX = JSON.parse(execFileSync("python3", ["-c", PY, repo, DEV], { encoding: "utf8" }));
} catch (e) {
  skip("python3 could not build the fixtures from server/ and mqtt/ — " + e.message);
}
const DEFAULT = FIX.default;
ok(DEFAULT === "America/Los_Angeles",
   `fixture: the default zone was read from cloud_config.py — got ${DEFAULT}`);
ok(typeof FIX.refusal === "string" && FIX.refusal.includes(TYPO),
   `fixture: the whitelist refuses a typo with a sentence — got ${JSON.stringify(FIX.refusal)}`);
ok(FIX.views["Europe/Berlin||"].robots[0].config_sources.timezone_id === "fleet" &&
   FIX.views["Europe/Berlin||"].robots[0].config_effective.timezone_id === BERLIN,
   "fixture: the real normalize_fleet labels a house rule's zone");

const RECORD = { id: RID, "embodied-robot-id": RID, serial: DEV, name: "Moxie", state: "paired",
                 "pairing-status": "paired", "mqtt-device-id": DEV };
const ROBOT_POST = `POST /local/robots/${DEV}/config`, FLEET_POST = "POST /local/fleet/config";

const site = await serveStatic(STATIC, { extIsHtml: false });
const browser = await puppeteer.launch({
  executablePath: chrome, headless: "new",
  defaultViewport: { width: 1280, height: 1000 },
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const until = async (done, ms) => { for (let t = 0; t < ms && !done(); t += 100) await sleep(100); };

/** A fresh visit, logged in, on the Moxie tab, with this browser set to `phone`'s zone. The
 *  supervisor's layers are `zones` ({house, own, env}); a save moves them as the real one
 *  would, so the redraw after it reads the new state. A robot save is answered `saved`
 *  (#309's flag). Every POST lands in `st.calls`, its parsed body in `st.bodies`.
 *  `mutate` serves a changed js/settings.js. */
async function drive({ mutate = null, phone = DEFAULT, zones = {}, saved = true } = {}) {
  const st = { calls: [], bodies: {}, zones: { house: "", own: "", env: "", ...zones },
               unknown: [] };
  const page = await browser.newPage();
  await page.emulateTimezone(phone);
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  const { errs, aborted } = watchPage(page);
  await page.setRequestInterception(true);
  page.on("request", (r) => {
    if (r.isInterceptResolutionHandled()) return;
    const u = new URL(r.url()), p = u.pathname, m = r.method(), call = `${m} ${p}`;
    const J = (o, status = 200) =>
      r.respond({ status, contentType: "application/json", body: JSON.stringify(o) });
    if (p === SETTINGS_JS && mutate)
      return r.respond({ status: 200, contentType: "text/javascript; charset=utf-8",
                         body: mutate(SRC) });
    if (p === "/local/quicklogin") return J({ token: TOKEN, email: "parent@home.lan" });
    let body = null;
    if (m === "POST") {
      st.calls.push(call);
      try { body = JSON.parse(r.postData() || "{}"); } catch (e) { body = r.postData(); }
      st.bodies[call] = body;
    }
    if (call === ROBOT_POST || call === FLEET_POST) {
      const zone = body && body.timezone_id;
      if (zone === TYPO) {                /* the supervisor's 400, as the console relays it */
        aborted.refused++;
        return J({ ok: false, error: FIX.refusal }, 400);
      }
      if (zone) st.zones[call === FLEET_POST ? "house" : "own"] = zone;
      return J(call === FLEET_POST
        ? { ok: true, scope: "fleet", applied: body, robots: [DEV],
            fleet_config: st.zones.house ? { timezone_id: st.zones.house } : {} }
        : { ok: true, scope: "robot", device_id: DEV, applied: body, saved,
            online: true, pushed: true });
    }
    if (p === "/local/state")
      return J({ user: { id: "u1", email: "parent@home.lan" }, children: [],
                 robots: [RECORD], unclaimed: [], unclaimed_known: true,
                 on_other_accounts: [] });
    if (p === "/local/fleet") {
      const key = `${st.zones.house}|${st.zones.own}|${st.zones.env}`;
      if (!FIX.views[key]) { st.unknown.push(key); return J({ ok: false, error: "no fixture" }); }
      return J(FIX.views[key]);
    }
    /* Every other card fires its own XHR on entry; {ok:false} renders each one's honest
     * "unavailable" branch. */
    if (p.startsWith("/local/") || p.startsWith("/api/"))
      return J({ ok: false, error: "not in this fixture" });
    if (p === "/favicon.ico") aborted.refused++;
    return r.continue();
  });
  await page.goto(site.url + "/", { waitUntil: "domcontentloaded", timeout: 20000 });
  await page.waitForSelector("#btn-login", { timeout: 10000 });
  await page.click("#btn-login");
  await page.waitForFunction(
    "!document.querySelector('#tabs').classList.contains('hidden')", { timeout: 10000 });
  await page.click('.tab[data-tab="moxie"]');
  await page.waitForSelector("#tab-moxie.active", { timeout: 10000 });
  await page.waitForFunction(() => document.querySelector("#robot-live").innerHTML.trim()
                               .length > 0, { timeout: 10000 });
  return { page, st, errs, aborted };
}

/** The settings card's zone field, its hint, the field's own phone-zone button (`here`, null
 *  while hidden), the offer line and the save status. */
const card = (page) => page.evaluate(() => {
  const offer = document.querySelector("#tz-offer");
  const here = document.querySelector("#btn-tz-here");
  return {
    field: document.querySelector("#cfg-tz").value,
    hint: document.querySelector("#cfg-tz-hint").textContent,
    here: !here || here.classList.contains("hidden") ? null : here.textContent,
    offer: offer.classList.contains("hidden") ? null : offer.textContent,
    button: (document.querySelector("#btn-tz-phone") || { textContent: null }).textContent,
    status: document.querySelector("#cfg-status").textContent,
  };
});

/** Open ⚙️ Settings (unless it is open), set the zone field to `zone` (null: leave it), tick
 *  or untick "Apply to all robots", save, and wait for the answer to be written on the card. */
async function save(page, st, { zone = null, fleet = false } = {}) {
  if (!(await page.$eval("#cfg-box", (el) => el.open))) await page.click("#cfg-box > summary");
  if (zone !== null) {
    await page.$eval("#cfg-tz", (el) => { el.value = ""; });
    await page.type("#cfg-tz", zone);
  }
  const ticked = await page.$eval("#cfg-fleet", (el) => el.checked);
  if (ticked !== fleet) await page.click("#cfg-fleet");
  const before = st.calls.length;
  await page.click("#btn-cfg-save");
  await until(() => st.calls.length > before, 6000);
  await page.waitForFunction(() => !/Saving/.test(document.querySelector("#cfg-status")
                                                  .textContent), { timeout: 6000 })
    .catch(() => {});
}

const posted = (st, call) => st.bodies[call] || null;
const clean = (C, tag, { errs, aborted, st }) => {
  C.eq(notable(errs, aborted).length, 0, `${tag}: no page errors — ${notable(errs, aborted).slice(0, 3)}`);
  C.eq(st.unknown.length, 0, `${tag}: every fleet read had a fixture — ${st.unknown}`);
};

const SCENARIOS = {
  async Z1(C, o) {
    const cases = [
      [{ house: BERLIN }, BERLIN, /house rule for every robot/],
      [{ own: NEW_YORK }, NEW_YORK, /robot’s own time zone/],
      [{ env: BERLIN }, BERLIN, /MOXIE_TIMEZONE/],
      [{}, "", new RegExp(`Not set: Moxie uses ${DEFAULT}, the default`)],
    ];
    for (const [zones, want, hint] of cases) {
      const run = await drive({ ...o, zones });
      try {
        const got = await card(run.page);
        C.eq(got.field, want, `Z1 ${JSON.stringify(zones)}: the field shows the zone in force`);
        C.ok(hint.test(got.hint), `Z1 ${JSON.stringify(zones)}: the hint names it — got ${JSON.stringify(got.hint)}`);
        clean(C, "Z1", run);
      } finally { await run.page.close(); }
    }
  },

  async Z2(C, o) {
    const run = await drive({ ...o, zones: { house: BERLIN } });
    try {
      await save(run.page, run.st, { zone: NEW_YORK });
      C.eq(run.st.calls.filter((c) => c === FLEET_POST).length, 0, "Z2: nothing is posted as a house rule");
      C.eq((posted(run.st, ROBOT_POST) || {}).timezone_id, NEW_YORK,
           "Z2: the zone typed is posted for this robot");
      C.ok(/Saved — pushed to Moxie/.test((await card(run.page)).status), "Z2: the card says it was saved");
      clean(C, "Z2", run);
    } finally { await run.page.close(); }
  },

  async Z3(C, o) {
    const run = await drive({ ...o });
    try {
      await save(run.page, run.st, { zone: BERLIN, fleet: true });
      C.eq(run.st.calls.filter((c) => c === ROBOT_POST).length, 0, "Z3: nothing is posted for one robot");
      C.eq((posted(run.st, FLEET_POST) || {}).timezone_id, BERLIN,
           "Z3: the zone is posted as a house rule");
      C.ok(/Saved as house rules/.test((await card(run.page)).status), "Z3: the card says so");
      clean(C, "Z3", run);
    } finally { await run.page.close(); }
  },

  async Z4(C, o) {
    const run = await drive({ ...o, zones: { house: BERLIN } });
    try {
      await save(run.page, run.st);
      const body = posted(run.st, ROBOT_POST);
      C.ok(body && !("timezone_id" in body),
           `Z4: an untouched zone is not sent — got ${JSON.stringify(body)}`);
      clean(C, "Z4", run);
    } finally { await run.page.close(); }
  },

  async Z5(C, o) {
    const run = await drive({ ...o, phone: BERLIN });
    try {
      const before = await card(run.page);
      C.ok(before.offer && before.offer.includes(DEFAULT) && before.offer.includes(BERLIN),
           `Z5: the offer names the default and this browser's zone — got ${JSON.stringify(before.offer)}`);
      C.eq(before.button, `Use ${BERLIN}`, "Z5: one button, for this browser's zone");
      await run.page.click("#btn-tz-phone");
      await until(() => run.st.calls.length > 0, 6000);
      await run.page.waitForFunction(() => /now uses/.test(
        document.querySelector("#tz-offer").textContent), { timeout: 6000 }).catch(() => {});
      /* The line is written as soon as the save answers; the field refills only when the
       * fleet read the save then starts (refreshLive) answers. Wait for that redraw, never
       * race it. */
      await run.page.waitForFunction((zone) => document.querySelector("#cfg-tz").value === zone,
                                     { timeout: 6000 }, BERLIN).catch(() => {});
      C.eq(JSON.stringify(run.st.calls), JSON.stringify([FLEET_POST]),
           "Z5: one click, one house rule");
      C.eq(JSON.stringify(posted(run.st, FLEET_POST)), JSON.stringify({ timezone_id: BERLIN }),
           "Z5: it saves this browser's zone and nothing else");
      const after = await card(run.page);
      C.ok(after.offer && after.offer.includes(`Moxie now uses ${BERLIN}`),
           `Z5: and then says so — got ${JSON.stringify(after.offer)}`);
      C.eq(after.field, BERLIN, "Z5: the field shows the new house rule");
      clean(C, "Z5", run);
    } finally { await run.page.close(); }
  },

  async Z6(C, o) {
    for (const [what, opts] of [["the browser in the default zone", { phone: DEFAULT }],
                                ["a house rule chosen", { phone: "Asia/Tokyo", zones: { house: BERLIN } }],
                                ["the server's MOXIE_TIMEZONE", { phone: "Asia/Tokyo", zones: { env: BERLIN } }]]) {
      const run = await drive({ ...o, ...opts });
      try {
        C.eq((await card(run.page)).offer, null, `Z6: no offer with ${what}`);
        clean(C, "Z6", run);
      } finally { await run.page.close(); }
    }
  },

  async Z7(C, o) {
    const run = await drive({ ...o, saved: false });
    try {
      await save(run.page, run.st, { zone: NEW_YORK });
      const status = (await card(run.page)).status;
      C.ok(/NOT saved/.test(status) && !/✅/.test(status),
           `Z7: an applied but unsaved change says it will be lost — got ${JSON.stringify(status)}`);
      clean(C, "Z7", run);
    } finally { await run.page.close(); }
  },

  async Z8(C, o) {
    const run = await drive({ ...o });
    try {
      await save(run.page, run.st, { zone: TYPO });
      const status = (await card(run.page)).status;
      C.ok(status.includes(FIX.refusal) && status.startsWith("⚠️"),
           `Z8: the server's reason is said on the card — got ${JSON.stringify(status)}`);
      clean(C, "Z8", run);
    } finally { await run.page.close(); }
  },

  async Z9(C, o) {
    /* A house rule (Berlin) is already chosen, so the offer stands aside (Z6); the field's
     * own button still offers this browser's zone (New York). */
    for (const [fleet, call, other] of [[false, ROBOT_POST, FLEET_POST],
                                        [true, FLEET_POST, ROBOT_POST]]) {
      const tag = `Z9 ${fleet ? "house rule" : "this robot"}`;
      const run = await drive({ ...o, phone: NEW_YORK, zones: { house: BERLIN } });
      try {
        const before = await card(run.page);
        C.eq(before.here, `Use this phone’s zone (${NEW_YORK})`,
             `${tag}: the field's button names this browser's zone, a zone chosen or not`);
        C.eq(before.field, BERLIN, `${tag}: the field shows the house rule first`);
        await run.page.click("#cfg-box > summary");
        await run.page.click("#btn-tz-here");
        C.eq((await card(run.page)).field, NEW_YORK, `${tag}: one click puts it in the field`);
        C.eq(run.st.calls.length, 0, `${tag}: the button alone saves nothing`);
        await save(run.page, run.st, { fleet });
        C.eq((posted(run.st, call) || {}).timezone_id, NEW_YORK,
             `${tag}: Save settings posts it ${fleet ? "as a house rule" : "for this robot"}`);
        C.eq(run.st.calls.filter((c) => c === other).length, 0, `${tag}: and nowhere else`);
        clean(C, tag, run);
      } finally { await run.page.close(); }
    }
  },
};

async function run(C, name, o = {}) {
  try { await SCENARIOS[name](C, o); }
  catch (e) { C.ok(false, `${name} threw: ${e.message}`); }
}

/* ---- the honest run ------------------------------------------------------------- */
for (const name of Object.keys(SCENARIOS)) await run({ ok, eq }, name);

/* ---- TEETH: each mutation must redden the scenario that guards it ------------------- */
const once = (from, to) => (s) => {
  ok(s.split(from).length === 2, `teeth: the anchor ${JSON.stringify(from.slice(0, 60))} is in js/settings.js exactly once`);
  return s.replace(from, to);
};
const SENT = "if(zone && zone!==(tz.dataset.was||'')) body.timezone_id=zone;";
const OFFER = "const offer=!chosen && !!phone && phone!==zone;";
const HERE_SHOWN = "b.classList.toggle('hidden', !phone);";
const HERE_FILLS = "b.onclick=()=>{ tz.value=phone; };";
const TEETH = [
  ["the zone never sent", "Z2", once(SENT, "")],
  ["the zone never sent", "Z3", once(SENT, "")],
  ["the field not prefilled", "Z1",
   once("if(tz){ tz.value=z.chosen?z.zone:''; tz.dataset.was=tz.value; }",
        "if(tz){ tz.value=''; tz.dataset.was=tz.value; }")],
  ["the field reading the robot's own layer only", "Z1",
   once("const ov=(r&&(r.config_effective||r.config_overrides))||{};",
        "const ov=(r&&r.config_overrides)||{};")],
  ["an untouched zone sent anyway", "Z4", once(SENT, "if(zone) body.timezone_id=zone;")],
  ["no offer", "Z5", once(OFFER, "const offer=false;")],
  ["the offer saving the default, not this browser's zone", "Z5",
   once("$('#btn-tz-phone').onclick=()=>useZone(phone);", "$('#btn-tz-phone').onclick=()=>useZone(zone);")],
  ["the offer saving for one robot, not as a house rule", "Z5",
   once("api('/local/fleet/config',{method:'POST',auth:false,body:{timezone_id:zone}})",
        "api(`/local/robots/${encodeURIComponent(liveDevice)}/config`,{method:'POST',auth:false,body:{timezone_id:zone}})")],
  ["the offer shown over a chosen zone", "Z6", once(OFFER, "const offer=!!phone && phone!==zone;")],
  ["saved:false ignored", "Z7",
   once("return (r && r.saved===false)", "return (false)")],
  ["a refusal not said", "Z8",
   once("    refreshLive();\n  }catch(e){ s.textContent='⚠️ '+(e.message||'save failed'); }\n}\nfunction renderRobot(",
        "    refreshLive();\n  }catch(e){ s.textContent='⚠️ save failed'; }\n}\nfunction renderRobot(")],
  ["the field's button hidden", "Z9", once(HERE_SHOWN, "b.classList.toggle('hidden', true);")],
  ["the field's button filling in the default, not this browser's zone", "Z9",
   once(HERE_FILLS, "b.onclick=()=>{ tz.value=HOUSE_ZONE_DEFAULT; };")],
];
for (const [what, scenario, mutate] of TEETH) {
  ok(mutate(SRC) !== SRC, `teeth: the "${what}" mutation must actually change js/settings.js`);
  const C = makeChecks();
  await run(C, scenario, { mutate });
  ok(C.fails.length > 0, `teeth: ${scenario} must redden when js/settings.js has "${what}"`);
  console.log(`   teeth "${what}": ${scenario} reddened with ${C.fails.length} failure(s); ` +
              `first: ${C.fails[0]}`);
}
await browser.close();
site.close();
finish(LABEL, { fails, count });
