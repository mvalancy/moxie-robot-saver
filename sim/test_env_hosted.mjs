// test_env_hosted.mjs — the REAL sim.html wired end to end under a hosted hostname: env =
// hosted fires ZERO :8081/:8082 sidecar probes and ONE same-origin /api/health (A10); each
// mode (offline 404, degraded, live, live-without-transport, busy, malformed) renders its
// badge/marks with a clean console; a loopback load still probes both sidecars. The per-mode
// COPY is pinned in node by sim/tests/edge/mode/04_indicator_lint.mjs; this is the wiring.
//
//   node sim/test_env_hosted.mjs
import { spawn } from "node:child_process";
import { join } from "node:path";
import net from "node:net";
import { requireBrowser, launchBrowser, makeChecks, finish, repo } from "./browser_harness.mjs";

const LABEL = "env-hosted test";
const { puppeteer, chrome, skip } = await requireBrowser(LABEL);

const port = await new Promise((res) => {
  const s = net.createServer(); s.listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => res(p)); });
});
const server = spawn("python3", [join(repo, "sim", "serve.py"), String(port)], { cwd: repo, stdio: "ignore" });
async function waitUp(n = 50) {
  for (let i = 0; i < n; i++) {
    try { const r = await fetch(`http://127.0.0.1:${port}/`, { signal: AbortSignal.timeout(1000) }); if (r.ok) return true; } catch {}
    await new Promise((r) => setTimeout(r, 200));
  }
  return false;
}
function cleanup() { try { server.kill("SIGKILL"); } catch {} }
if (!(await waitUp())) { cleanup(); skip("serve.py did not come up"); }

const { fails, ok, eq, count } = makeChecks();

// Envelope bodies come from the REAL Function, so this cannot drift from the route.
const health = await import(join(repo, "functions", "api", "health.js"));
const envelope = await import(join(repo, "functions", "api", "_lib", "envelope.js"));
const bodyOf = async (env) => (await health.onRequestGet({ env })).text();
const HEALTH_BARE = await bodyOf({});                       // nothing configured
const HEALTH_LIVE = await bodyOf({
  DEMO_GATEWAY_BASE_URL: "https://gw.invalid.test/v1",
  DEMO_GATEWAY_API_KEY: "sk-testonly-abcdefghijklmnop",
  DEMO_CHAT_MODEL: "test-brain-model",
  DEMO_TTS_MODEL: "test-voice-model",
  DEMO_STT_MODEL: "test-ears-model",
});
const HEALTH_BUSY = JSON.stringify(envelope.envelope({
  ok: true, mode: "live", voice: true, ears: true, load: { inflight: 4, capacity: 4 },
}));

// A non-local hostname mapped to the loopback test server — makes env.js see a "hosted" host.
const browser = await launchBrowser(puppeteer, chrome, { hosts: { "moxie.hosted.test": port } });

/** Load sim.html and report what a visitor sees. `health` stubs /api/health at the browser;
 *  `transport` presets window.moxieCloudTransport; `noTransport` 404s cloud-transport.js. */
