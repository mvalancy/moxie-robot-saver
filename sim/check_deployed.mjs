/* check_deployed.mjs — drive a real phone-sized browser at a DEPLOYED URL and ask what only
 * the deployed artifact can answer. FREE: every spending route is aborted at the browser.
 *
 *   node sim/check_deployed.mjs                      # the site's own canonical origin
 *   node sim/check_deployed.mjs https://host/sim     # any deployment
 *   MOXIE_DEPLOYED_URL=https://host/sim node sim/check_deployed.mjs
 *   node sim/check_deployed.mjs --selftest           # hermetic; no network at all
 *
 * Why it exists: Cloudflare injects its Web Analytics beacon into the HTML on the way out
 * (custom domain only, and only for a browser's Accept header — `curl` never sees it), and
 * our first CSP refused it in production where no local suite could look. Not a
 * `test_*.mjs` because it needs a finished Pages build and the internet: the fast tier runs
 * `--selftest` (the teeth), `deployed.yml` runs the real thing on a schedule, gating nothing.
 *
 * Clauses: 1 the composer is sized, in the first viewport and hit-testable on a fresh phone
 * load (each way it has failed passes the other two tests); 2 the beacon LOADS and zero
 * `securitypolicyviolation`s fire; 3 every same-origin non-/api asset arrived (/api/health
 * 404ing is how a static origin decides it is offline); 4 each script RAN, by one mark it
 * alone leaves — a file served 200 OK and inert passes clause 3; 5 on the site's own
 * canonical origin, the deployment is LIVE (`data-mode` "live", badge MOXIE ONLINE).
 *
 * What it cannot see: a dead brain. `/api/health` reads configuration only, so a gateway
 * outage still paints MOXIE ONLINE; `check_live_turn.mjs` spends one turn a day on that.
 */
