/* test_console_child_name.mjs — the PARENT CONSOLE and the child's name (server/static,
 * js/core.js), in a real browser. The name the parent types is the name Moxie says; the
 * invariants, each over the intercepted requests and the rendered page:
 *
 *   N1 the Wi-Fi tab RENAMES the account's child: typing a name for an account that has a
 *      child sends one PUT /api/children/{that child} (with the parent's token) and no POST,
 *      so the account keeps one child and the server sends the name to its robot
 *   N2 for an account with no child yet the same field makes the first one (one POST)
 *   N3 the live box on the Moxie tab says "Moxie calls your child" with the name the fleet
 *      view gives for the robot (the server names a robot's child only to the account that
 *      has it, so the fleet read carries the token), and nothing more when the account's
 *      record says the same name
 *   N4 when the account's record says another name, the row says it is not sent yet and why:
 *      the reason the rename's own answer gave (the server's sentence), else a plain one
 *   N5 every fleet read and every connection-monitor read carries the parent's token
 *   N6 no row for a robot that is not on this account, or whose name the view does not give
 *   N7 the pairing placeholder "Moxie Kid" is no name: the row shows what Moxie says and
 *      where to type the name, never "Moxie Kid" and never "not sent yet"
 *
 * No FastAPI: `serveStatic` serves server/static and every `/local/*` and `/api/*` call is
 * answered at the browser. The fleet views come from the REAL `moxie_server.fleet`
 * (dependency-free) in a python3 subprocess, and the placeholder and the reasons are read out
 * of the REAL server/moxie_server/child_profile.py (its constants, parsed with `ast`: the
 * module itself opens the database). TEETH: mutated copies of js/core.js (a new child on every
 * click; no name row; the mismatch never said; the rename's reason dropped; the fleet or the
 * monitor read without the token; the placeholder taken for a name) must each redden the
 * scenario that guards it. A child's name is personal data: only 'Zoë', 'José' and 'Sam'.
 *
 *   node sim/test_console_child_name.mjs
 */
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { requireBrowser, serveStatic, makeChecks, finish, repo, watchPage, notable }
  from "./browser_harness.mjs";

const LABEL = "console child-name test";
const { puppeteer, chrome, skip } = await requireBrowser(LABEL);
const { fails, ok, eq, count } = makeChecks();

const DEV = "d_bench_01", RID = "r-claimed", CID = "c-kid", TOKEN = "t-fixture";
const BEARER = `Bearer ${TOKEN}`;
const STATIC = join(repo, "server", "static");
const CORE_JS = "/js/core.js";
const SRC = readFileSync(join(STATIC, CORE_JS), "utf8");

const PY = `
import ast, json, sys
repo, dev = sys.argv[1:3]
sys.path.insert(0, repo + "/server")
from moxie_server import fleet

def constants(path, names):
    tree = ast.parse(open(repo + path).read())
    return {t.id: ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
            for t in n.targets if isinstance(t, ast.Name) and t.id in names}

def snap(child):
    return {"ok": True, "app": "content", "uptime_s": 5, "allow_unverified_bots": False,
            "robots": [{"device_id": dev, "child": child, "permitted": True, "pending": False,
                        "permit_label": "added to a parent account", "battery_level": 80,
                        "audio_volume": 0.5, "wifi_ssid": "Home", "mode": "awake",
                        "firmware": "v24.10.803", "telemetry_count": 0,
                        "config_overrides": {}, "config_effective": {}}],
            "schedule_modules": [], "recent": []}
print(json.dumps({
    "zoe": fleet.normalize_fleet(snap("Zoë")),
    "friend": fleet.normalize_fleet(snap("friend")),
    "hidden": fleet.normalize_fleet(snap(None)),
    "profile": constants("/server/moxie_server/child_profile.py",
                         ("PLACEHOLDER", "UNREACHABLE", "NO_NAME")),
}))
`;
let FIX;
try {
  FIX = JSON.parse(execFileSync("python3", ["-c", PY, repo, DEV], { encoding: "utf8" }));
} catch (e) {
  skip("python3 could not build the fixtures from server/moxie_server — " + e.message);
}
ok(FIX.zoe.robots[0].child === "Zoë" && FIX.hidden.robots[0].child === null,
   "fixture: the real normalize_fleet carries the robot's child (or null) to the page");
