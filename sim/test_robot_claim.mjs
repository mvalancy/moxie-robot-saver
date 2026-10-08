/* test_robot_claim.mjs — the PARENT CONSOLE's bench-day pairing flow (server/static,
 * js/core.js), in a real browser: a robot that paired by scanning the codes is on the broker
 * with no account record until a parent presses "Add to my account". The invariants, each
 * over the intercepted requests:
 *
 *   C1 the "No Moxie paired yet" card offers exactly one Add to my account for the one
 *      unclaimed robot, sends nothing until it is clicked, and one click is exactly one
 *      POST .../claim (with the parent's token); then the robot card (live state, Settings,
 *      Wake, Unpair) and the memory card render, and Wake and Unpair act on that record
 *   C2 a pending row in Robot access offers the same button beside Permit; it claims (the
 *      server permits as part of the claim), it does not also send Permit
 *   C3 a refusal is shown to the parent in its own words, and no robot card appears
 *   C4 an account that already has a robot is offered no second one
 *   C5 when the supervisor could not be asked (`unclaimed_known: false`) the card says the
 *      robot service cannot be reached, instead of looking as if no robot arrived
 *   W1 the Wi-Fi tab's code is Wi-Fi ONLY by default: one POST /local/wifi/payload, its
 *      payload shown, no recovery phrase; once the robot is on the broker the tab says to
 *      add it, and claims nothing itself
 *   W2 the pairing-key code (the original app's, for Simulate robot scan) is made only
 *      when its option is ticked
 *
 * No FastAPI: `serveStatic` serves server/static and every `/local/*` and `/api/*` call is
 * answered at the browser. The fleet views and the unpair answer come out of the REAL server
 * modules (`moxie_server.fleet`, `moxie_server.lifecycle`, both dependency-free) in a python3
 * subprocess, and both Wi-Fi codes from the REAL `tools/pairing/moxie_qr.py`; the claim
 * answer's keys are the ones sim/tests/test_robot_claim.py pins on the real route, and
 * sim/tests/test_wifi_first_qr.py pins what /local/wifi/payload really answers. TEETH:
 * mutated copies of js/core.js (an automatic claim; no button on the card; no button on the
 * pending row; the answer not rendered; a second robot offered; a refusal swallowed; the
 * pairing-key code by default; a claim from the Wi-Fi tab's poll) must each redden the
 * scenario that guards it.
 *
 *   node sim/test_robot_claim.mjs
 */
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { requireBrowser, serveStatic, makeChecks, finish, repo, watchPage, notable }
  from "./browser_harness.mjs";

const LABEL = "robot-claim test";
const { puppeteer, chrome, skip } = await requireBrowser(LABEL);
const { fails, ok, eq, count } = makeChecks();

const DEV = "d_bench_01", RID = "r-claimed", CID = "c-kid", TOKEN = "t-fixture";
const STATIC = join(repo, "server", "static");
const CORE_JS = "/js/core.js";
const SRC = readFileSync(join(STATIC, CORE_JS), "utf8");