async function load(url, opts = {}) {
  const page = await browser.newPage();
  const raw = [], sidecar = [], api = [], notFound = [];
  page.on("console", (m) => { if (m.type() === "error") raw.push(m.text()); });
  page.on("pageerror", (e) => raw.push("PAGEERR " + e.message));
  page.on("response", (r) => { if (r.status() === 404) notFound.push(r.url()); });
  const refused = [];
  page.on("requestfailed", (r) => refused.push(r.url()));
  page.on("request", (r) => {
    const u = r.url();
    if (/:808[12]\/health\b/.test(u)) sidecar.push(u);       // the doomed sidecar probes
    if (/\/api\/health\b/.test(u)) api.push(u);              // the same-origin mode probe
  });
  if (opts.transport)
    await page.evaluateOnNewDocument(() => { window.moxieCloudTransport = true; });
  if (opts.health || opts.noTransport) {
    await page.setRequestInterception(true);
    page.on("request", (r) => {
      if (r.isInterceptResolutionHandled()) return;
      if (opts.noTransport && /cloud-transport\.js/.test(r.url()))
        return r.respond({ status: 404, body: "", contentType: "text/plain" });
      if (opts.health && /\/api\/health\b/.test(r.url()))
        return r.respond({ status: opts.health.status, body: opts.health.body,
                           contentType: opts.health.contentType || "application/json" });
      return r.continue();
    });
  }
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 15000 }).catch((e) => raw.push("NAV " + e.message));
  // Wait on the mode verdict, THEN keep a fixed settle: the absence checks (no sidecar
  // probe, no error) need a window, so the condition only ever extends the wait.
  await page.waitForFunction(
    () => !!window.moxieMode && window.moxieMode.stats().transitions.length > 0,
    { timeout: 20000 },
  ).catch(() => {});   // expiring is not a failure HERE — the assertions below say what broke
  await new Promise((r) => setTimeout(r, 3000));  // give the probes (if any) time to fire
  const info = await page.evaluate(() => {
    const q = (sel) => document.querySelector(sel);
    const marked = (id) => {
      const el = document.getElementById(id);
      return !!(el && el.classList.contains("needs-backend"));
    };
    const pill = q(".mode-pill");
    return {
      env: document.body.getAttribute("data-env"),
      mode: document.body.getAttribute("data-mode"),
      badge: (q(".env-badge") || {}).textContent || "",
      badgeColor: q(".env-badge") ? getComputedStyle(q(".env-badge")).color : "",
      pill: pill ? pill.textContent : null,
      pillShown: !!(pill && !pill.hidden),
      banner: (q("#env-banner .eb-text") || {}).textContent || "",
      ttsStatus: (document.getElementById("tts-status") || {}).textContent || "",
      micStatus: (document.getElementById("mic-status") || {}).textContent || "",
      micMarked: marked("mic-btn"),
      busMarked: marked("bus-connect"),
      ttsMarked: marked("tts-test"),
      hasMode: !!window.moxieMode,
      state: window.moxieMode ? window.moxieMode.state() : null,
      scheduled: window.moxieMode ? window.moxieMode.stats().scheduled : null,
    };
  });
  await page.close();
  // Forgive exactly the expected noise: the mode probe's own 404 (and a withheld
  // cloud-transport.js), and one refusal per LOCALHOST sidecar request that really failed.
  const SIDECAR = /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\]):(8081|8082)(\/|$)/;
  const expected404 = (u) => /\/api\/health\b/.test(u) || (opts.noTransport && /cloud-transport\.js/.test(u));
  const onlyProbe404 = notFound.length > 0 && notFound.every(expected404);
  let budget = refused.filter((u) => SIDECAR.test(u)).length;
  const allSidecars = refused.length > 0 && refused.every((u) => SIDECAR.test(u));
  const errs = raw.filter((t) => {
    if (onlyProbe404 && /status of 404/.test(t)) return false;
    if (allSidecars && budget > 0 && /ERR_CONNECTION_REFUSED|Failed to load resource/.test(t)) {
      budget--; return false;
    }
    return true;
  });
  return { errs, raw, notFound, sidecar, api, ...info };
}

const HOSTED = `http://moxie.hosted.test:${port}/sim.html`;