ok(FIX.profile.PLACEHOLDER === "Moxie Kid" && FIX.profile.UNREACHABLE.length > 20,
   `fixture: the placeholder and the reasons were read from child_profile.py — got ${JSON.stringify(FIX.profile)}`);

const RECORD = { id: RID, "embodied-robot-id": RID, serial: DEV, name: "Moxie", state: "paired",
                 "pairing-status": "paired", "mqtt-device-id": DEV, child_id: CID };
const kid = (name) => ({ id: CID, "child-first-name": name });
const WIFI = "POST /local/wifi/payload", PUT = `PUT /api/children/${CID}`;
const POST_CHILD = "POST /api/children";
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
const until = async (done, ms) => { for (let t = 0; t < ms && !done(); t += 100) await sleep(100); };

/** A fresh visit, logged in, on `tab`. The account has `children` and `robots`; the fleet
 *  view names the robot's child `live` when the read carries the token (the real server's
 *  rule), else `null`. A rename (PUT) is answered `put` and saved into `children`. Every
 *  write lands in `st.calls`, with its token and body; every fleet and monitor read's token
 *  in `st.reads`. `mutate` serves a changed js/core.js. */
async function drive({ mutate = null, tab = "moxie", children = [kid("Zoë")],
                       robots = [RECORD], live = "zoe",
                       put = { child_pushed: true, reason: null } } = {}) {
  const st = { calls: [], auth: {}, bodies: {}, reads: [], children: [...children] };
  const page = await browser.newPage();
  await page.evaluateOnNewDocument(() => { try { localStorage.clear(); } catch (e) {} });
  const { errs, aborted } = watchPage(page);
  await page.setRequestInterception(true);
  page.on("request", (r) => {
    if (r.isInterceptResolutionHandled()) return;
    const u = new URL(r.url()), p = u.pathname, m = r.method(), call = `${m} ${p}`;
    const auth = r.headers().authorization || "";
    const J = (o, status = 200) =>
      r.respond({ status, contentType: "application/json", body: JSON.stringify(o) });
    if (p === CORE_JS && mutate)
      return r.respond({ status: 200, contentType: "text/javascript; charset=utf-8",
                         body: mutate(SRC) });
    if (p === "/local/quicklogin") return J({ token: TOKEN, email: "parent@home.lan" });
    if (m !== "GET") { st.calls.push(call); st.auth[call] = auth; st.bodies[call] = r.postData(); }
    if (/\.png$/.test(p)) return r.respond({ status: 200, contentType: "image/png", body: PNG });
    if (call === WIFI) return J({ qr_payload: "WIFI-ONLY", wifi_only: true });
    if (call === PUT) {
      const name = JSON.parse(r.postData()).child["child-first-name"];
      st.children = st.children.map((k) => (k.id === CID ? { ...k, "child-first-name": name } : k));
      return J({ data: { id: CID, type: "children", attributes: { "child-first-name": name } },
                 ...put });
    }
    if (call === POST_CHILD) {
      const attrs = JSON.parse(r.postData()).child;
      st.children.push({ id: "c-new", ...attrs });
      return J({ data: { id: "c-new", type: "children", attributes: attrs } });
    }
    if (p === "/local/state")
      return J({ user: { id: "u1", email: "parent@home.lan",
                         "active-child-id": (st.children[0] || {}).id },
                 children: st.children, robots, unclaimed: [], unclaimed_known: true,
                 on_other_accounts: [] });
    if (p === "/local/fleet") {
      st.reads.push(["fleet", auth]);
      return J(auth === BEARER ? FIX[live] : FIX.hidden);
    }
    if (p === "/local/broker/status") {
      st.reads.push(["monitor", auth]);
      return J({ ok: true, app: "content", robots: [], recent: [] });
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
  await page.click(`.tab[data-tab="${tab}"]`);
  await page.waitForSelector(`#tab-${tab}.active`, { timeout: 10000 });
  if (tab === "moxie")
    await page.waitForFunction(() => document.querySelector("#robot-live").innerHTML.trim()
                                 .length > 0, { timeout: 10000 });
  return { page, st, errs, aborted };
}

/** Type a name in the Wi-Fi tab and make the code; resolves once the code was asked for. */
async function makeCode(page, st, name) {
  await page.type("#child-name", name);
  await page.type("#ssid", "BenchNet");
  await page.click("#btn-qr");
  await until(() => st.calls.includes(WIFI), 8000);
}

/** The live box's name row: its value and its note, or null when there is none. */
const nameRow = (page) => page.evaluate(() => {
  const row = document.querySelector("#robot-live .name-row");
  if (!row) return null;
  return { label: row.querySelector("span").textContent, value: row.querySelector("b").textContent,
           note: (row.querySelector(".name-note") || { textContent: "" }).textContent,
           text: row.textContent };
});

const calls = (st, call) => st.calls.filter((c) => c === call).length;

const SCENARIOS = {
  async N1(C, o) {
    const { page, st, errs, aborted } = await drive({ ...o, tab: "wifi" });
    try {
      await makeCode(page, st, "José");
      C.eq(calls(st, PUT), 1, "N1: one PUT renames the account's child");
      C.eq(calls(st, POST_CHILD), 0, "N1: no second child is made");
      C.eq(st.auth[PUT], BEARER, "N1: the rename carries the parent's token");
      C.eq(st.bodies[PUT], JSON.stringify({ child: { "child-first-name": "José" } }),
           "N1: it sends the name the parent typed");
      C.eq(st.children.length, 1, "N1: the account still has one child");
      C.eq(notable(errs, aborted).length, 0, `N1: no page errors — ${notable(errs, aborted).slice(0, 3)}`);
    } finally { await page.close(); }
  },

  async N2(C, o) {
    const { page, st, errs, aborted } = await drive({ ...o, tab: "wifi", children: [], robots: [] });
    try {
      await makeCode(page, st, "Sam");
      C.eq(calls(st, POST_CHILD), 1, "N2: the first name makes the account's child");
      C.eq(calls(st, PUT), 0, "N2: nothing to rename yet");
      C.eq(notable(errs, aborted).length, 0, `N2: no page errors — ${notable(errs, aborted).slice(0, 3)}`);
    } finally { await page.close(); }
  },

  async N3(C, o) {
    const { page, errs, aborted } = await drive(o);
    try {
      const row = await nameRow(page);
      C.ok(row && /Moxie calls your child/i.test(row.label),
           `N3: the live box has the name row — got ${JSON.stringify(row)}`);
      C.eq(row && row.value, "Zoë", "N3: it names the child the fleet view gives");
      C.ok(row && !/not sent yet/.test(row.text), "N3: nothing more when the record agrees");
      C.eq(notable(errs, aborted).length, 0, `N3: no page errors — ${notable(errs, aborted).slice(0, 3)}`);
    } finally { await page.close(); }
  },

  async N4(C, o) {
    /* The rename could not reach the robot: its answer's reason is the one shown. */
    const reason = FIX.profile.UNREACHABLE;
    const { page, st, errs, aborted } = await drive({
      ...o, tab: "wifi", put: { child_pushed: false, reason } });
    try {
      await makeCode(page, st, "José");
      await page.click('.tab[data-tab="moxie"]');
      await page.waitForFunction(() => !!document.querySelector("#robot-live .name-row"),
                                 { timeout: 8000 }).catch(() => {});
      const row = await nameRow(page);
      C.eq(row && row.value, "Zoë", "N4: the row says what the robot has now");
      C.ok(row && row.note.includes("“José” not sent yet") && row.note.includes(reason),
           `N4: and that the account's name is not sent yet, with the rename's reason — got ${JSON.stringify(row)}`);
      C.eq(notable(errs, aborted).length, 0, `N4: no page errors — ${notable(errs, aborted).slice(0, 3)}`);
    } finally { await page.close(); }
    /* A fresh page, no rename seen: the same mismatch with the plain reason. */
    const fresh = await drive({ ...o, children: [kid("José")] });
    try {
      const row = await nameRow(fresh.page);
      C.ok(row && row.note.includes("“José” not sent yet") && row.note.length > 30,
           `N4: with no rename answer the row still says it, with a plain reason — got ${JSON.stringify(row)}`);
    } finally { await fresh.page.close(); }
  },

  async N5(C, o) {
    const { page, st, errs, aborted } = await drive({ ...o, tab: "direct" });
    try {
      await until(() => st.reads.some(([k]) => k === "monitor"), 6000);
      await page.click('.tab[data-tab="moxie"]');
      await until(() => st.reads.some(([k]) => k === "fleet"), 6000);
      const kinds = new Set(st.reads.map(([k]) => k));
      C.ok(kinds.has("monitor") && kinds.has("fleet"), `N5: both reads happened — got ${[...kinds]}`);
      C.eq(st.reads.filter(([, a]) => a !== BEARER).length, 0,
           `N5: every fleet and monitor read carries the token — got ${JSON.stringify(st.reads)}`);
      C.eq(notable(errs, aborted).length, 0, `N5: no page errors — ${notable(errs, aborted).slice(0, 3)}`);
    } finally { await page.close(); }
  },

  async N6(C, o) {
    for (const [what, opts] of [["a robot not on this account", { robots: [] }],
                                ["a name the view does not give", { live: "hidden" }]]) {
      const { page, errs, aborted } = await drive({ ...o, ...opts });
      try {
        C.eq(await nameRow(page), null, `N6: no name row for ${what}`);
        C.eq(notable(errs, aborted).length, 0, `N6: no page errors — ${notable(errs, aborted).slice(0, 3)}`);
      } finally { await page.close(); }
    }
  },

  async N7(C, o) {
    const { page, errs, aborted } = await drive({ ...o, children: [kid(FIX.profile.PLACEHOLDER)],
                                                  live: "friend" });
    try {
      const row = await nameRow(page);
      C.eq(row && row.value, "friend", "N7: the row shows what Moxie says now");
      C.ok(row && /Wi-Fi tab/.test(row.note) && !/not sent yet/.test(row.text),
           `N7: it says where the name goes, not that a placeholder is unsent — got ${JSON.stringify(row)}`);
      C.ok(row && !row.text.includes(FIX.profile.PLACEHOLDER), "N7: the placeholder is never shown as a name");
      C.eq(notable(errs, aborted).length, 0, `N7: no page errors — ${notable(errs, aborted).slice(0, 3)}`);
    } finally { await page.close(); }
  },
};

async function run(C, name, o = {}) {
  try { await SCENARIOS[name](C, o); }
  catch (e) { C.ok(false, `${name} threw: ${e.message}`); }
}

/* ---- the honest run ------------------------------------------------------------- */
for (const name of Object.keys(SCENARIOS)) await run({ ok, eq }, name);

/* ---- TEETH: each mutation must redden the scenario that guards it ------------------- */
const NO_TOKEN_FLEET = (s) => s.replace("f=await api('/local/fleet'); }",
                                        "f=await api('/local/fleet',{auth:false}); }");
const TEETH = [
  ["a new child on every click", "N1",
   (s) => s.replace("if(name){ await saveChildName(name); }",
                    "if(name){ await api('/api/children',{method:'POST',body:{child:{'child-first-name':name}}}); }")],
  ["no name row", "N3", (s) => s.replace("${nameRow(r)}${rows}", "${rows}")],
  ["the fleet read without the token", "N3", NO_TOKEN_FLEET],
  ["the fleet read without the token", "N5", NO_TOKEN_FLEET],
  ["the monitor read without the token", "N5",
   (s) => s.replace("s=await api('/local/broker/status'); }",
                    "s=await api('/local/broker/status',{auth:false}); }")],
  ["the mismatch never said", "N4", (s) => s.replace("if(want && want!==r.child){", "if(false){")],
  ["the rename's reason dropped", "N4",
   (s) => s.replace("(CHILD_PUSH && CHILD_PUSH.name===want && CHILD_PUSH.reason)", "(null)")],
  ["a row for a robot not on this account", "N6",
   (s) => s.replace("if(!rec || typeof r.child!=='string' || !r.child) return '';",
                    "if(typeof r.child!=='string' || !r.child) return '';")],
  ["the placeholder taken for a name", "N7",
   (s) => s.replace("return n.toLowerCase()===PLACEHOLDER_CHILD.toLowerCase() ? '' : n;", "return n;")],
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