const PY = `
import json, sys
repo, dev, rid, cid = sys.argv[1:5]
sys.path.insert(0, repo + "/server")
sys.path.insert(0, repo + "/tools/pairing")
from moxie_server import fleet, lifecycle as L
import moxie_qr

wifi = moxie_qr.WifiInfo("BenchNet", "s3cret", band=moxie_qr.Band.ONLY_24G)

def snap(pending):
    return {"ok": True, "app": "content", "uptime_s": 5, "allow_unverified_bots": False,
            "robots": [{"device_id": dev, "permitted": not pending, "pending": pending,
                        "permit_label": "" if pending else "added to a parent account",
                        "battery_level": 80, "audio_volume": 0.5, "wifi_ssid": "Home",
                        "mode": "awake", "firmware": "v24.10.803", "telemetry_count": 0,
                        "config_overrides": {}, "config_effective": {}}],
            "schedule_modules": [], "recent": []}
print(json.dumps({
    "pending": fleet.normalize_fleet(snap(True)),
    "served": fleet.normalize_fleet(snap(False)),
    "down": fleet.normalize_fleet({"ok": False, "error": "supervisor not reachable"}),
    "unpair": L.unpair_result(rid, unpaired=True, factory_reset=False,
                              child={"id": cid, "name": "Moxie Kid"}, codes_voided=0,
                              access=L.access_view(dev, revoked=True)),
    "wifi": moxie_qr.encode_wifi_only(wifi),
    "wifi_decoded": {k: v for k, v in moxie_qr.decode_proto(moxie_qr.encode_wifi_only(wifi)).items()
                     if k in ("secret_key", "hide_pair")},
    "keyed": moxie_qr.encode_proto(wifi, bytes(range(32))),
}))
`;
let FIX;
try {
  FIX = JSON.parse(execFileSync("python3", ["-c", PY, repo, DEV, RID, CID], { encoding: "utf8" }));
} catch (e) {
  skip("python3 could not build the fixtures from server/moxie_server — " + e.message);
}
ok(FIX.pending.pending_count === 1 && FIX.pending.robots[0].pending === true,
   "fixture: the real normalize_fleet lists the bench robot as pending");
ok(FIX.served.pending_count === 0 && FIX.served.robots.length === 1,
   "fixture: once claimed it is served");
ok(FIX.down.ok === false && FIX.down.robots.length === 0,
   "fixture: the real normalize_fleet of a supervisor that cannot be asked");
ok(FIX.unpair.unpaired === true, "fixture: the real unpair_result produced an unpair answer");
ok(FIX.wifi_decoded.secret_key === null && FIX.wifi_decoded.hide_pair === true,
   "fixture: the real encode_wifi_only carries no key and the wifi-only flag");

/* What /local/state lists once the claim made the record (test_robot_claim.py pins these
 * attributes on the real route), and what the real claim route answers. */
const CLAIMED = { id: RID, "embodied-robot-id": RID, serial: DEV, name: "Moxie", state: "paired",
                  "pairing-status": "paired", "mqtt-device-id": DEV, child_id: CID };
const SIMULATED = { id: "r-sim", name: "Moxie (simulated)", "pairing-status": "paired", child_id: CID };
const CLAIM_OK = { ok: true, robot_id: RID, device_id: DEV, child_id: CID, created: true,
                   permitted: true, permit_error: null };
const REFUSED = { ok: false, error: "account already has a robot", device_id: DEV,
                  reason: "This account already has a robot (Moxie (simulated)). Unpair the "
                          + "current robot first, then add this one." };
const CLAIM = `POST /local/robots/${DEV}/claim`, PERMIT = `POST /local/robots/${DEV}/permit`;
const WIFI = "POST /local/wifi/payload", KEYED = "POST /local/pairing/prepare";
const PHRASE = "apple banana cherry dune";
/* 1x1 transparent PNG: every QR image the console asks for. */
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
  "base64");

