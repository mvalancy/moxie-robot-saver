/* journey/load.mjs — FREE first-visit probe of a deployment: the hub, the hub -> /sim path,
 * 30 s idle, the first tap (on Moxie herself), 30 s after it. Spends nothing: no POST is
 * ever sent (the guard would ledger one; the summary asserts none happened).
 *   node sim/tools/journey/load.mjs [base] [profile...]   profiles: phone-slow phone desktop
 * With no base it aims at the site's own origin.
 */
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { browserFor, visitor, shot, sleep, PROD, PHONE, IOS_UA, DESKTOP, DESKTOP_UA, OUT } from "./lib.mjs";

const base = (process.argv[2] && process.argv[2].startsWith("http")) ? process.argv[2].replace(/\/$/, "") : PROD;
if (!base) { console.error("a base URL is required (no canonical origin in sim/web/index.html)"); process.exit(2); }
const wanted = process.argv.slice(2).filter((a) => !a.startsWith("http"));
const PROFILES = {
  "phone-slow": { viewport: PHONE, ua: IOS_UA, throttle: { cpu: 4, latency: 150, down: 1.6e6, up: 7.5e5 } },
  "phone": { viewport: PHONE, ua: IOS_UA, throttle: null },
  "desktop": { viewport: DESKTOP, ua: DESKTOP_UA, throttle: null },
};
const names = wanted.length ? wanted : Object.keys(PROFILES);
const tag = process.env.JOURNEY_TAG || "prod";
const browser = await browserFor({});
const results = {};
for (const name of names) {
  const prof = PROFILES[name];
  const run = `load-${tag}-${name}`;
  console.log(`\n=== ${run} (${base})`);
  const v = await visitor(browser, { run, ...prof });
  const { page } = v;
  const r = { profile: name };
  // ---- the hub
  const tHub = Date.now();
  await page.goto(base + "/", { waitUntil: "load", timeout: 120000 });
  r.hub_load_wall_ms = Date.now() - tHub;
  await sleep(1500);
  r.hub = await page.evaluate(() => {
    const n = performance.getEntriesByType("navigation")[0];
    const res = performance.getEntriesByType("resource");
    return { fcp: window.__perf.fcp, lcp: window.__perf.lcp, cls: +window.__perf.cls.toFixed(4),
             dcl: Math.round(n.domContentLoadedEventEnd), load: Math.round(n.loadEventEnd),
             bytes: n.transferSize + res.reduce((a, e) => a + (e.transferSize || 0), 0), requests: 1 + res.length,
             anims: document.getAnimations ? document.getAnimations().filter((a) => a.playState === "running").length : null };
  });
  await shot(page, `${run}-hub`);
  // ---- hub -> sim via the primary CTA
  const cta = await page.$("a.btn.primary[href='sim']");
  const tClick = Date.now();
  await Promise.all([page.waitForNavigation({ waitUntil: "domcontentloaded", timeout: 120000 }), cta.click()]);
  r.cta_to_dcl_wall_ms = Date.now() - tClick;
  r.sim_url = page.url();
  await page.waitForFunction(() => window.__tl && window.__tl.some((e) => e.k === "composer-adopted"), { timeout: 120000, polling: 100 }).catch(() => {});
  r.cta_to_composer_wall_ms = Date.now() - tClick;
  await page.waitForFunction(() => document.readyState === "complete", { timeout: 120000 }).catch(() => {});
  r.sim = await page.evaluate(() => {
    const n = performance.getEntriesByType("navigation")[0];
    const res = performance.getEntriesByType("resource");
    const tl = window.__tl || [];
    const at = (k) => { const e = tl.find((x) => x.k === k); return e ? e.t : null; };
    const modeLive = tl.find((x) => x.k === "mode" && x.v && x.v.state !== "boot");
    let gl = null;
    try {
      const c = document.querySelector("#app canvas"); const g = c && (c.getContext("webgl2") || c.getContext("webgl"));
      const ext = g && g.getExtension("WEBGL_debug_renderer_info"); gl = ext ? g.getParameter(ext.UNMASKED_RENDERER_WEBGL) : null;
    } catch (e) {}
    return { fcp: window.__perf.fcp, lcp: window.__perf.lcp, cls: +window.__perf.cls.toFixed(4),
             dcl: Math.round(n.domContentLoadedEventEnd), load: Math.round(n.loadEventEnd),
             moxie_ready: at("moxie-ready"), mode_decided: modeLive ? modeLive.t : null, composer: at("composer-adopted"),
             bytes: n.transferSize + res.reduce((a, e) => a + (e.transferSize || 0), 0), requests: 1 + res.length,
             longtask_ms: window.__perf.longtasks.reduce((a, x) => a + x[1], 0), longtasks: window.__perf.longtasks.length,
             gl, audio_ctx: window.__moxieVoice && window.__moxieVoice.ctx ? window.__moxieVoice.ctx.state : "none" };
  });
  await shot(page, `${run}-sim-ready`);
  // ---- 30 s idle, nobody touches anything
  const idleT0 = await page.evaluate(() => Math.round(performance.now()));
  await sleep(10000); await shot(page, `${run}-idle-10s`);
  await sleep(20000); await shot(page, `${run}-idle-30s`);
  r.idle = await page.evaluate((t0) => ({
    plays: (window.__audio.plays || []).length, started: window.__audio.started,
    ctx: window.__moxieVoice && window.__moxieVoice.ctx ? window.__moxieVoice.ctx.state : "none",
    bubble: (window.__tl || []).filter((e) => e.k === "bubble" && e.t >= t0).map((e) => e.v),
    rows: document.querySelectorAll("#transcript > div").length,
    synth: (window.__tl || []).filter((e) => e.k === "speechSynthesis").length,
    mode: window.moxieMode.snapshot().badge, banner: (document.querySelector("#env-banner .eb-text") || {}).textContent || null,
  }), idleT0);
  // ---- the first tap: on Moxie herself (the middle of the stage)
  const box = await page.evaluate(() => { const r = document.getElementById("stage").getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height * 0.45 }; });
  const tapT = await page.evaluate(() => Math.round(performance.now()));
  if (prof.viewport.hasTouch) await page.touchscreen.tap(box.x, box.y); else await page.mouse.click(box.x, box.y);
  await sleep(1000); await shot(page, `${run}-tap-1s`);
  await sleep(29000); await shot(page, `${run}-tap-30s`);
  r.after_tap = await page.evaluate((t0) => {
    const tl = (window.__tl || []).filter((e) => e.t >= t0);
    const unlock = tl.find((e) => e.k === "audio-unlocked");
    const plays = (window.__audio.plays || []).filter((p) => p.t >= t0);
    return { unlock_ms: unlock ? unlock.t - t0 : null,
             first_sound_ms: plays.length ? Math.round(plays[0].t - t0) : null,
             plays: plays.map((p) => ({ t: Math.round(p.t - t0), src: p.src, dur: Math.round(p.dur), bytes: p.bytes })),
             clips: tl.filter((e) => e.k === "clip-fetch").map((e) => ({ t: e.t - t0, v: e.v })),
             bubble: tl.filter((e) => e.k === "bubble").map((e) => ({ t: e.t - t0, v: e.v })),
             rows: [...document.querySelectorAll("#transcript > div")].map((d) => d.className + ": " + d.textContent.slice(0, 120)),
             ctx: window.__moxieVoice && window.__moxieVoice.ctx ? window.__moxieVoice.ctx.state : "none" };
  }, tapT);
  const d = await v.dump(run);
  const posts = d.net.filter((n) => n.method === "POST");
  r.posts = posts.map((p) => p.url);
  r.http_errors = d.net.filter((n) => (n.status && n.status >= 400) || n.err).map((n) => ({ url: n.url, status: n.status, err: n.err }));
  r.console = d.console.filter((c) => c.type === "error" || c.type === "warning" || c.type === "pageerror");
  r.csp = d.state && d.state.csp;
  results[name] = r;
  console.log(JSON.stringify(r, null, 1).slice(0, 6000));
  await v.context.close();
}
writeFileSync(join(OUT, `load-${tag}-summary.json`), JSON.stringify(results, null, 1));
await browser.close();