import { writeFileSync, existsSync, mkdtempSync, cpSync, appendFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { requireBrowser, serveStatic, pagesHeaders, makeChecks, finish, web, launchBrowser,
         PHONE, IOS_UA, SPENDING, deployedTarget, recordCspViolations, measureBoxes,
         canonicalOrigin, liveFixture, pcmToneBase64 }
  from "./browser_harness.mjs";

const LABEL = "deployed-composer check";
const argv = process.argv.slice(2);
const SELFTEST = argv.includes("--selftest");
const cliUrl = argv.find((a) => !a.startsWith("-"));

/**
 * Should this deployment carry Cloudflare's injected beacon? Not on loopback/`.test`, not on
 * `*.pages.dev` (injection belongs to the ZONE, measured: no pages.dev host gets it), yes on
 * any other https host. `MOXIE_EXPECT_BEACON=1|0` overrides.
 */
function expectsBeacon(url) {
  const env = process.env.MOXIE_EXPECT_BEACON;
  if (env === "1" || env === "true") return true;
  if (env === "0" || env === "false") return false;
  const u = new URL(url);
  if (u.protocol !== "https:") return false;
  const h = u.hostname.toLowerCase();
  if (/(^|\.)pages\.dev$/.test(h)) return false;
  return true;
}

/**
 * Must this deployment be LIVE (clause 5)? Yes on the site's own canonical origin, the one
 * deployment configured with a gateway, where `degraded` means lost secrets or the kill
 * switch and `offline` means no Functions. No anywhere else: previews and forks are keyless
 * by design. `MOXIE_EXPECT_LIVE=1|0` overrides.
 */
function expectsLive(url, override = process.env.MOXIE_EXPECT_LIVE) {
  if (override === "1" || override === "true") return true;
  if (override === "0" || override === "false") return false;
  const canon = canonicalOrigin();
  try { return !!canon && new URL(url).origin === canon; } catch { return false; }
}

/** Load `url` on a phone and RECORD what a first-time visitor finds; the caller decides what
 *  fails, so `--selftest` demands its reds from the same code path production runs. */
async function probe(browser, url, { settleMs = 2000 } = {}) {
  const page = await browser.newPage();
  await page.setViewport(PHONE);
  await page.setUserAgent(IOS_UA);
  await page.evaluateOnNewDocument(recordCspViolations);

  const net = [];         // every response: url + status
  const failed = [];      // requests the network layer never completed
  const blocked = [];     // the spending routes we refused, so the report can say "none"
  await page.setRequestInterception(true);
  page.on("request", (r) => {
    if (r.isInterceptResolutionHandled()) return;
    if (SPENDING.test(r.url())) { blocked.push(r.url()); return r.abort("blockedbyclient"); }
    return r.continue();
  });
  page.on("response", (r) => net.push({ url: r.url(), status: r.status() }));
  page.on("requestfailed", (r) => {
    if (SPENDING.test(r.url())) return;                       // ours, on purpose
    failed.push({ url: r.url(), why: r.failure()?.errorText || "?" });
  });
  const consoleErrs = [];
  page.on("console", (m) => { if (m.type() === "error") consoleErrs.push(m.text()); });
  page.on("pageerror", (e) => consoleErrs.push("PAGEERR " + e.message));

  const res = await page.goto(url, { waitUntil: "load", timeout: 45000 });
  await page.waitForFunction("!!document.getElementById('speech-input')", { timeout: 15000 })
            .catch(() => {});

  /* Clause 4's WAIT: every mark lands at its own pace on a cold deployment (moxie.js is a
   * deferred module, mode.js answers over the network), so poll rather than sample. 8 s
   * outlasts mode.js's own 6 s probe timeout; an expired wait falls through and the
   * assertions REPORT which mark is missing. The settle stays well under the ~7 s at which
   * she speaks unprompted. */
  await page.waitForFunction(() => {
    const named = document.querySelectorAll('#motors input[type="range"][aria-label]').length;
    const mode = document.body && document.body.getAttribute("data-mode");
    return !!document.querySelector("#app canvas")               // moxie.js built the stage
        && document.querySelectorAll("#motors .motor").length > 0  // moxie.js built the panel
        && named > 0                                             // hud.js named the sliders
        && !!mode && mode !== "boot";                            // mode.js answered
  }, { timeout: 8000, polling: 200 }).catch(() => {});
  await new Promise((r) => setTimeout(r, settleMs));

  const boxes = await page.evaluate(measureBoxes, ["#speech-input", "#speech-btn"]);
  const view = await page.evaluate(() => ({
    scrollY: Math.round(window.scrollY),
    innerW: window.innerWidth, innerH: window.innerHeight,
    title: document.title,
    hasHud: !!document.getElementById("hud"),
    hasDock: !!document.getElementById("chat-dock"),
    railOpen: document.getElementById("rail-toggle")?.getAttribute("aria-expanded") ?? null,
    beaconTags: [...document.querySelectorAll("script[src]")]
      .map((s) => s.src).filter((s) => /cloudflareinsights\.com/.test(s)),
    csp: window.__csp || [],
    /* One mark per script, each produced ONLY by that script (the page ships without it):
     *   stage/motors/faces  moxie.js — the renderer's <canvas>, buildPanel's rows and glyphs
     *   named               hud.js::labelMotors — the aria-label moxie.js leaves off
     *   mode                mode.js's answer, painted by env.js (it paints "boot" with none)
     *   badge               env.js CREATES .env-badge
     * Rejected as markup-identical either way: body[data-bus], #link-label, #alive-toggle. */
    ran: {
      stage: document.querySelectorAll("#app canvas").length,
      motors: document.querySelectorAll("#motors .motor").length,
      faces: document.querySelectorAll("#faces .face-chip").length,
      named: document.querySelectorAll('#motors input[type="range"][aria-label]').length,
      mode: document.body ? document.body.getAttribute("data-mode") : null,
      badge: (document.querySelector("#topbar .env-badge") || {}).textContent || "",
    },
  }));

  /* qr.js does nothing until asked, so its mark is PROVOKED — after the geometry is read.
   * Make is free (local encoders, default kind needs no input). INK needs the alpha term: an
   * untouched canvas is rgba(0,0,0,0), "dark" everywhere. An inert hud.js (the listener) or
   * qr.js (the encoder) both leave it blank; `named` separates the two. */
  const qr = await page.evaluate(async () => {
    const btn = document.getElementById("qr-make");
    const cv = document.getElementById("qr-canvas");
    const st = document.getElementById("qr-status");
    if (!btn || !cv) return { present: false, ink: 0, status: "" };
    btn.click();
    await new Promise((r) => setTimeout(r, 300));
    let ink = 0;
    try {
      const d = cv.getContext("2d").getImageData(0, 0, cv.width, cv.height).data;
      for (let i = 0; i < d.length; i += 4) if (d[i + 3] > 0 && d[i] < 128) ink++;
    } catch (e) {
      return { present: true, ink: 0, status: "readback failed: " + (e && e.message) };
    }
    return { present: true, ink, w: cv.width, h: cv.height,
             status: (st && st.textContent) || "" };
  });

  await page.close();
  return { url, status: res ? res.status() : 0, headers: res ? res.headers() : {},
           net, failed, blocked, consoleErrs, qr, ...view,
           input: boxes["#speech-input"], button: boxes["#speech-btn"] };
}

/* ---- the assertions, over one probe record -------------------------------- */
function assertReachable(c, p, tag, { expectBeacon, expectLive = false }) {
  const { ok, eq } = c;
  const origin = new URL(p.url).origin;

  eq(p.status, 200, `${tag}: HTTP status of ${p.url}`);
  /* Pages answers a missing route 200 with the HTML fallback, so identity needs an anchor —
   * `#hud`, NOT `#chat-dock`: a dockless build is the regression itself, not a wrong page. */
  ok(p.hasHud, `${tag}: #hud exists — is ${p.url} really the SIM page? (title ${JSON.stringify(p.title)})`);
  // "In the first viewport" is a claim about a page nobody has touched or opened the rail on.
  eq(p.scrollY, 0, `${tag}: the page must not have scrolled on its own`);
  ok(p.railOpen === null || p.railOpen === "false",
     `${tag}: the CONTROLS rail must still be closed on load (aria-expanded=${p.railOpen})`);

  for (const [sel, m] of [["#speech-input", p.input], ["#speech-btn", p.button]]) {
    ok(m.found, `${tag}: ${sel} exists`);
    if (!m.found) continue;
    ok(m.sized, `${tag}: ${sel} has a non-zero box — got ${m.w}×${m.h} ` +
                `(display:${m.display} visibility:${m.visibility})`);
    if (!m.sized) continue;
    ok(m.top >= 0 && m.bottom <= p.innerH,
       `${tag}: ${sel} is inside the first viewport — box y ${m.top}…${m.bottom} of ${p.innerH}`);
    ok(m.left >= 0 && m.right <= p.innerW,
       `${tag}: ${sel} is inside the viewport horizontally — x ${m.left}…${m.right} of ${p.innerW}`);
    ok(m.self, `${tag}: a tap at the centre of ${sel} reaches it — elementFromPoint gave ${m.hit}`);
  }

  /* ---- clause 3: every same-origin, non-/api asset arrived (not "zero console errors":
   * a 404 on /api/health is how a Functions-less origin decides it is offline) ---- */
  const asset = (u) => {
    if (!u.startsWith(origin + "/")) return false;         // third parties are not ours
    return !/\/api\//.test(u);                             // /api/* is feature detection
  };
  const badAssets = p.net.filter((r) => asset(r.url) && r.status >= 400)
                         .map((r) => `${r.status} ${r.url}`);
  eq(badAssets.length, 0,
     `${tag}: every page asset loaded — ${badAssets.length} did not: ${JSON.stringify(badAssets)}`);
  const deadAssets = p.failed.filter((f) => asset(f.url)).map((f) => `${f.url} — ${f.why}`);
  eq(deadAssets.length, 0,
     `${tag}: no page asset failed at the network layer — ${JSON.stringify(deadAssets)}`);

  /* ---- clause 4: the scripts RAN, one named mark per file. NOT covered (no cheap effect
   * on an untouched page, or ~7 s away): sw-reset, stub, bridge, audio, life, mic, rail,
   * turnstile, cloud-transport, ambient. ---- */
  const r = p.ran;
  ok(r.stage > 0,
     `${tag}: moxie.js ran — the three.js renderer appended its <canvas> to #app (found ${r.stage})`);
  ok(r.motors > 0 && r.faces > 0,
     `${tag}: moxie.js ran — buildPanel() filled the empty #motors and #faces ` +
     `(${r.motors} motor rows, ${r.faces} expression glyphs)`);
  ok(r.motors > 0 && r.named === r.motors,
     `${tag}: hud.js ran — labelMotors() gave every motor slider its accessible name ` +
     `(${r.named} named of ${r.motors} rows)`);
  ok(!!r.mode && r.mode !== "boot",
     `${tag}: mode.js ran — body[data-mode] left "boot" for this deployment's own answer ` +
     `(got ${JSON.stringify(r.mode)})`);
  ok(!!r.badge,
     `${tag}: env.js ran — it built the environment badge in the topbar (got ${JSON.stringify(r.badge)})`);
  ok(p.qr.present && p.qr.ink > 500,
     `${tag}: qr.js ran — pressing Make drew a real code, opaque ink and not an untouched ` +
     `canvas (${p.qr.ink} ink px on ${p.qr.w}x${p.qr.h})`);
  ok(p.qr.present && /\{/.test(p.qr.status),
     `${tag}: qr.js ran — …and printed the JSON payload it encoded (${JSON.stringify(String(p.qr.status).slice(0, 48))})`);

  /* ---- clause 5: the canonical origin is LIVE. Clause 4 proves mode.js RAN, not what it
   * found: "degraded" (secrets lost, the kill switch) and "offline" (no Functions) are
   * answers too, and all three passed clauses 1-4 (24/24, five fixtures, 2026-10-07). The
   * badge is env.js painting mode.js's verdict, so it also reddens a live origin whose
   * cloud-transport.js never loaded (HOSTED DEMO · SCRIPTED). ---- */
  if (expectLive) {
    eq(r.mode, "live", `${tag}: the canonical origin is LIVE — body[data-mode] is mode.js's verdict ` +
       `from /api/health ("degraded" = secrets lost or the kill switch, "offline" = no Functions)`);
    eq(r.badge, "MOXIE ONLINE", `${tag}: the badge a visitor sees says MOXIE ONLINE ` +
       `(SCRIPTED = live with no cloud-transport.js; BUSY = live at the concurrency ceiling)`);
  }

  /* ---- the beacon reality (clause 2) ---- */
  eq(p.csp.length, 0, `${tag}: ZERO securitypolicyviolation events on load — ` +
     JSON.stringify(p.csp.slice(0, 4)));

  if (expectBeacon) {
    ok(p.beaconTags.length > 0,
       `${tag}: Cloudflare's Web Analytics beacon must be in the served HTML. ` +
       `None found. If this deployment has Web Analytics off, run with ` +
       `MOXIE_EXPECT_BEACON=0 — but then the CSP entry for static.cloudflareinsights.com ` +
       `in sim/web/_headers is guarding nothing and should be revisited.`);
  }
  for (const src of p.beaconTags) {
    const r = p.net.find((x) => x.url === src);
    ok(!!r, `${tag}: the beacon ${src} produced a response at all`);
    if (r) eq(r.status, 200, `${tag}: beacon ${src} loaded`);
    ok(!p.csp.some((v) => /cloudflareinsights/.test(v.blocked || "")),
       `${tag}: the beacon was not refused by the CSP`);
    ok(!p.failed.some((f) => f.url === src),
       `${tag}: the beacon request did not fail — ` +
       JSON.stringify(p.failed.filter((f) => f.url === src)));
  }
}

/** One line per measurement, so a run leaves numbers behind rather than a verdict. */
function report(p, { expectBeacon, expectLive = false }) {
  const box = (m) => m.found
    ? (m.sized ? `${m.w}×${m.h} at y=${m.top}…${m.bottom}  hit=${m.hit}${m.self ? " (self)" : " ⚠ NOT SELF"}`
               : `${m.w}×${m.h}  display:${m.display} visibility:${m.visibility}`)
    : "ABSENT";
  console.log(`\n  ${p.url}`);
  console.log(`    HTTP ${p.status}   viewport ${p.innerW}×${p.innerH}   scrollY ${p.scrollY}` +
              `   rail aria-expanded=${p.railOpen}   #hud ${p.hasHud ? "yes" : "NO"}   #chat-dock ${p.hasDock ? "yes" : "NO"}`);
  console.log(`    #speech-input   ${box(p.input)}`);
  console.log(`    #speech-btn     ${box(p.button)}`);
  console.log(`    beacon          ${p.beaconTags.length ? p.beaconTags.map((s) => s.slice(0, 78)).join(", ") : "(none)"}` +
              `   expected: ${expectBeacon ? "yes" : "no"}`);
  console.log(`    CSP violations  ${p.csp.length}${p.csp.length ? "  " + JSON.stringify(p.csp.slice(0, 3)) : ""}`);
  // Printed as NUMBERS: a mutation that mutates nothing is only caught because a run shows them.
  console.log(`    scripts ran     moxie.js: ${p.ran.stage} stage canvas, ${p.ran.motors} motors, ` +
              `${p.ran.faces} faces   hud.js: ${p.ran.named} named sliders   ` +
              `mode.js: data-mode=${JSON.stringify(p.ran.mode)}   env.js: badge ${JSON.stringify(p.ran.badge)}`);
  console.log(`    live            ${expectLive ? "REQUIRED (the canonical-origin rule)" : "not required here"}` +
              `   data-mode=${JSON.stringify(p.ran.mode)}   badge ${JSON.stringify(p.ran.badge)}`);
  console.log(`    qr.js           ${p.qr.present ? `${p.qr.ink} ink px on ${p.qr.w}x${p.qr.h}` : "NO #qr-make/#qr-canvas"}` +
              `   payload ${JSON.stringify(String(p.qr.status).slice(0, 46))}`);
  console.log(`    spending routes aborted: ${p.blocked.length}   failed requests: ${p.failed.length}` +
              `   console errors: ${p.consoleErrs.length}`);
  if (p.failed.length) for (const f of p.failed.slice(0, 5)) console.log(`      · failed ${f.url} — ${f.why}`);
  if (p.consoleErrs.length) for (const e of p.consoleErrs.slice(0, 5)) console.log(`      · console ${e.slice(0, 140)}`);
}

/* ─────────────────────────── selftest: the teeth ─────────────────────────── *
 * Mutated COPIES of `sim/web` under the real `_headers`, each of which must redden a
 * DIFFERENT clause (assertions that always fail together are one assertion). CSS is
 * APPENDED so it wins the cascade and cannot silently miss a moved selector. C moves a REAL
 * element: `elementFromPoint` never returns a pseudo-element's origin, so a `::after`
 * overlay tripped nothing. D deletes a script (clause 3); E-I serve one script 200 OK and
 * inert (clause 4) — E must fire the MOXIE clause and F the HUD one, since gutting moxie.js
 * also leaves hud.js nothing to name. Served under MAPPED `.test` hosts: on a loopback
 * origin env.js/voice/ probe the :8081/:8082 sidecars and the CSP refuses them.
 */
const GUT = "/* --selftest mutation: this file was served 200 OK and did nothing. */\n";
const gut = (name) => (dir) => writeFileSync(join(dir, name), GUT);

const MUTATIONS = [
  // [name, appended CSS, the clause it MUST fire, a mutate(dir) fn, the file it needs]
  ["A · dock hidden (the pre-#162 0×0 state)",
   `#chat-dock { display: none !important; }`,
   /non-zero box/],
  ["B · dock pushed below the fold (y≈2095 of 844)",
   `#chat-dock { position: absolute !important; top: 2095px !important; left: 0; right: 0; }`,
   /inside the first viewport/],
  ["C · the title bar relocated over the composer (the #env-banner collision)",
   `#topbar { position: fixed !important; top: auto !important; left: 0; right: 0; bottom: 0;
      height: 320px; z-index: 99; pointer-events: auto; }`,
   /reaches it — elementFromPoint/],
  ["D · a shipped script is missing from the build (qr.js 404s)",
   null,
   /every page asset loaded/,
   (dir) => rmSync(join(dir, "qr.js"), { force: true }),
   "qr.js"],
  ["E · moxie.js served 200 OK and INERT (the stage never boots)",
   null, /moxie\.js ran/, gut("moxie.js"), "moxie.js"],
  ["F · hud.js served 200 OK and INERT (no HUD glue ever wires up)",
   null, /hud\.js ran/, gut("hud.js"), "hud.js"],
  ["G · mode.js served 200 OK and INERT (the deployment never decides what it is)",
   null, /mode\.js ran/, gut("mode.js"), "mode.js"],
  ["H · env.js served 200 OK and INERT (no badge, no banner, no needs-backend marks)",
   null, /env\.js ran/, gut("env.js"), "env.js"],
  ["I · qr.js served 200 OK and INERT (Make is wired and encodes nothing)",
   null, /qr\.js ran/, gut("qr.js"), "qr.js"],
];

/* Clause 5's teeth, checked the way the scheduled run checks the canonical origin. Each copy
 * answers /api/health with what the REAL route says (`liveFixture`), so a fixture cannot
 * drift from production. The control must pass every clause. J and K are the "degraded" and
 * "offline" answers that passed clauses 1-4 on 2026-10-07 (lost secrets and the kill switch
 * send the same body); L is the half only the badge sees, mode "live" with no transport. */
const LIVE_CASES = [
  // [name, the /api/health answer (null = the route is absent), the clause it MUST fire, mutate(dir), the file it needs]
  ["live control · the canonical origin, configured", "health", null],
  ["J · degraded on the canonical origin (secrets lost, or the kill switch)", "bareHealth", /is LIVE/],
  ["K · offline on the canonical origin (no Functions: /api/health 404s)", null, /is LIVE/],
  ["L · live, but cloud-transport.js served 200 OK and INERT (the badge reads SCRIPTED)",
   "health", /says MOXIE ONLINE/, gut("cloud-transport.js"), "cloud-transport.js"],
];

/** A `serveStatic` handler answering `GET /api/health` with `body`, as the route would. */
const answerHealth = (body) => (req, res) => {
  if ((req.url || "/").split("?")[0] !== "/api/health") return false;
  res.writeHead(200, { "Content-Type": "application/json; charset=utf-8" });
  res.end(body);
  return true;
};

/* `force: true`: with qr.js already gone (page_teeth_check's `qr-gone`), a throwing delete
 * once crashed setup and scored as "caught". The anchor check in `selftest()` names it. */
function mutatedCopy(css, mutate) {
  const dir = mkdtempSync(join(tmpdir(), "moxie-deployed-"));
  cpSync(web, dir, { recursive: true });
  if (css) appendFileSync(join(dir, "style.css"), "\n/* --selftest mutation */\n" + css + "\n");
  if (mutate) mutate(dir);
  return dir;
}

async function selftest(puppeteer, chrome) {
  const c = makeChecks();
  const headers = pagesHeaders();

  // A mutation whose target file is missing mutates NOTHING — a named check, not a crash.
  for (const [name, , , , anchor] of [...MUTATIONS, ...LIVE_CASES]) {
    if (!anchor) continue;
    c.ok(existsSync(join(web, anchor)),
         `mutation ${name} needs sim/web/${anchor} to exist before it can break it — ` +
         `the file is missing from the tree, so this mutation proves nothing`);
  }

  // Clause 5 keys on the canonical origin: the scheduled run (no URL) must be held to it,
  // a preview must not be, and the override must work both ways.
  const canon = canonicalOrigin();
  const preview = "https://preview.example.com/sim";
  c.ok(!!canon, `sim/web/index.html declares <link rel="canonical"> — clause 5 keys on it`);
  if (canon) {
    c.ok(expectsLive(canon + "/sim", ""), `the scheduled target ${canon}/sim must be held to clause 5`);
    c.ok(!expectsLive(preview, ""), `a preview (${preview}) must NOT be held to clause 5 — previews are keyless`);
    c.ok(!expectsLive(canon + "/sim", "0") && expectsLive(preview, "1"),
         `MOXIE_EXPECT_LIVE=0|1 must override the derivation both ways`);
  }
  const fx = await liveFixture({ eid: "sim-selftest", reply: "hi", tone: pcmToneBase64({ seconds: 0.05 }) });

  // Every server first: Chrome takes its resolver rules at LAUNCH, and one browser serves all.
  const targets = [];
  for (const [i, entry] of [[0, null], ...MUTATIONS.map((m, n) => [n + 1, m])]) {
    const [name, css, wanted, mutate] = entry || ["baseline (the tree as committed)", null, null];
    const site = await serveStatic(mutatedCopy(css, mutate), { headers });
    targets.push({ name, wanted, site, host: `moxie-selftest-${i}.hosted.test`,
                   control: i === 0, expectLive: false });
  }
  for (const [name, answer, wanted, mutate] of LIVE_CASES) {
    const handle = answer ? answerHealth(fx[answer]) : undefined;
    const site = await serveStatic(mutatedCopy(null, mutate), { headers, handle });
    targets.push({ name, wanted, site, host: `moxie-selftest-${targets.length}.hosted.test`,
                   control: !wanted, expectLive: true });
  }
  const browser = await launchBrowser(puppeteer, chrome,
    { hosts: Object.fromEntries(targets.map((t) => [t.host, t.site.port])) });

  try {
    for (const t of targets) {
      const url = `http://${t.host}:${t.site.port}/sim.html`;
      const m = makeChecks();
      const p = await probe(browser, url);
      report(p, { expectBeacon: false, expectLive: t.expectLive });
      assertReachable(m, p, t.control ? "baseline" : "mutant",
                      { expectBeacon: false, expectLive: t.expectLive });
      console.log(`    → ${t.name}\n      fired: ${m.fails.length ? m.fails.map((f) => "· " + f).join("\n      ") : "NOTHING"}`);

      if (t.control) {
        // The control: a baseline that does not pass proves nothing about the mutations.
        c.ok(m.fails.length === 0,
             `the UNMUTATED tree must pass every clause — ${m.fails.length} failure(s): ` +
             JSON.stringify(m.fails));
      } else {
        c.ok(m.fails.length > 0, `mutation ${t.name} must make the check FAIL — it passed`);
        c.ok(m.fails.some((f) => t.wanted.test(f)),
             `mutation ${t.name} must fire the ${t.wanted} clause specifically — ` +
             `got ${JSON.stringify(m.fails)}`);
      }
    }
  } finally {
    try { await browser.close(); } catch {}
    for (const t of targets) t.site.close();
  }
  return c;
}

/* ───────────────────────────────── main ─────────────────────────────────── */
const { puppeteer, chrome } = await requireBrowser(LABEL);

if (SELFTEST) {
  const c = await selftest(puppeteer, chrome);
  finish(LABEL + " (selftest)", c);
}

const target = deployedTarget(cliUrl, LABEL);
const browser = await launchBrowser(puppeteer, chrome);
try {
  const expectBeacon = expectsBeacon(target);
  const expectLive = expectsLive(target);
  const c = makeChecks();
  const p = await probe(browser, target);
  report(p, { expectBeacon, expectLive });
  assertReachable(c, p, "deployed", { expectBeacon, expectLive });
  await browser.close();
  finish(LABEL, c);
} catch (err) {
  try { await browser.close(); } catch {}
  // A network failure against a real deployment is a RESULT, not a skip.
  console.error(`❌ ${LABEL}: ${err && err.stack ? err.stack : err}`);
  process.exit(1);
}