const site = await serveStatic(STATIC, { extIsHtml: false });
const browser = await puppeteer.launch({
  executablePath: chrome, headless: "new",
  defaultViewport: { width: 1280, height: 1000 },
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** A fresh first visit, logged in, on the Moxie tab. Until a claim succeeds the account has
 *  `robots` and the broker one pending robot; every POST and DELETE lands in `st.calls`.
 *  `known: false` is a supervisor that could not be asked (/local/state and /local/fleet). */
async function drive({ mutate = null, robots = [], unclaimed = [DEV], refuse = false, tab = "moxie",
                       known = true } = {}) {
  const st = { calls: [], auth: [], bodies: {}, claimed: false, unpaired: false };
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  const { errs, aborted } = watchPage(page);
  await page.setRequestInterception(true);
  page.on("request", (r) => {
    if (r.isInterceptResolutionHandled()) return;
    const u = new URL(r.url()), p = u.pathname, m = r.method(), call = `${m} ${p}`;
    const J = (o, status = 200) =>
      r.respond({ status, contentType: "application/json", body: JSON.stringify(o) });
    if (p === CORE_JS && mutate)
      return r.respond({ status: 200, contentType: "text/javascript; charset=utf-8",
                         body: mutate(SRC) });
    if (p === "/local/quicklogin") return J({ token: TOKEN, email: "parent@home.lan" });
    if (m === "POST" || m === "DELETE") {
      st.calls.push(call); st.auth.push(r.headers().authorization || ""); st.bodies[call] = r.postData();
    }
    if (/\.png$/.test(p)) return r.respond({ status: 200, contentType: "image/png", body: PNG });
    if (call === WIFI) return J({ qr_payload: FIX.wifi, wifi_only: true });
    if (call === KEYED) return J({ qr_payload: FIX.keyed, recovery_phrase: PHRASE,
                                   secret_hash: "h", child_id: CID, public_key: "k" });
    if (p === "/local/state") {
      const mine = st.unpaired ? [] : st.claimed ? [CLAIMED] : robots;
      return J({ user: { id: "u1", email: "parent@home.lan" },
                 children: [{ id: CID, "child-first-name": "Moxie Kid" }],
                 robots: mine, unclaimed: st.claimed || !known ? [] : unclaimed,
                 unclaimed_known: known });
    }
    if (p === "/local/fleet") return J(!known ? FIX.down : st.claimed ? FIX.served : FIX.pending);
    if (call === CLAIM) {
      if (refuse) { aborted.refused++; return J(REFUSED, 409); }
      st.claimed = true;
      return J(CLAIM_OK);
    }
    if (call === `POST /api/robots/${RID}/wakeup`)
      return J({ ok: true, published: true, error: null, resolved_by: "record",
                 note: "Command sent to Moxie." });
    if (call === `DELETE /api/robots/${RID}`) { st.unpaired = true; return J(FIX.unpair); }
    /* Every other card fires its own XHR on entry; {ok:false} renders each one's honest
     * "unavailable" branch. An unexpected POST also lands here, and in `st.calls`. */
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
  await page.click(`.tab[data-tab="${tab}"]`);
  if (tab !== "moxie") {
    await page.waitForSelector(`#tab-${tab}.active`, { timeout: 10000 });
    return { page, st, errs, aborted };
  }
  /* Wait for the tab's RECORDED state, never a live sample: /local/state decides which card
   * shows, and /local/fleet fills Robot access and the live box. */
  await page.waitForFunction(
    (sel) => !document.querySelector(sel).classList.contains("hidden"), { timeout: 10000 },
    robots.length ? "#moxie-card" : "#moxie-none");
  await page.waitForFunction(() => ["#permits-box", "#robot-live"]
    .every((s) => document.querySelector(s).innerHTML.trim().length > 0), { timeout: 10000 });
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
  return { page, st, errs, aborted };
}

const view = (page) => page.evaluate(() => {
  const $ = (s) => document.querySelector(s);
  const shown = (s) => { const e = $(s); return !!e && !e.closest(".hidden") && e.getClientRects().length > 0; };
  const texts = (s) => [...document.querySelectorAll(s)].map((e) => e.textContent.trim());
  const text = (s) => ($(s) || { textContent: "" }).textContent;   // a missing node reads as empty
  return {
    none: shown("#moxie-none"), card: shown("#moxie-card"), memory: shown("#memory-card"),
    settings: shown("#cfg-box"), wake: shown("#btn-wake"), unpair: shown("#btn-unpair"),
    live: !!$("#robot-live .livegrid"), robot: text("#robot-card"),
    cardButtons: texts("#moxie-none .claim-btn"), rowButtons: texts("#permits-box .claim-btn"),
    allButtons: texts(".claim-btn"), permitButtons: texts("#permits-box .permit-btn"),
    claimStatus: text("#claim-status"), devStatus: text("#dev-status"),
    unknown: shown("#claim-unknown"), unknownText: text("#claim-unknown").replace(/\s+/g, " ").trim(),
    qrCard: shown("#wifi-qr-card"), recovery: shown("#recovery-box"), phrase: text("#phrase"),
    qrKind: text("#qr-kind"), pairStatus: text("#pair-status"),
    qrPayload: decodeURIComponent((($("#qr-img") || {}).getAttribute
      ? $("#qr-img").getAttribute("src") || "" : "").split("payload=")[1] || ""),
  };
});
const claims = (st) => st.calls.filter((c) => c === CLAIM).length;
/* The robot card is unhidden by /local/state; its live grid and Settings follow with the
 * fleet answer, so wait for both before reading. */
const cardShown = (page) => page.waitForFunction(
  () => !document.querySelector("#moxie-card").classList.contains("hidden")
        && !!document.querySelector("#robot-live .livegrid"),
  { timeout: 8000 }).catch(() => {});

/* ---- the scenarios, each into a checks collector `C` ------------------------------ */
const SCENARIOS = {
  async C1(C, o) {
    const { page, st, errs, aborted } = await drive(o);
    try {
      let v = await view(page);
      C.ok(v.none && !v.card, "C1: with no record the tab shows No Moxie paired yet");
      C.eq(JSON.stringify(v.cardButtons), JSON.stringify(["Add to my account"]),
           "C1: the card offers exactly one Add to my account for the one unclaimed robot");
      C.ok(!v.unknown, "C1: a supervisor that answered is not reported unreachable");
      await sleep(1200);
      C.eq(claims(st), 0, "C1: nothing is claimed without the click");
      await page.click("#moxie-none .claim-btn");
      await cardShown(page);
      await page.waitForFunction(() => /Added to your account/.test(
        document.querySelector("#dev-status").textContent), { timeout: 5000 }).catch(() => {});
      v = await view(page);
      C.eq(JSON.stringify(st.calls), JSON.stringify([CLAIM]),
           "C1: one click is exactly one POST .../claim, and nothing else is sent");
      C.eq(st.auth[0], `Bearer ${TOKEN}`, "C1: the claim carries the parent's token");
      C.ok(v.card && !v.none, "C1: after the claim the robot card replaces No Moxie paired yet");
      C.ok(v.memory, "C1: the memory card appears with the robot card");
      C.ok(v.settings && v.wake && v.unpair, "C1: Settings, Wake up and Unpair are on the card");
      C.ok(v.live && /Moxie/.test(v.robot) && v.robot.includes(DEV),
           "C1: the card shows the robot's live state and its record");
      C.ok(/Added to your account/.test(v.devStatus), "C1: the parent is told it was added");
      C.eq(v.allButtons.length, 0, "C1: once added, no Add to my account is offered anywhere");

      await page.click("#btn-wake");
      await page.waitForFunction(() => /Command sent/.test(
        document.querySelector("#dev-status").textContent), { timeout: 5000 }).catch(() => {});
      C.eq(st.calls[1], `POST /api/robots/${RID}/wakeup`, "C1: Wake up acts on the claimed record");
      await page.click("#btn-unpair");
      await page.waitForFunction(() => document.querySelector("#lc-sheet").open, { timeout: 5000 }).catch(() => {});
      await page.type("#lc-confirm", "unpair");
      await page.click("#lc-go");
      await page.waitForFunction(() => !document.querySelector("#lc-done").classList.contains("hidden"),
                                 { timeout: 8000 }).catch(() => {});
      C.eq(st.calls[2], `DELETE /api/robots/${RID}`, "C1: Unpair acts on the claimed record");
      C.eq(notable(errs, aborted).length, 0, `C1: no page errors — ${notable(errs, aborted).slice(0, 3)}`);
    } finally { await page.close(); }
  },

  async C2(C, o) {
    const { page, st, errs, aborted } = await drive(o);
    try {
      const v = await view(page);
      C.eq(JSON.stringify(v.rowButtons), JSON.stringify(["Add to my account"]),
           "C2: the pending row in Robot access offers Add to my account");
      C.eq(JSON.stringify(v.permitButtons), JSON.stringify(["Permit"]), "C2: beside Permit");
      await page.click("#permits-box .claim-btn");
      await cardShown(page);
      await sleep(300);
      C.eq(claims(st), 1, "C2: one click is one claim");
      C.ok(!st.calls.includes(PERMIT), "C2: the claim permits on the server; the page sends no Permit");
      C.ok((await view(page)).card, "C2: the robot card appears");
      C.eq(notable(errs, aborted).length, 0, `C2: no page errors — ${notable(errs, aborted).slice(0, 3)}`);
    } finally { await page.close(); }
  },

  async C3(C, o) {
    const { page, st, errs, aborted } = await drive({ ...o, refuse: true });
    try {
      await page.click("#moxie-none .claim-btn");
      await page.waitForFunction(() => /Unpair the current robot first/.test(
        document.querySelector("#claim-status").textContent), { timeout: 5000 }).catch(() => {});
      await sleep(300);
      const v = await view(page);
      C.eq(claims(st), 1, "C3: one click is one claim, refused or not");
      C.ok(/Unpair the current robot first/.test(v.claimStatus),
           `C3: the refusal is shown in the server's words — got "${v.claimStatus}"`);
      C.ok(v.none && !v.card, "C3: no robot card appears after a refusal");
      C.eq(notable(errs, aborted).length, 0, `C3: no page errors — ${notable(errs, aborted).slice(0, 3)}`);
    } finally { await page.close(); }
  },

  async C4(C, o) {
    const { page, st, errs, aborted } = await drive({ ...o, robots: [SIMULATED] });
    try {
      const v = await view(page);
      C.ok(v.card && !v.none, "C4: the account's own robot card is shown");
      C.eq(JSON.stringify(v.permitButtons), JSON.stringify(["Permit"]),
           "C4: the other robot still waits in Robot access");
      C.eq(v.allButtons.length, 0, "C4: no Add to my account while the account has a robot");
      C.eq(claims(st), 0, "C4: nothing is claimed");
      C.eq(notable(errs, aborted).length, 0, `C4: no page errors — ${notable(errs, aborted).slice(0, 3)}`);
    } finally { await page.close(); }
  },

  async C5(C, o) {
    const { page, st, errs, aborted } = await drive({ ...o, known: false });
    try {
      const v = await view(page);
      C.ok(v.none && !v.card, "C5: with no record the tab shows No Moxie paired yet");
      C.ok(v.unknown && /cannot be reached right now/.test(v.unknownText),
           `C5: the card says the robot service cannot be reached — got "${v.unknownText}"`);
      C.eq(v.allButtons.length, 0, "C5: and offers nothing to add");
      C.eq(claims(st), 0, "C5: nothing is claimed");
      C.eq(notable(errs, aborted).length, 0, `C5: no page errors — ${notable(errs, aborted).slice(0, 3)}`);
    } finally { await page.close(); }
  },

  async W1(C, o) {
    const { page, st, errs, aborted } = await drive({ ...o, tab: "wifi" });
    try {
      await page.type("#ssid", "BenchNet");
      await page.type("#wifipass", "s3cret");
      await page.click("#btn-qr");
      await page.waitForFunction(() => !document.querySelector("#wifi-qr-card").classList.contains("hidden"),
                                 { timeout: 8000 }).catch(() => {});
      const v = await view(page);
      C.eq(JSON.stringify(st.calls), JSON.stringify([WIFI]),
           "W1: by default the Wi-Fi tab asks for the Wi-Fi-only code, and only that");
      C.eq(st.bodies[WIFI], JSON.stringify({ ssid: "BenchNet", password: "s3cret", band: "24g", hidden: false }),
           "W1: it sends the network the parent typed");
      C.eq(v.qrPayload, FIX.wifi, "W1: the QR shown is the Wi-Fi-only payload");
      C.ok(v.qrCard && !v.recovery, "W1: no recovery phrase for a code that pairs nothing");
      C.ok(/only your Wi-Fi name and password/.test(v.qrKind), "W1: the card says what the code carries");
      /* The tab polls /local/state; with the robot on the broker it says what to do next. */
      await page.waitForFunction(() => /Add to my account/.test(
        document.querySelector("#pair-status").textContent), { timeout: 6000 }).catch(() => {});
      C.ok(/reached this server/.test((await view(page)).pairStatus),
           "W1: once the robot is on the broker the tab says to add it");
      C.eq(claims(st), 0, "W1: the tab never claims by itself");
      C.eq(notable(errs, aborted).length, 0, `W1: no page errors — ${notable(errs, aborted).slice(0, 3)}`);
    } finally { await page.close(); }
  },

  async W2(C, o) {
    const { page, st, errs, aborted } = await drive({ ...o, tab: "wifi" });
    try {
      await page.type("#ssid", "BenchNet");
      await page.type("#wifipass", "s3cret");
      await page.click("#pairing-key-opt summary");
      await page.click("#pairing-key");
      await page.click("#btn-qr");
      await page.waitForFunction(() => !document.querySelector("#wifi-qr-card").classList.contains("hidden"),
                                 { timeout: 8000 }).catch(() => {});
      const v = await view(page);
      C.eq(JSON.stringify(st.calls), JSON.stringify([KEYED]),
           "W2: with the option ticked the tab asks for the pairing-key code");
      C.eq(v.qrPayload, FIX.keyed, "W2: the QR shown is the pairing-key payload");
      C.ok(v.recovery && v.phrase === PHRASE, "W2: the recovery phrase is shown with it");
      C.eq(notable(errs, aborted).length, 0, `W2: no page errors — ${notable(errs, aborted).slice(0, 3)}`);
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
  ["a claim made without the click", "C1",
   (s) => s.replace("  wireClaims(list, '#claim-status');\n",
                    "  wireClaims(list, '#claim-status');\n  if(ids.length) claimRobot(ids[0], '#claim-status');\n")],
  ["no button on the No Moxie card", "C1",
   (s) => s.replace("<span>${escapeHtml(id)}</span> ${claimButton(id)}</div>", "<span>${escapeHtml(id)}</span></div>")],
  ["the claim's answer never rendered", "C1",
   (s) => s.replace("  await refreshMoxie();\n  const d=$('#dev-status');", "  const d=$('#dev-status');")],
  ["no button on the pending row", "C2",
   (s) => s.replace("+ (claimable(r.device_id) ? ' '+claimButton(r.device_id) : '')", "")],
  ["a refusal swallowed", "C3",
   (s) => s.replace("catch(e){ if(s) s.textContent=oops(e,'could not add it'); }", "catch(e){}")],
  ["a second robot offered", "C4",
   (s) => s.replace("function claimable(deviceId){ return !ACCOUNT.robots.length && ",
                    "function claimable(deviceId){ return ")],
  ["a supervisor that cannot be asked never said", "C5",
   (s) => s.replace("u.classList.toggle('hidden', ACCOUNT.known);", "u.classList.toggle('hidden', true);")],
  ["a supervisor that answered reported unreachable", "C1",
   (s) => s.replace("u.classList.toggle('hidden', ACCOUNT.known);", "u.classList.toggle('hidden', false);")],
  ["the pairing-key code by default", "W1",
   (s) => s.replace("const withKey=!!($('#pairing-key') && $('#pairing-key').checked);",
                    "const withKey=true;")],
  ["a claim from the Wi-Fi tab's poll", "W1",
   (s) => s.replace("    } else if((st.unclaimed||[]).length){\n",
                    "    } else if((st.unclaimed||[]).length){\n      claimRobot(st.unclaimed[0], '#pair-status');\n")],
];
for (const [what, scenario, mutate] of TEETH) {
  ok(mutate(SRC) !== SRC, `teeth: the "${what}" mutation must actually change js/core.js`);
  const C = makeChecks();
  await run(C, scenario, { mutate });
  ok(C.fails.length > 0, `teeth: ${scenario} must redden when js/core.js has "${what}"`);
  console.log(`   teeth "${what}": ${scenario} reddened with ${C.fails.length} failure(s); ` +
              `first: ${C.fails[0]}`);
}
await browser.close();
site.close();
finish(LABEL, { fails, count });