try {
  // --- 1. OFFLINE: no Functions behind this static server, so /api/health 404s — the page
  //        must be byte-identical to the site as it shipped before the mode machine.
  const off = await load(HOSTED);
  eq(off.env, "hosted", "a public hostname reads env=hosted");
  eq(off.sidecar.length, 0, "hosted deploy fires NO :8081/:8082 probes");
  eq(off.hasMode, true, "mode.js is loaded on sim.html");
  eq(off.api.length, 1, "an absent route is probed ONCE");
  // The poll floor is 30 s, so "never again" is read off the timer's own record, not a wait.
  eq(JSON.stringify(off.scheduled), "[]", "…and the next poll was never even ARMED");
  eq(off.state, "offline", "a 404 /api/health reads as offline");
  eq(off.mode, "offline", "body[data-mode] says offline");
  eq(off.badge, "HOSTED DEMO", "offline keeps today's badge exactly");
  eq(off.pillShown, false, "offline shows no pill");
  ok(/only pre.scripted lines have audio/.test(off.ttsStatus), `offline keeps today's TTS wording (got "${off.ttsStatus}")`);
  ok(/scripted child line/.test(off.micStatus), `offline keeps today's mic wording (got "${off.micStatus}")`);
  ok(off.micMarked && off.busMarked && off.ttsMarked, "offline keeps all three needs-backend marks");
  ok(/need a locally/.test(off.banner), `offline keeps today's banner (got "${off.banner}")`);
  eq(off.errs.length, 0, `offline console errors: ${off.errs.slice(0, 3).join(" | ")}`);
  ok(off.notFound.length === 1 && /\/api\/health\b/.test(off.notFound[0]),
     `the ONLY 404 on the page is the mode probe itself (got ${JSON.stringify(off.notFound)})`);
  eq(off.raw.length, 1, "…and it is the only console error either");

  // --- 2. DEGRADED: the route says nothing is configured. ONE request per session (§4.5).
  const deg = await load(HOSTED, { health: { status: 200, body: HEALTH_BARE } });
  eq(deg.state, "degraded", "gateway_not_configured reads as degraded");
  eq(deg.api.length, 1, "not-configured is probed ONCE");
  eq(JSON.stringify(deg.scheduled), "[]", "…and never ARMED a second one");
  eq(deg.badge, "HOSTED DEMO", "degraded/not-configured keeps today's badge");
  ok(deg.micMarked && deg.busMarked, "degraded keeps the mic and link marks");
  eq(deg.notFound.length, 0, `a route that answers produces no 404 at all (got ${JSON.stringify(deg.notFound)})`);
  eq(deg.raw.length, 0, `…and a completely clean console (got ${JSON.stringify(deg.raw.slice(0, 2))})`);

  // --- 3. LIVE with a transport: the mic no longer claims to need a local server.
  const live = await load(HOSTED, { health: { status: 200, body: HEALTH_LIVE }, transport: true });
  eq(live.state, "live", "a configured route reads as live");
  eq(live.mode, "live", "body[data-mode] says live");
  eq(live.badge, "MOXIE ONLINE", "live badge");
  // Most visitors are on a phone: the mic line names the button and says "tap", not "click".
  ok(/Listen/.test(live.micStatus) && !/click/i.test(live.micStatus),
     `live mic line tells a phone visitor what to do (got "${live.micStatus}")`);
  eq(live.micMarked, false, "live ears REMOVE #mic-btn's needs-backend mark");
  eq(live.busMarked, true, "#bus-connect keeps its mark in EVERY mode — a real broker is not here");
  ok(/own voice is live/.test(live.ttsStatus), `live voice wording reaches the page (got "${live.ttsStatus}")`);
  eq(live.sidecar.length, 0, "a live hosted page still fires no sidecar probes");
  // POSITIVE CONTROL for the "never ARMED" checks above.
  ok(Array.isArray(live.scheduled) && live.scheduled.length === 1 && live.scheduled[0] >= 30000,
     `a LIVE page DOES arm its next poll, ~30 s out (scheduled ${JSON.stringify(live.scheduled)})`);
  eq(live.errs.length, 0, `live console errors: ${live.errs.slice(0, 3).join(" | ")}`);

  // --- 3b. LIVE but cloud-transport.js 404s: must not claim LIVE over stub.js answers.
  const noTr = await load(HOSTED, { health: { status: 200, body: HEALTH_LIVE }, noTransport: true });
  eq(noTr.state, "live", "the mode is still live");
  eq(noTr.badge, "HOSTED DEMO · SCRIPTED", "a live mode with no transport reads SCRIPTED");
  ok(noTr.pillShown === true && /no live transport/.test(noTr.pill || ""), `…and says why (got "${noTr.pill}")`);
  // The badge's COLOUR is part of its honesty: online must not share a fallback's amber.
  ok(live.badgeColor !== deg.badgeColor && live.badgeColor !== noTr.badgeColor,
     `online does not share the fallback badges' colour (online ${live.badgeColor}, ` +
     `degraded ${deg.badgeColor}, scripted ${noTr.badgeColor})`);
  eq(noTr.errs.length, 0, `no-transport console errors: ${noTr.errs.slice(0, 3).join(" | ")}`);

  // --- 4. AT CAPACITY (§7).
  const busy = await load(HOSTED, { health: { status: 200, body: HEALTH_BUSY }, transport: true });
  eq(busy.badge, "HOSTED DEMO · BUSY", "4/4 in flight reads BUSY");
  ok(busy.pillShown && /hands full/.test(busy.pill || ""), `…with §7's copy in a visible pill (got "${busy.pill}")`);
  eq(busy.errs.length, 0, `busy console errors: ${busy.errs.slice(0, 3).join(" | ")}`);

  // --- 5. A 200 of HTML must leave the page SAFE: not thrown, not believed.
  const bad = await load(HOSTED, {
    health: { status: 200, body: "<!doctype html><html>not the api</html>", contentType: "text/html" },
  });
  eq(bad.state, "offline", "a 200 of HTML is not believed");
  eq(bad.badge, "HOSTED DEMO", "…and the page stays today's");
  eq(bad.errs.length, 0, `malformed-reply console errors: ${bad.errs.slice(0, 3).join(" | ")}`);

  // --- 6. LOCAL: the sidecar probes still fire (feature detection intact).
  const local = await load(`http://127.0.0.1:${port}/sim.html`);
  eq(local.env, "local", "a loopback host reads env=local");
  eq(local.sidecar.length, 2, "a local load probes both sidecars");
  eq(local.badge, "LOCAL", "local badge stays LOCAL");
  eq(local.errs.length, 0, `local console errors: ${local.errs.slice(0, 3).join(" | ")}`);
  eq(local.api.length, 1, "local probes the same-origin route once too");
  ok(local.notFound.every((u) => /\/api\/health\b/.test(u)),
     `the only 404 locally is the mode probe (got ${JSON.stringify(local.notFound)})`);
} finally {
  await browser.close();
  cleanup();
}

finish(LABEL, { fails, count });
