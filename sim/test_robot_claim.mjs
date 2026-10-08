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
 *   C3 every refusal the real claim route gives (on another account, account already has a
 *      robot, robot left, supervisor down), clicked on either card, leaves the server's own
 *      words on screen: rendered text, so a line inside a card the redraw hid does not count.
 *      The fixture then lists what the real server lists after that change (the robot on
 *      another account, a robot on this account, no robot, nobody could check), and where
 *      the server keeps its last answer for a while (supervisor down) the words also survive
 *      the tab's next redraw
 *   C4 an account that already has a robot is offered no second one, and is told why beside
 *      the waiting robot in Robot access and on its own robot card (unpair it first)
 *   C5 when the supervisor could not be asked (`unclaimed_known: false`) the card says the
 *      robot service cannot be reached, instead of looking as if no robot arrived
 *   C6 a robot that arrives while the tab is open gets its Add to my account without
 *      re-opening the tab (the tab's watch reads /local/state), and the watch asks nothing
 *      while another tab is open or once a robot is added
 *   C7 a watch read that lands while a claim is unanswered never redraws over the claim's
 *      own answer ("Added to your account")
 *   C8 a robot another account has added is offered nowhere, and its row in Robot access
 *      (under Allowed: adding it there let it in) says why
 *   S1 a refused Simulate robot scan is shown in the server's words on its card, never as
 *      an unhandled error
 *   W1 the Wi-Fi tab's code is Wi-Fi ONLY by default: one POST /local/wifi/payload, its
 *      payload shown, no recovery phrase; once the robot is on the broker the tab says to
 *      add it, and claims nothing itself
 *   W2 the pairing-key code (the original app's, for Simulate robot scan) is made only
 *      when its option is ticked
 *   W3 a robot record the account had before the code was made (an earlier Simulate robot
 *      scan) is never reported as "Moxie connected!"; the tab says to unpair it first, and
 *      reports a connection once a new record appears
 *
 * No FastAPI: `serveStatic` serves server/static and every `/local/*` and `/api/*` call is
 * answered at the browser. The fleet views and the unpair answer come out of the REAL server
 * modules (`moxie_server.fleet`, `moxie_server.lifecycle`, both dependency-free) in a python3
 * subprocess, and both Wi-Fi codes from the REAL `tools/pairing/moxie_qr.py`; the refusal
 * sentences are read out of the REAL routes/pairing.py and supervisor.py (their constants,
 * parsed with `ast`: no fastapi needed); the claim answer's keys are the ones
 * sim/tests/test_robot_claim.py pins on the real route, and
 * sim/tests/test_wifi_first_qr.py pins what /local/wifi/payload really answers. TEETH:
 * mutated copies of js/core.js (an automatic claim; no button on the card; no button on the
 * pending row; the answer not rendered; a second robot offered; no reason beside a robot that
 * cannot be added, or beside one on another account; a refusal swallowed; an answer written
 * to a hidden line, or before Robot access is redrawn; the unreachable state never said, or
 * said of a supervisor that answered; no watch on the Moxie tab, or one still asking with a
 * robot card up or from another tab, or one redrawing over a claim's answer; the pairing-key
 * code by default; a claim from the Wi-Fi tab's poll; an earlier record reported as the robot
 * on the bench; a refused scan left unhandled) and of index.html (the claim's status line back
 * inside the box the redraw hides) must each redden the scenario that guards it.
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
const HTML = readFileSync(join(STATIC, "index.html"), "utf8");
/* The Moxie tab's watch interval, read from js/core.js so a change there moves the windows
 * (5 s when it is missing, so a run against a page without the watch still ends). */
const WATCH_FOUND = Number((SRC.match(/const WATCH_MS=(\d+);/) || [])[1]);
const WATCH_MS = WATCH_FOUND || 5000;

const PY = `
import ast, json, sys
repo, dev, rid, cid = sys.argv[1:5]
sys.path.insert(0, repo + "/server")
sys.path.insert(0, repo + "/tools/pairing")
from moxie_server import fleet, lifecycle as L
import moxie_qr

def constants(path, names):
    """Module-level string constants, read without importing the module (it needs fastapi)."""
    tree = ast.parse(open(repo + path).read())
    return {t.id: ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
            for t in n.targets if isinstance(t, ast.Name) and t.id in names}

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
    "empty": fleet.normalize_fleet({**snap(True), "robots": []}),
    "unpair": L.unpair_result(rid, unpaired=True, factory_reset=False,
                              child={"id": cid, "name": "Moxie Kid"}, codes_voided=0,
                              access=L.access_view(dev, revoked=True)),
    "wifi": moxie_qr.encode_wifi_only(wifi),
    "wifi_decoded": {k: v for k, v in moxie_qr.decode_proto(moxie_qr.encode_wifi_only(wifi)).items()
                     if k in ("secret_key", "hide_pair")},
    "keyed": moxie_qr.encode_proto(wifi, bytes(range(32))),
    "reasons": constants("/server/moxie_server/routes/pairing.py",
                         ("ON_ANOTHER_ACCOUNT", "UNKNOWN_ROBOT", "CANNOT_CHECK")),
    "unreachable": constants("/server/moxie_server/supervisor.py", ("UNREACHABLE",)).get("UNREACHABLE"),
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
ok(FIX.empty.ok === true && FIX.empty.robots.length === 0, "fixture: a broker with no robot yet");
ok(WATCH_FOUND > 0 && WATCH_FOUND <= 10000,
   `js/core.js names the Moxie tab's watch interval — got ${WATCH_FOUND}`);
ok(FIX.unpair.unpaired === true, "fixture: the real unpair_result produced an unpair answer");
ok(FIX.wifi_decoded.secret_key === null && FIX.wifi_decoded.hide_pair === true,
   "fixture: the real encode_wifi_only carries no key and the wifi-only flag");
ok(["ON_ANOTHER_ACCOUNT", "UNKNOWN_ROBOT", "CANNOT_CHECK"].every((k) => (FIX.reasons[k] || "").length > 20)
   && typeof FIX.unreachable === "string" && FIX.unreachable.length > 0,
   `fixture: the claim's refusal sentences were read from routes/pairing.py — got ${JSON.stringify(FIX.reasons)}`);

/* What /local/state lists once the claim made the record (test_robot_claim.py pins these
 * attributes on the real route), and what the real claim route answers. */
const CLAIMED = { id: RID, "embodied-robot-id": RID, serial: DEV, name: "Moxie", state: "paired",
                  "pairing-status": "paired", "mqtt-device-id": DEV, child_id: CID };
const SIMULATED = { id: "r-sim", name: "Moxie (simulated)", "pairing-status": "paired", child_id: CID };
const CLAIM_OK = { ok: true, robot_id: RID, device_id: DEV, child_id: CID, created: true,
                   permitted: true, permit_error: null };
const REFUSED = { ok: false, error: "account already has a robot", device_id: DEV,
                  reason: "This account already has a robot (Moxie (simulated)). Unpair the "
                          + "current robot first, then add this one.", robot_id: SIMULATED.id };
/* Each refusal the real claim route gives (its status and body), and what the real server
 * lists once that refusal was given: the change that caused it. `staleReads` is how many
 * /local/state reads after it still get the answer from before the change: the server keeps
 * its last good supervisor read for a while when the supervisor stops answering
 * (routes/pairing.py STATE_GRACE_S), and the tab's watch redraws once it changes. */
const REFUSALS = {
  taken: { status: 409, body: { ok: false, error: "on another account", device_id: DEV,
                                reason: FIX.reasons.ON_ANOTHER_ACCOUNT },
           after: { robots: [], unclaimed: [], elsewhere: [DEV], fleet: "served" } },
  occupied: { status: 409, body: REFUSED,
              after: { robots: [SIMULATED], unclaimed: [DEV], elsewhere: [], fleet: "pending" } },
  left: { status: 404, body: { ok: false, error: "unknown robot", device_id: DEV,
                               reason: FIX.reasons.UNKNOWN_ROBOT },
          after: { robots: [], unclaimed: [], elsewhere: [], fleet: "empty" } },
  down: { status: 503, body: { ok: false, error: FIX.unreachable, device_id: DEV,
                               reason: FIX.reasons.CANNOT_CHECK },
          after: { robots: [], unclaimed: [], elsewhere: [], known: false, fleet: "down",
                   staleReads: 1 } },
};
const CLAIM = `POST /local/robots/${DEV}/claim`, PERMIT = `POST /local/robots/${DEV}/permit`;
const SCAN = "POST /local/simulate-robot-scan";
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
 *  `known: false` is a supervisor that could not be asked (/local/state and /local/fleet);
 *  `arriveLater` keeps the broker empty until the test sets `st.arrived`. Every GET of
 *  /local/state is counted in `st.stateGets`. `holdClaim` answers the claim only when the
 *  test calls `st.releaseClaim()`. `refuse` names a REFUSALS entry: the claim is refused that
 *  way, and from then on the fixture lists what the real server lists after that change.
 *  `elsewhere` lists the robots another account has (`on_other_accounts`) and `fleet` picks
 *  the fleet view by name. `scanRefused` answers Simulate robot scan with the claim's own
 *  409, as the real route does for a robot on another account. `mutate` and `mutateHtml`
 *  serve a changed js/core.js and index.html. */
async function drive({ mutate = null, mutateHtml = null, robots = [], unclaimed = [DEV], refuse = null,
                       tab = "moxie", known = true, arriveLater = false, holdClaim = false,
                       elsewhere = [], fleet = null, scanRefused = false } = {}) {
  const st = { calls: [], auth: [], bodies: {}, claimed: false, unpaired: false,
               arrived: !arriveLater, stateGets: 0, refused: false, staleReads: 0 };
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
    if ((p === "/" || p === "/index.html") && mutateHtml)
      return r.respond({ status: 200, contentType: "text/html; charset=utf-8",
                         body: mutateHtml(HTML) });
    if (p === "/local/quicklogin") return J({ token: TOKEN, email: "parent@home.lan" });
    if (m === "POST" || m === "DELETE") {
      st.calls.push(call); st.auth.push(r.headers().authorization || ""); st.bodies[call] = r.postData();
    }
    if (/\.png$/.test(p)) return r.respond({ status: 200, contentType: "image/png", body: PNG });
    if (call === WIFI) return J({ qr_payload: FIX.wifi, wifi_only: true });
    if (call === KEYED) return J({ qr_payload: FIX.keyed, recovery_phrase: PHRASE,
                                   secret_hash: "h", child_id: CID, public_key: "k" });
    if (p === "/local/state") {
      st.stateGets++;
      /* After a refusal: what the real server lists then, once its kept answer is spent. */
      let after = null;
      if (st.refused) { if (st.staleReads > 0) st.staleReads--; else after = REFUSALS[refuse].after; }
      const mine = after ? after.robots : st.unpaired ? [] : st.claimed ? [CLAIMED] : robots;
      const isKnown = after && "known" in after ? after.known : known;
      const body = { user: { id: "u1", email: "parent@home.lan" },
                     children: [{ id: CID, "child-first-name": "Moxie Kid" }],
                     robots: mine,
                     unclaimed: after ? after.unclaimed
                       : st.claimed || !known || !st.arrived ? [] : unclaimed,
                     unclaimed_known: isKnown,
                     on_other_accounts: after ? after.elsewhere : known ? elsewhere : [] };
      /* holdClaim: the first /local/state read while the claim is unanswered sees the robot
       * already added (the server commits before it answers), and the read after it is held
       * until the test lets it go (st.releaseHeld). */
      if (st.holdNext) { st.holdNext = false; st.releaseHeld = () => J(body); return; }
      if (st.claimHeld && !st.readDuringClaim) { st.readDuringClaim = true; st.holdNext = true; }
      return J(body);
    }
    if (p === "/local/fleet") {
      const named = st.refused ? REFUSALS[refuse].after.fleet : fleet;   // never cached
      return J(named ? FIX[named] : !known ? FIX.down : st.claimed ? FIX.served
               : st.arrived ? FIX.pending : FIX.empty);
    }
    if (call === SCAN && scanRefused) { aborted.refused++; return J(REFUSALS.taken.body, 409); }
    if (call === CLAIM) {
      if (refuse) {
        const { status, body, after } = REFUSALS[refuse];
        aborted.refused++;
        st.refused = true;
        st.staleReads = after.staleReads || 0;
        return J(body, status);
      }
      st.claimed = true;
      if (holdClaim) {
        st.claimHeld = true;
        st.releaseClaim = () => { st.claimHeld = false; return J(CLAIM_OK); };
        return;
      }
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
    rowWhy: texts("#permits-box .claim-why"), cardWhy: text("#claim-why"), cardWhyShown: shown("#claim-why"),
    permitsCard: shown("#permits-card"),
    qrCard: shown("#wifi-qr-card"), recovery: shown("#recovery-box"), phrase: text("#phrase"),
    qrKind: text("#qr-kind"), pairStatus: text("#pair-status"),
    qrPayload: decodeURIComponent((($("#qr-img") || {}).getAttribute
      ? $("#qr-img").getAttribute("src") || "" : "").split("payload=")[1] || ""),
  };
});
const claims = (st) => st.calls.filter((c) => c === CLAIM).length;
/* Is `words` on screen? Rendered text only: a line inside a hidden card does not count. */
const onScreen = (page, words) => page.evaluate((w) => document.body.innerText.includes(w), words);
const waitOnScreen = (page, words, timeout = 5000) => page.waitForFunction(
  (w) => document.body.innerText.includes(w), { timeout }, words).catch(() => {});
/* Each status line a claim's answer can land on: its text, or why it cannot be seen. */
const statusLines = (page) => page.evaluate(() => JSON.stringify(Object.fromEntries(
  ["#claim-status", "#permit-status", "#dev-status"].map((s) => {
    const e = document.querySelector(s);
    const seen = !!e && !e.closest(".hidden") && e.getClientRects().length > 0;
    return [s, e ? (seen ? "" : "(hidden) ") + e.textContent.trim() : "(missing)"];
  }))));
const ELSEWHERE_WHY = "That robot is on another account on this server: unpair it there first.";
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

  async C4(C, o) {
    const { page, st, errs, aborted } = await drive({ ...o, robots: [SIMULATED] });
    try {
      const v = await view(page);
      C.ok(v.card && !v.none, "C4: the account's own robot card is shown");
      C.eq(JSON.stringify(v.permitButtons), JSON.stringify(["Permit"]),
           "C4: the other robot still waits in Robot access");
      C.eq(v.allButtons.length, 0, "C4: no Add to my account while the account has a robot");
      C.eq(JSON.stringify(v.rowWhy),
           JSON.stringify(["This account already has a robot (Moxie (simulated)): unpair it first."]),
           "C4: the pending row says why it offers no Add to my account");
      C.ok(v.cardWhyShown && v.cardWhy.includes(DEV)
           && v.cardWhy.includes("This account already has a robot (Moxie (simulated)): unpair it first."),
           `C4: the robot card names the waiting robot and the same reason — got "${v.cardWhy}"`);
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

  async C6(C, o) {
    const { page, st, errs, aborted } = await drive({ ...o, arriveLater: true });
    const buttons = (sel) => page.waitForFunction((s) => document.querySelectorAll(s).length > 0,
                                                  { timeout: 3 * WATCH_MS }, sel).catch(() => {});
    try {
      let v = await view(page);
      C.ok(v.none && v.allButtons.length === 0, "C6: before the robot arrives there is nothing to add");
      st.arrived = true;                                        // it reaches the broker now
      await buttons("#moxie-none .claim-btn");
      v = await view(page);
      C.eq(JSON.stringify(v.cardButtons), JSON.stringify(["Add to my account"]),
           "C6: a robot that arrives while the tab is open is offered without re-opening it");
      if (!v.cardButtons.length) return;                        // nothing below can run
      await buttons("#permits-box .claim-btn");
      v = await view(page);
      C.eq(JSON.stringify(v.rowButtons), JSON.stringify(["Add to my account"]),
           "C6: and on its pending row in Robot access");
      C.eq(claims(st), 0, "C6: the watch never claims");

      await page.click('.tab[data-tab="wifi"]');
      await sleep(300);
      let asked = st.stateGets;
      await sleep(WATCH_MS + 1500);
      C.eq(st.stateGets - asked, 0, "C6: the watch asks nothing while another tab is open");

      /* Re-opening the tab redraws the card: click the redrawn button, never the old one. */
      await page.evaluate(() => document.querySelectorAll("#moxie-none .claim-btn")
        .forEach((b) => { b.dataset.old = "1"; }));
      await page.click('.tab[data-tab="moxie"]');
      await buttons("#moxie-none .claim-btn:not([data-old])");
      await page.click("#moxie-none .claim-btn");
      await cardShown(page);
      await sleep(300);
      asked = st.stateGets;
      await sleep(WATCH_MS + 1500);
      C.ok((await view(page)).card, "C6: the robot card is up");
      C.eq(st.stateGets - asked, 0, "C6: once a robot is added the watch asks nothing");
      C.eq(notable(errs, aborted).length, 0, `C6: no page errors — ${notable(errs, aborted).slice(0, 3)}`);
    } finally { await page.close(); }
  },

  async C7(C, o) {
    const { page, st, errs, aborted } = await drive({ ...o, holdClaim: true });
    const until = async (done, ms) => { for (let t = 0; t < ms && !done(); t += 100) await sleep(100); };
    try {
      await page.click("#moxie-none .claim-btn");
      await until(() => st.claimHeld, 5000);
      /* A whole watch interval with the claim unanswered: a watch read now finds the robot
       * already added, and the redraw it would start is held (st.releaseHeld). */
      await until(() => st.readDuringClaim, WATCH_MS + 2000);
      await sleep(500);
      if (st.releaseClaim) st.releaseClaim();      // the claim answers and redraws itself
      await sleep(500);
      if (st.releaseHeld) st.releaseHeld();        // then any redraw the watch started lands
      await cardShown(page);
      await sleep(1000);
      const v = await view(page);
      C.eq(claims(st), 1, "C7: one click is one claim");
      C.ok(v.card, "C7: the robot card is up");
      C.ok(/Added to your account/.test(v.devStatus),
           `C7: the claim's answer is not wiped by a redraw from the tab's watch — got "${v.devStatus}"`);
      C.eq(notable(errs, aborted).length, 0, `C7: no page errors — ${notable(errs, aborted).slice(0, 3)}`);
    } finally { await page.close(); }
  },

  async C8(C, o) {
    /* Another account added the robot: the claim permitted it, so it is served (Allowed). */
    const { page, st, errs, aborted } = await drive({ ...o, unclaimed: [], elsewhere: [DEV],
                                                      fleet: "served" });
    try {
      const v = await view(page);
      C.ok(v.none && !v.card, "C8: an account with no robot of its own shows No Moxie paired yet");
      C.eq(v.allButtons.length, 0, "C8: a robot on another account is offered nowhere");
      C.eq(JSON.stringify(v.permitButtons), JSON.stringify(["Revoke"]),
           "C8: it is under Allowed in Robot access (adding it there let it in)");
      C.eq(JSON.stringify(v.rowWhy), JSON.stringify([ELSEWHERE_WHY]),
           "C8: its row says why there is no Add to my account");
      C.eq(claims(st), 0, "C8: nothing is claimed");
      C.eq(notable(errs, aborted).length, 0, `C8: no page errors — ${notable(errs, aborted).slice(0, 3)}`);
    } finally { await page.close(); }
  },

  async S1(C, o) {
    const { page, st, errs, aborted } = await drive({ ...o, tab: "wifi", scanRefused: true });
    const words = REFUSALS.taken.body.reason;
    try {
      await page.type("#ssid", "BenchNet");
      await page.type("#wifipass", "s3cret");
      await page.click("#pairing-key-opt summary");
      await page.click("#pairing-key");
      await page.click("#btn-qr");
      await page.waitForFunction(() => !document.querySelector("#wifi-qr-card").classList.contains("hidden"),
                                 { timeout: 8000 }).catch(() => {});
      await page.click('.tab[data-tab="moxie"]');
      await page.waitForFunction(() => !document.querySelector("#moxie-none").classList.contains("hidden")
        && document.querySelector("#permits-box").innerHTML.trim().length > 0, { timeout: 10000 });
      await page.click("#moxie-none details.dev summary");
      await page.click("#btn-sim");
      await waitOnScreen(page, words);
      await sleep(300);
      C.ok(await onScreen(page, words),
           `S1: a refused scan is shown in the server's words — status lines: ${await statusLines(page)}`);
      C.eq(st.calls.filter((c) => c === SCAN).length, 1, "S1: one click is one scan");
      C.eq(JSON.parse(st.bodies[SCAN] || "{}").device_id, DEV, "S1: naming the one pending robot");
      C.eq(notable(errs, aborted).length, 0,
           `S1: no page errors (a refusal left unhandled is one) — ${notable(errs, aborted).slice(0, 3)}`);
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

  async W3(C, o) {
    const { page, st, errs, aborted } = await drive({ ...o, robots: [SIMULATED], tab: "wifi" });
    try {
      await page.type("#ssid", "BenchNet");
      await page.type("#wifipass", "s3cret");
      await page.click("#btn-qr");
      /* The poll's first answers: the account's earlier record is not the robot on the bench. */
      await page.waitForFunction(() => /already has a robot|connected/.test(
        document.querySelector("#pair-status").textContent), { timeout: 8000 }).catch(() => {});
      let v = await view(page);
      C.ok(!/connected/i.test(v.pairStatus),
           `W3: a record the account already had is not reported as this robot — got "${v.pairStatus}"`);
      C.ok(v.pairStatus.includes("this account already has a robot (Moxie (simulated)): unpair it first"),
           `W3: the tab says why this robot cannot be added yet — got "${v.pairStatus}"`);
      st.claimed = true;                      // the old one unpaired, this robot added elsewhere
      await page.waitForFunction(() => /Moxie connected!/.test(
        document.querySelector("#pair-status").textContent), { timeout: 8000 }).catch(() => {});
      v = await view(page);
      C.ok(/Moxie connected!/.test(v.pairStatus),
           `W3: a robot record that appears after the code is reported — got "${v.pairStatus}"`);
      C.eq(claims(st), 0, "W3: the tab never claims by itself");
      C.eq(notable(errs, aborted).length, 0, `W3: no page errors — ${notable(errs, aborted).slice(0, 3)}`);
    } finally { await page.close(); }
  },
};

/** C3 for one refusal, clicked on No Moxie paired yet (`card`) or in Robot access (`row`). */
function refusalScenario(kind, from) {
  const { body: { reason }, after } = REFUSALS[kind];
  const name = `C3 (${kind}, clicked ${from === "card" ? "on No Moxie paired yet" : "in Robot access"})`;
  return async (C, o) => {
    const { page, st, errs, aborted } = await drive({ ...o, refuse: kind });
    try {
      await page.click(from === "card" ? "#moxie-none .claim-btn" : "#permits-box .claim-btn");
      await waitOnScreen(page, reason);
      await sleep(500);                          // and the redraws that follow have landed
      C.ok(await onScreen(page, reason),
           `${name}: the parent sees the server's words — status lines: ${await statusLines(page)}`);
      if (after.staleReads) {
        /* The server's kept answer runs out; the tab's watch redraws No Moxie paired yet. */
        await page.waitForFunction(() => !document.querySelector("#claim-unknown").classList.contains("hidden"),
                                   { timeout: WATCH_MS + 4000 }).catch(() => {});
        await sleep(500);
        C.ok(await onScreen(page, reason),
             `${name}: and still after the tab's next redraw — status lines: ${await statusLines(page)}`);
      }
      const v = await view(page);
      C.eq(JSON.stringify(st.calls), JSON.stringify([CLAIM]),
           `${name}: one click is one claim, and nothing else is sent`);
      if (kind === "occupied") {
        C.ok(v.card && !v.none, `${name}: the robot added meanwhile has its card`);
        C.ok(v.cardWhyShown && v.cardWhy.includes(DEV), `${name}: which names the robot still waiting`);
      } else {
        C.ok(v.none && !v.card, `${name}: No Moxie paired yet still shows, and no robot card`);
        C.eq(v.allButtons.length, 0, `${name}: the robot is no longer offered`);
      }
      if (kind === "taken")
        C.eq(JSON.stringify(v.rowWhy), JSON.stringify([ELSEWHERE_WHY]),
             `${name}: its row in Robot access says it is on another account`);
      if (kind === "left") C.ok(!v.permitsCard, `${name}: Robot access is gone with the robot`);
      if (kind === "down") C.ok(v.unknown, `${name}: the card says the robot service cannot be reached`);
      C.eq(notable(errs, aborted).length, 0, `${name}: no page errors — ${notable(errs, aborted).slice(0, 3)}`);
    } finally { await page.close(); }
  };
}
for (const kind of Object.keys(REFUSALS))
  for (const from of ["card", "row"]) SCENARIOS[`C3-${kind}-${from}`] = refusalScenario(kind, from);

/** One scenario into collector `C`; an interaction that throws (a control that never
 *  became clickable) is that scenario's failure, not the end of the run. */
async function run(C, name, o = {}) {
  try { await SCENARIOS[name](C, o); }
  catch (e) { C.ok(false, `${name} threw: ${e.message}`); }
}

/* ---- the honest run ------------------------------------------------------------- */
for (const name of Object.keys(SCENARIOS)) await run({ ok, eq }, name);

/* ---- TEETH: each mutation must redden the scenario that guards it ------------------- */
/* The claim's answer on the line of the card the click came from, shown or not. */
const HIDDEN_LINE = (s) => s.replace("return [fromSel, '#claim-status', '#dev-status'].map(s=>$(s))\n"
                                     + "    .find(el=>el && !el.closest('.hidden')) || null;", "return $(fromSel);");
/* index.html with #claim-status back inside #claim-box, where it was. */
const STATUS_LINE = '<div id="claim-status" class="muted" role="status" aria-live="polite"></div>';
const STATUS_IN_BOX = (h) => h.replace(`        ${STATUS_LINE}\n`, "")
  .replace("if it is the one you just showed the codes to.</p>\n        </div>",
           `if it is the one you just showed the codes to.</p>\n          ${STATUS_LINE}\n        </div>`);
const TEETH = [
  ["a claim made without the click", "C1",
   (s) => s.replace("  wireClaims(list, '#claim-status');\n",
                    "  wireClaims(list, '#claim-status');\n  if(ids.length) claimRobot(ids[0], '#claim-status');\n")],
  ["no button on the No Moxie card", "C1",
   (s) => s.replace("<span>${escapeHtml(id)}</span> ${claimButton(id)}</div>", "<span>${escapeHtml(id)}</span></div>")],
  ["the claim's answer never rendered", "C1",
   (s) => s.replace("  try{ await refreshMoxie(); }catch(e){}    // the whole redraw first", "  //")],
  ["no button on the pending row", "C2",
   (s) => s.replace("+ (claimable(r.device_id) ? ' '+claimButton(r.device_id) : '')", "")],
  ["a refusal swallowed", "C3-taken-card",
   (s) => s.replace("catch(e){ refused=oops(e,'could not add it'); }", "catch(e){}")],
  ["an answer written to a hidden line", "C3-occupied-card", HIDDEN_LINE],
  ["an answer written to a hidden line", "C3-left-row", HIDDEN_LINE],
  ["the claim's answer placed before Robot access is redrawn", "C3-left-row",
   (s) => s.replace("  await refreshLive();     // Robot access too", "  refreshLive();     // Robot access too")],
  ["the claim's status line back inside the box a redraw hides", "C3-taken-card", STATUS_IN_BOX, "html"],
  ["a second robot offered", "C4",
   (s) => s.replace("function claimable(deviceId){ return !ACCOUNT.robots.length && ",
                    "function claimable(deviceId){ return ")],
  ["no reason beside a robot that cannot be added", "C4",
   (s) => s.replace("  if(!ACCOUNT.robots.length || !ACCOUNT.unclaimed.includes(deviceId)) return '';\n",
                    "  return '';\n")],
  ["no reason beside a robot on another account", "C8",
   (s) => s.replace("  if(ACCOUNT.elsewhere.includes(deviceId))\n"
                    + "    return 'That robot is on another account on this server: unpair it there first.';\n", "")],
  ["no reason on an Allowed row", "C8",
   (s) => s.replace('data-permit="0">Revoke</button>`\n        + why(r)', 'data-permit="0">Revoke</button>`')],
  ["an earlier record reported as the robot on the bench", "W3",
   (s) => s.replace("if(mine.some(r=>!known.has(r.id))){", "if(mine.length){")],
  ["no watch on the Moxie tab", "C6",
   (s) => s.replace("if(name==='moxie'){ refreshMoxie(); monTimer=setInterval(watchForRobot,WATCH_MS); }",
                    "if(name==='moxie'){ refreshMoxie(); }")],
  ["the watch still asking with a robot card up", "C6",
   (s) => s.replace("document.hidden || $('#moxie-none').classList.contains('hidden')) return;",
                    "document.hidden) return;")],
  ["the watch redrawing over a claim's answer", "C7",
   (s) => s.replace("  if(claiming || document.hidden || ", "  if(document.hidden || ")
           .replace("  if(!claiming && accountKey(", "  if(accountKey(")],
  ["the watch still asking from another tab", "C6",
   (s) => s.replace("  clearInterval(monTimer);\n  if(name==='direct')", "  if(name==='direct')")],
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
  ["a refused scan left unhandled", "S1",
   (s) => s.replace(/  try\{\n    await api\('\/local\/simulate-robot-scan',[\s\S]*?\n    return;\n  \}\n/,
                    "  await api('/local/simulate-robot-scan',\n"
                    + "            {method:'POST',auth:false,body:{qr_payload:LAST.qr_payload, device_id}});\n")],
];
ok(STATUS_IN_BOX(HTML).split(STATUS_LINE).length === 2
   && STATUS_IN_BOX(HTML).indexOf(STATUS_LINE) < STATUS_IN_BOX(HTML).indexOf("<!-- Outside #claim-box"),
   "teeth: the index.html mutation moves the one #claim-status line back inside #claim-box");
for (const [what, scenario, mutate, file = "core"] of TEETH) {
  const [before, label] = file === "html" ? [HTML, "index.html"] : [SRC, "js/core.js"];
  ok(mutate(before) !== before, `teeth: the "${what}" mutation must actually change ${label}`);
  const C = makeChecks();
  await run(C, scenario, file === "html" ? { mutateHtml: mutate } : { mutate });
  ok(C.fails.length > 0, `teeth: ${scenario} must redden when ${label} has "${what}"`);
  console.log(`   teeth "${what}": ${scenario} reddened with ${C.fails.length} failure(s); ` +
              `first: ${C.fails[0]}`);
}
await browser.close();
site.close();
finish(LABEL, { fails, count });
