// test_env_hosted.mjs — the REAL sim.html wired end to end under a hosted hostname: env =
// hosted fires ZERO :8081/:8082 sidecar probes and ONE same-origin /api/health (A10); each
// mode (offline 404, degraded, live, live-without-transport, busy, malformed) renders its
// badge/marks with a clean console; a loopback load still probes both sidecars. The per-mode
// COPY is pinned in node by sim/tests/edge/mode/04_indicator_lint.mjs; this is the wiring.
// 7-9: a deployment WITH a brain that is out says so (napping/resting, never "Run it
// locally", in the banner or the badge's tooltip); a hosted desktop page is a toy (no
// engineering labels, the rail closed and remembered, how to orbit kept) while a local one is
// unchanged; serve.py and the Docker stack's nginx answer /sim like Pages does.
//
//   node sim/test_env_hosted.mjs
import { spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
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
// A configured deployment with its unit budget spent: what the REAL route answers
// (degraded / budget_exhausted / a retry) — a brain that is out, not a brain that is absent.
const limits = await import(join(repo, "functions", "api", "_lib", "limits.js"));
const envlib = await import(join(repo, "functions", "api", "_lib", "env.js"));
const SPENT_ENV = { DEMO_GATEWAY_BASE_URL: "https://gw.invalid.test/v1", DEMO_GATEWAY_API_KEY: "test-only-placeholder",
                    DEMO_CHAT_MODEL: "test-brain-model", DEMO_TTS_MODEL: "test-voice-model",
                    DEMO_STT_MODEL: "test-ears-model" };
limits.__exhaustBudget(envlib.readConfig(SPENT_ENV));
const HEALTH_SPENT = await bodyOf(SPENT_ENV);
limits.__reset();

// A non-local hostname mapped to the loopback test server — makes env.js see a "hosted" host.
const browser = await launchBrowser(puppeteer, chrome, { hosts: { "moxie.hosted.test": port } });

/** Load sim.html and report what a visitor sees. `health` stubs /api/health at the browser;
 *  `transport` presets window.moxieCloudTransport; `noTransport` 404s cloud-transport.js;
 *  `viewport` sizes the page (default 800x600); `init` runs in the page before its scripts;
 *  `after(page)` runs before the readout. */
async function load(url, opts = {}) {
  const page = await browser.newPage();
  if (opts.viewport) await page.setViewport(opts.viewport);
  const raw = [], sidecar = [], api = [], notFound = [];
  page.on("console", (m) => { if (m.type() === "error") raw.push(m.text()); });
  page.on("pageerror", (e) => raw.push("PAGEERR " + e.message));
  page.on("response", (r) => { if (r.status() === 404) notFound.push(r.url()); });
  const refused = [];
  const failedWhy = [];
  page.on("requestfailed", (r) => { refused.push(r.url()); failedWhy.push(r.url() + " " + ((r.failure() || {}).errorText || "")); });
  page.on("request", (r) => {
    const u = r.url();
    if (/:808[12]\/health\b/.test(u)) sidecar.push(u);       // the doomed sidecar probes
    if (/\/api\/health\b/.test(u)) api.push(u);              // the same-origin mode probe
  });
  if (opts.transport)
    await page.evaluateOnNewDocument(() => { window.moxieCloudTransport = true; });
  if (opts.init) await page.evaluateOnNewDocument(opts.init);
  await page.setRequestInterception(true);
  page.on("request", (r) => {
    if (r.isInterceptResolutionHandled()) return;
    // The local sidecars are REFUSED here, whatever listens on this machine's :8081/:8082 —
    // a developer's own Piper (or any service on that port) would otherwise change the page
    // under test. The request is still made and recorded, so the probe count is real.
    if (/^https?:\/\/(127\.0\.0\.1|localhost|\[::1\]):(8081|8082)\//.test(r.url()))
      return r.abort("connectionrefused");
    if (opts.noTransport && /cloud-transport\.js/.test(r.url()))
      return r.respond({ status: 404, body: "", contentType: "text/plain" });
    if (opts.health && /\/api\/health\b/.test(r.url()))
      return r.respond({ status: opts.health.status, body: opts.health.body,
                         contentType: opts.health.contentType || "application/json" });
    return r.continue();
  });
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 15000 }).catch((e) => raw.push("NAV " + e.message));
  // Wait on the mode verdict, THEN keep a fixed settle: the absence checks (no sidecar
  // probe, no error) need a window, so the condition only ever extends the wait.
  await page.waitForFunction(
    () => !!window.moxieMode && window.moxieMode.stats().transitions.length > 0,
    { timeout: 20000 },
  ).catch(() => {});   // expiring is not a failure HERE — the assertions below say what broke
  await new Promise((r) => setTimeout(r, 3000));  // give the probes (if any) time to fire
  if (opts.after) await opts.after(page);
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
      badgeTitle: (q(".env-badge") || {}).title || "",
      badgeColor: q(".env-badge") ? getComputedStyle(q(".env-badge")).color : "",
      pill: pill ? pill.textContent : null,
      pillShown: !!(pill && !pill.hidden),
      banner: (q("#env-banner .eb-text") || {}).textContent || "",
      // "Run it locally": present AND not hidden (paintBanner hides it for a napping brain).
      bannerLink: !!q("#env-banner .eb-link") && !q("#env-banner .eb-link").hidden &&
                  getComputedStyle(q("#env-banner .eb-link")).display !== "none",
      // What a visitor can SEE (innerText skips display:none), not what the markup holds.
      seen: ["SOFTWARE-IN-THE-LOOP", "v24.10.803", "LINK IDLE", "TTS OUT", "window.moxie"]
        .filter((t) => document.body.innerText.includes(t)),
      // How a visitor turns her around (the scene hint, desktop widths only).
      orbitHint: /DRAG\s*·\s*ORBIT/.test(document.body.innerText) && /SCROLL\s*·\s*ZOOM/.test(document.body.innerText),
      bubbleLabel: (q("#bubble .callout-label") || {}).innerText || "",
      rail: (document.getElementById("rail-toggle") || { getAttribute: () => null }).getAttribute("aria-expanded"),
      railClosed: document.getElementById("hud").classList.contains("rail-closed"),
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
  // The mode probe's 404 is never read, so its body is cut at mode.js's 6 s probe timeout
  // (net::ERR_ABORTED): expected noise like its 404, not a reason to stop forgiving.
  const allSidecars = refused.length > 0 && refused.every((u) => SIDECAR.test(u) || expected404(u));
  const errs = raw.filter((t) => {
    if (onlyProbe404 && /status of 404/.test(t)) return false;
    if (allSidecars && budget > 0 && /ERR_CONNECTION_REFUSED|Failed to load resource/.test(t)) {
      budget--; return false;
    }
    return true;
  });
  return { errs, raw, notFound, sidecar, api, failed: failedWhy, ...info };
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
  // The site's own ears stop by themselves after a breath of silence (mic.js), so the line
  // must not teach a second tap: it re-opened the mic and uploaded a second clip.
  ok(/^Tap Listen and talk — I'll know when you're done\.?$/.test(live.micStatus),
     `live mic line: no second tap to teach — her ears know when you are done (got "${live.micStatus}")`);
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

  // --- 7. A BRAIN THAT IS OUT, NOT ABSENT. "Need a locally-run backend" + "Run it locally"
  //        is true only of a deployment with no brain (1, 2 above keep it); a configured
  //        one whose brain is out must say so, and how long, without that advice.
  const spent = await load(HOSTED, { health: { status: 200, body: HEALTH_SPENT }, transport: true });
  eq(`${spent.state}:${JSON.parse(HEALTH_SPENT).reason}`, "degraded:budget_exhausted",
     "the real route, budget spent, reads degraded/budget_exhausted");
  // ONE SOURCE (W4-S6): the pill and the banner say the same sentence, and it says WHEN —
  // the real budget here is the hour's, so minutes (it read "today's demo budget" in the pill
  // and "try again later" in the banner).
  eq(spent.banner, spent.pill, "budget spent: the banner says exactly what the pill says");
  ok(/recorded lines/.test(spent.banner) && /back in about (a minute|\d+ minutes)\b/.test(spent.banner) &&
     !/today/.test(spent.banner),
     `budget spent: …that she is on her recorded lines and back in about N minutes (got "${spent.banner}")`);
  ok(!/locally/i.test(spent.banner) && !spent.bannerLink,
     `budget spent: no "locally-run backend" and no "Run it locally" (got "${spent.banner}", link ${spent.bannerLink})`);
  ok(/I'll know when you're done/.test(spent.micStatus),
     `budget spent: her ears are still configured, so the mic line is unchanged (got "${spent.micStatus}")`);
  eq(spent.errs.length, 0, `budget-spent console errors: ${spent.errs.slice(0, 3).join(" | ")}`);
  // Mid-conversation the transport reports a failed brain exactly like this (cloud-transport.js
  // hands mode.js the route's reason); a minute later the next poll brings her back.
  const nap = await load(HOSTED, { health: { status: 200, body: HEALTH_LIVE }, transport: true,
    after: (page) => page.evaluate(() => window.moxieMode.note({ reason: "upstream_down", retry_after_s: 0 })) });
  eq(nap.state, "degraded", "a brain that failed a turn reads degraded");
  ok(/brain is napping/.test(nap.banner) && /try again in a minute/.test(nap.banner),
     `brain down: the banner says she is napping, try again in a minute (got "${nap.banner}")`);
  ok(!/locally/i.test(nap.banner) && !nap.bannerLink,
     `brain down: no "locally-run backend" and no "Run it locally" (got "${nap.banner}", link ${nap.bannerLink})`);
  ok(/need a locally/.test(deg.banner) && deg.bannerLink,
     `CONTROL: a deployment with NO brain keeps the honest advice and its link (got "${deg.banner}")`);
  // The badge's tooltip tells the same story: "no backend … need a locally-run server" only
  // where there is no brain.
  for (const [what, r] of [["budget spent", spent], ["brain down", nap]])
    ok(/not answering right now/.test(r.badgeTitle) && !/no backend|locally/i.test(r.badgeTitle),
       `${what}: the badge's tooltip says her brain is not answering, not "no backend" (got "${r.badgeTitle}")`);
  ok(/no backend/.test(deg.badgeTitle), `CONTROL: with NO brain the tooltip still says so (got "${deg.badgeTitle}")`);
  /* Out for longer than a minute, or for a reason this page does not know: the copy that
   * promises less. Cloudflare Access gating is the owner's to fix (a turn reports it), and a
   * reason newer than this page is nulled by mode.js — neither is "back in a minute". */
  const gated = await load(HOSTED, { health: { status: 200, body: HEALTH_LIVE }, transport: true,
    after: (page) => page.evaluate(() => window.moxieMode.note({ reason: "gateway_unreachable_or_gated", retry_after_s: 0 })) });
  const novel = await load(HOSTED, { transport: true, health: { status: 200, body: JSON.stringify({
    ok: true, degraded: true, reason: "a_reason_from_a_newer_server", retry_after_s: 0, mode: "degraded", voice: true, ears: true }) } });
  for (const [what, r] of [["Access-gated", gated], ["unknown reason", novel]]) {
    eq(r.state, "degraded", `${what}: precondition — the page reads degraded`);
    ok(/brain is resting/.test(r.banner) && /try again later/.test(r.banner) && !/in a minute/.test(r.banner),
       `${what}: the banner says she is resting, try LATER — not "in a minute" (got "${r.banner}")`);
    ok(!/locally/i.test(r.banner) && !r.bannerLink, `${what}: …and gives no "Run it locally" advice (link ${r.bannerLink})`);
    eq(r.errs.length, 0, `${what} console errors: ${r.errs.slice(0, 3).join(" | ")}`);
  }
  /* The mic line follows the capture mic.js really uses. An explicit `moxie.sttBase` always
   * wins (mic.js::sttTarget) and records with MediaRecorder — no silence stop — so there the
   * second tap is still how a line is sent, even on a deployment with its own ears. */
  const viaBase = await load(HOSTED, { health: { status: 200, body: HEALTH_LIVE }, transport: true,
    init: () => { try { localStorage.setItem("moxie.sttBase", "http://127.0.0.1:8082"); } catch (e) {} },
    after: (page) => page.evaluate(() => { try { localStorage.removeItem("moxie.sttBase"); } catch (e) {} }) });
  ok(/tap it again to send/.test(viaBase.micStatus),
     `moxie.sttBase set: the mic line keeps the second tap its MediaRecorder capture needs (got "${viaBase.micStatus}")`);
  eq(viaBase.errs.length, 0, `sttBase console errors: ${viaBase.errs.slice(0, 3).join(" | ")}`);

  // --- 8. A HOSTED DESKTOP PAGE IS A TOY; A LOCAL ONE IS THE BENCH IT WAS.
  const DESK = { width: 1440, height: 900 };
  const toy = await load(HOSTED, { health: { status: 200, body: HEALTH_LIVE }, transport: true, viewport: DESK });
  eq(JSON.stringify(toy.seen), "[]",
     `hosted desktop: no engineering label is VISIBLE (version, SIL, LINK, TTS OUT, window.moxie) — saw ${JSON.stringify(toy.seen)}`);
  eq(toy.bubbleLabel, "MOXIE", "hosted desktop: her speech bubble is labelled just MOXIE");
  eq(toy.orbitHint, true, "hosted desktop: …but how to turn her around (DRAG · ORBIT // SCROLL · ZOOM) stays");
  ok(toy.rail === "false" && toy.railClosed,
     `hosted desktop: the servo rail starts CLOSED and says so (aria-expanded=${toy.rail})`);
  eq(toy.errs.length, 0, `hosted desktop console errors: ${toy.errs.slice(0, 3).join(" | ")}`);
  const bench = await load(`http://127.0.0.1:${port}/sim.html`, { viewport: DESK });
  eq(JSON.stringify(bench.seen), JSON.stringify(["SOFTWARE-IN-THE-LOOP", "v24.10.803", "LINK IDLE", "TTS OUT", "window.moxie"]),
     "local desktop: UNCHANGED — every engineering label is still there");
  eq(bench.bubbleLabel, "MOXIE · TTS OUT", "local desktop: the bubble label is unchanged");
  eq(bench.orbitHint, true, "local desktop: the scene hint is unchanged too");
  ok(bench.rail === "true" && !bench.railClosed, `local desktop: the rail is still open (aria-expanded=${bench.rail})`);
  eq(bench.errs.length, 0, `local desktop console errors: ${bench.errs.slice(0, 3).join(" | ")} ` +
     `(failed requests: ${JSON.stringify(bench.failed)})`);

  // …and the hosted visitor's own choice is remembered for the next visit, both ways.
  const railAcross = async () => {
    const page = await browser.newPage();
    await page.setViewport(DESK);
    await page.goto(HOSTED, { waitUntil: "domcontentloaded" });   // env.js + rail.js have run
    const first = await page.evaluate(() => document.getElementById("rail-toggle").getAttribute("aria-expanded"));
    await page.click("#rail-toggle");
    const clicked = await page.evaluate(() => document.getElementById("rail-toggle").getAttribute("aria-expanded"));
    await page.reload({ waitUntil: "domcontentloaded" });
    const next = await page.evaluate(() => document.getElementById("rail-toggle").getAttribute("aria-expanded"));
    await page.close();
    return { first, clicked, next };
  };
  const opened = await railAcross();
  eq(JSON.stringify(opened), '{"first":"false","clicked":"true","next":"true"}',
     "hosted desktop: a visitor who OPENS the rail finds it open on their next visit");
  const shut = await railAcross();
  eq(JSON.stringify(shut), '{"first":"true","clicked":"false","next":"false"}',
     "hosted desktop: …and one who closes it again finds it closed");

  // --- 9. sim/serve.py answers /sim as Cloudflare Pages does: the hub links there directly.
  const pretty = await fetch(`http://127.0.0.1:${port}/sim`).then(async (r) => ({ status: r.status, text: await r.text() }));
  ok(pretty.status === 200 && /id="chat-dock"/.test(pretty.text),
     `serve.py serves /sim as the sim page, like Pages (got ${pretty.status})`);
  /* …and so does the documented Docker stack. Its web service is stock nginx:alpine, which
   * answered /sim with a 404 (reviewed on #308), so it mounts sim/nginx.conf over the image's
   * default server. Read from the files (no Docker here): the mount, the root it serves, the
   * rule, and a page behind every extensionless link on the hub. */
  const text = (p) => { try { return readFileSync(p, "utf8"); } catch { return ""; } };
  const compose = text(join(repo, "sim", "docker-compose.yml")), conf = text(join(repo, "sim", "nginx.conf"));
  const svc = (compose.match(/\n  web:[^\n]*\n((?: {4,}[^\n]*\n|\s*\n)+)/) || [])[1] || "";
  ok(/\.\/web:\/usr\/share\/nginx\/html\b/.test(svc) &&
     /\.\/nginx\.conf:\/etc\/nginx\/conf\.d\/default\.conf\b/.test(svc),
     `docker compose (sim/): the web service mounts sim/web AND sim/nginx.conf over nginx's default server (${JSON.stringify(svc.trim().slice(0, 200))})`);
  ok(/^\s*root\s+\/usr\/share\/nginx\/html;/m.test(conf),
     "…sim/nginx.conf serves that mounted sim/web…");
  ok(/^\s*try_files\s+\$uri\s+\$uri\.html\s[^;]*=404;/m.test(conf),
     "…and answers an extensionless path with its .html page (try_files $uri $uri.html …), as Pages does");
  const bare = [...text(join(repo, "sim", "web", "index.html")).matchAll(/href="([a-z][a-z0-9-]*)"/g)].map((m) => m[1]);
  ok(bare.includes("sim") && bare.every((h) => existsSync(join(repo, "sim", "web", h + ".html"))),
     `…and every extensionless link on the hub has a page behind it (${JSON.stringify(bare)})`);
} finally {
  await browser.close();
  cleanup();
}

finish(LABEL, { fails, count });
