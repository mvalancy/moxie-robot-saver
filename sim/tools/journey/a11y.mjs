/* journey/a11y.mjs — keyboard-only use, screen-reader names + live regions, reduced motion.
 * FREE: run it against a LOCAL copy wired to the zero-spend mock gateway (journey/up.sh mock).
 * The one typed turn and one mic turn are answered by the mock (still ledgered by lib.mjs).
 *   node sim/tools/journey/a11y.mjs --base=http://moxie.hosted.test:PORT --mic=WAV [--tag=local] [--only=kbd,ax,motion]
 */
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { browserFor, visitor, shot, sleep, simReady, pnow, waitTurnDone,
         PHONE, IOS_UA, DESKTOP, DESKTOP_UA, OUT, PROD } from "./lib.mjs";

const flag = (n, d) => { const h = process.argv.find((a) => a.startsWith("--" + n + "=")); return h ? h.slice(n.length + 3) : d; };
const base = String(flag("base", "")).replace(/\/$/, "");
if (!base) { console.error("--base= is required (a LOCAL copy)"); process.exit(2); }
const mic = flag("mic", "");
const tag = flag("tag", "local");
const only = flag("only", "kbd,ax,motion").split(",");
const u = new URL(base);
if (PROD && u.origin === PROD) { console.error("a11y.mjs sends turns: never point it at production"); process.exit(2); }
const hosts = {}, secure = [];
if (u.hostname.endsWith(".test")) { hosts[u.hostname] = Number(u.port); secure.push(u.origin); }
const PH = { ...PHONE, deviceScaleFactor: 2 };

const browser = await browserFor({ hosts, mic: mic || null, secureOrigins: secure });
const R = { base, tag, at: new Date().toISOString() };
const save = () => writeFileSync(join(OUT, `a11y-${tag}.json`), JSON.stringify(R, null, 1));

/* ---- in-page: what has focus, and can a sighted keyboard user SEE it? ---- */
function describeActive() {
  const el = document.activeElement;
  if (!el || el === document.body || el === document.documentElement) return { body: true };
  const r = el.getBoundingClientRect();
  const cs = getComputedStyle(el);
  let hiddenBy = null;
  for (let a = el; a && a !== document.documentElement; a = a.parentElement) {
    const s = getComputedStyle(a);
    if (s.display === "none" || s.visibility === "hidden" || Number(s.opacity) === 0) { hiddenBy = (a.id ? "#" + a.id : a.tagName) + ":" + (s.display === "none" ? "display" : s.visibility === "hidden" ? "visibility" : "opacity"); break; }
  }
  // clipped by a scrolling/overflow ancestor (a closed drawer is often just translated away)
  let clippedBy = null;
  for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
    const s = getComputedStyle(a);
    if (/(hidden|clip|auto|scroll)/.test(s.overflow + s.overflowX + s.overflowY)) {
      const ar = a.getBoundingClientRect();
      if (r.bottom <= ar.top || r.top >= ar.bottom || r.right <= ar.left || r.left >= ar.right) { clippedBy = a.id ? "#" + a.id : a.className || a.tagName; break; }
    }
  }
  const inView = r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
  const name = (el.getAttribute("aria-label") || el.getAttribute("title") || el.textContent || el.getAttribute("placeholder") || el.value || "").replace(/\s+/g, " ").trim().slice(0, 70);
  return {
    tag: el.tagName.toLowerCase(), id: el.id || null,
    cls: typeof el.className === "string" ? el.className.split(" ").slice(0, 2).join(".") : null,
    role: el.getAttribute("role"), name, href: el.getAttribute("href"),
    rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
    inView, hiddenBy, clippedBy,
    ring: { outline: cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0 ? cs.outlineStyle + " " + cs.outlineWidth + " " + cs.outlineColor : "none",
            shadow: cs.boxShadow === "none" ? "none" : cs.boxShadow.slice(0, 60) },
    where: el.closest("#panel") ? "rail" : el.closest("#chat-dock") ? "dock" : el.closest("#topbar") ? "topbar" : el.closest("#notice") ? "notice" : el.closest("#env-banner") ? "banner" : "page",
  };
}

async function tabWalk(page, n, label) {
  const seq = [];
  for (let i = 0; i < n; i++) {
    await page.keyboard.press("Tab");
    await sleep(60);
    const d = await page.evaluate(describeActive);
    seq.push({ i: i + 1, ...d });
    if (i > 3 && seq.length > 2 && d.id && seq[0].id === d.id && d.id === seq[0].id && seq[0].name === d.name) break;  // wrapped
  }
  console.log(`  ${label}: ${seq.length} tab stops`);
  return seq;
}

/* ---- the accessibility tree a screen reader walks (CDP, ignored nodes dropped) ---- */
async function axAudit(cdp) {
  const { nodes } = await cdp.send("Accessibility.getFullAXTree");
  const out = [];
  const ROLES = new Set(["button", "link", "textbox", "checkbox", "slider", "combobox", "image", "img", "switch",
    "log", "status", "heading", "group", "main", "banner", "complementary", "note", "region", "alert", "navigation",
    "toggleButton", "PopUpButton", "searchbox", "spinbutton", "listbox", "option", "radio", "contentinfo", "Canvas", "canvas", "figure", "list", "DisclosureTriangle"]);
  for (const n of nodes) {
    if (n.ignored) continue;
    const role = n.role && n.role.value;
    const props = {};
    for (const p of n.properties || []) props[p.name] = p.value && p.value.value;
    if (!ROLES.has(role) && !props.live) continue;
    out.push({ role, name: ((n.name && n.name.value) || "").replace(/\s+/g, " ").slice(0, 160),
               live: props.live || null, relevant: props.relevant || null, atomic: props.atomic,
               focusable: !!props.focusable, pressed: props.pressed, expanded: props.expanded,
               disabled: props.disabled, level: props.level, hidden: props.hidden });
  }
  return out;
}

/* ===================== 1. keyboard only (desktop) ===================== */
if (only.includes("kbd")) {
  console.log("\n=== keyboard-only, desktop");
  const v = await visitor(browser, { run: `a11y-${tag}-kbd`, viewport: DESKTOP, ua: DESKTOP_UA, grantMic: u.origin,
                                      caps: { chat: 3, speech: 8, transcribe: 1 } });
  const { page } = v;
  const K = {};
  await page.goto(base + "/", { waitUntil: "load" });
  await sleep(1200);
  K.hub_initial_focus = await page.evaluate(describeActive);
  K.hub_tabs = await tabWalk(page, 22, "hub");
  // the primary CTA, by keyboard
  const ctaIdx = K.hub_tabs.findIndex((s) => s.tag === "a" && /Talk to Moxie/i.test(s.name));
  K.hub_cta_tab_index = ctaIdx >= 0 ? K.hub_tabs[ctaIdx].i : null;
  await page.goto(base + "/", { waitUntil: "load" });
  await sleep(800);
  for (let i = 0; i < (K.hub_cta_tab_index || 1); i++) { await page.keyboard.press("Tab"); await sleep(40); }
  K.hub_cta_focused = await page.evaluate(describeActive);
  await shot(page, `a11y-${tag}-kbd-hub-cta-focused`);
  await Promise.all([page.waitForNavigation({ waitUntil: "domcontentloaded", timeout: 60000 }).catch(() => {}), page.keyboard.press("Enter")]);
  K.after_enter_url = page.url();
  await simReady(page);
  await sleep(1500);
  K.sim_initial_focus = await page.evaluate(describeActive);
  K.sim_tabs = await tabWalk(page, 40, "sim");
  K.sim_input_tab_index = (K.sim_tabs.find((s) => s.id === "speech-input") || {}).i || null;
  K.sim_mic_tab_index = (K.sim_tabs.find((s) => s.id === "mic-btn") || {}).i || null;
  K.sim_invisible_stops = K.sim_tabs.filter((s) => !s.body && (!s.inView || s.hiddenBy || s.clippedBy)).map((s) => `${s.i}:${s.tag}${s.id ? "#" + s.id : ""} ${s.name} (${s.hiddenBy || s.clippedBy || "out of view"})`);
  K.sim_no_ring = K.sim_tabs.filter((s) => !s.body && s.ring && s.ring.outline === "none" && s.ring.shadow === "none").map((s) => `${s.i}:${s.tag}${s.id ? "#" + s.id : ""} ${s.name}`);
  // a typed turn by keyboard alone: reload, Tab to the box, type, Enter
  await page.reload({ waitUntil: "domcontentloaded" });
  await simReady(page);
  await sleep(1000);
  for (let i = 0; i < (K.sim_input_tab_index || 0); i++) { await page.keyboard.press("Tab"); await sleep(40); }
  K.at_input = await page.evaluate(describeActive);
  await shot(page, `a11y-${tag}-kbd-input-focused`);
  const t1 = await pnow(page);
  await page.keyboard.type("Hello Moxie, can you hear me?", { delay: 30 });
  await page.keyboard.press("Enter");
  const w1 = await waitTurnDone(page, { sinceT: t1, timeout: 30000 });
  K.typed_turn_waited = w1;
  K.focus_after_reply = await page.evaluate(describeActive);
  K.input_value_after = await page.$eval("#speech-input", (e) => e.value);
  // the mic by keyboard: Tab to Listen, press Space
  if (mic) {
    await page.focus("#speech-input");
    await page.keyboard.press("Tab");
    K.after_input_tab = await page.evaluate(describeActive);
    const t2 = await pnow(page);
    await page.keyboard.press("Space");
    await sleep(1200);
    await shot(page, `a11y-${tag}-kbd-mic-listening`);
    K.mic_focus_while_listening = await page.evaluate(describeActive);
    await page.waitForFunction(() => window.moxieMic && !window.moxieMic.isRecording(), { timeout: 20000, polling: 100 }).catch(() => {});
    const w2 = await waitTurnDone(page, { sinceT: t2, timeout: 40000 });
    K.mic_waited = w2;
    K.focus_after_mic_reply = await page.evaluate(describeActive);
  }
  const d = await v.dump(`a11y-${tag}-kbd`);
  K.mic_status_texts = (d.state.tl || []).filter((e) => e.k === "mic-status").map((e) => e.v);
  K.chat_status_texts = (d.state.tl || []).filter((e) => e.k === "chat-status").map((e) => e.v);
  K.console_errors = d.console.filter((c) => c.type === "error" || c.type === "pageerror");
  R.kbd = K;
  save();
  await v.context.close();
}

/* ===================== 2. what a screen reader is given ===================== */
if (only.includes("ax")) {
  for (const prof of [["phone", PH, IOS_UA], ["desktop", DESKTOP, DESKTOP_UA]]) {
    console.log(`\n=== accessibility tree, ${prof[0]}`);
    const v = await visitor(browser, { run: `a11y-${tag}-ax-${prof[0]}`, viewport: prof[1], ua: prof[2],
                                        caps: { chat: 1, speech: 4, transcribe: 0 } });
    const { page, cdp } = v;
    await cdp.send("Accessibility.enable");
    const A = {};
    await page.goto(base + "/", { waitUntil: "load" });
    await sleep(1200);
    A.hub = await axAudit(cdp);
    A.hub_lang = await page.evaluate(() => document.documentElement.lang);
    A.hub_title = await page.title();
    await page.goto(base + "/sim", { waitUntil: "domcontentloaded" });
    await simReady(page);
    await sleep(1500);
    A.sim_boot = await axAudit(cdp);
    A.sim_title = await page.title();
    // one turn, then what is in the tree (rows, statuses), and the ambient row's exposure
    const box = await page.$("#speech-input");
    await box.click();
    await page.keyboard.type("Tell me a joke", { delay: 20 });
    const t = await pnow(page);
    await page.keyboard.press("Enter");
    await waitTurnDone(page, { sinceT: t, timeout: 30000 });
    // force one ambient quip into the log so its exposure can be read (it is aria-hidden by design)
    await page.evaluate(() => { try { window.__ambient && window.__ambient.say("A test quip to myself."); } catch (e) {} });
    await sleep(300);
    A.sim_after_turn = await axAudit(cdp);
    A.live_regions = A.sim_after_turn.filter((n) => n.live);
    A.unnamed = A.sim_after_turn.filter((n) => ["button", "link", "textbox", "checkbox", "slider", "combobox"].includes(n.role) && !n.name.trim());
    A.mic_status_live = await page.evaluate(() => { const e = document.getElementById("mic-status"); return e ? { live: e.getAttribute("aria-live"), role: e.getAttribute("role") } : null; });
    A.bubble = await page.evaluate(() => { const e = document.getElementById("bubble"); return e ? { live: e.getAttribute("aria-live"), hidden: e.getAttribute("aria-hidden"), cls: e.className } : null; });
    A.headings = A.sim_after_turn.filter((n) => n.role === "heading").map((n) => `h${n.level} ${n.name}`);
    await shot(page, `a11y-${tag}-ax-${prof[0]}-after-turn`);
    R["ax_" + prof[0]] = A;
    save();
    await v.context.close();
  }
}

/* ===================== 3. reduced motion ===================== */
async function sampleMotors(page, ms) {
  return page.evaluate(async (ms) => {
    const n = 7, s = [];
    const t0 = performance.now();
    while (performance.now() - t0 < ms) {
      const row = []; for (let i = 0; i < n; i++) row.push(window.moxie.getMotor(i));
      s.push(row);
      await new Promise((r) => setTimeout(r, 100));
    }
    const travel = [], range = [];
    for (let i = 0; i < n; i++) {
      let tv = 0, lo = Infinity, hi = -Infinity;
      for (let k = 0; k < s.length; k++) { const v = s[k][i]; lo = Math.min(lo, v); hi = Math.max(hi, v); if (k) tv += Math.abs(v - s[k - 1][i]); }
      travel.push(Math.round(tv)); range.push(hi - lo);
    }
    return { samples: s.length, travel, range, total_travel: travel.reduce((a, b) => a + b, 0), names: window.moxie.motorNames };
  }, ms);
}
async function cssAnims(page) {
  return page.evaluate(() => {
    const a = document.getAnimations ? document.getAnimations() : [];
    const running = a.filter((x) => x.playState === "running");
    const by = {};
    for (const x of running) { const t = x.effect && x.effect.target; const k = t ? (t.id ? "#" + t.id : t.className && typeof t.className === "string" ? "." + t.className.split(" ")[0] : t.tagName) : "?"; by[k] = (by[k] || 0) + 1; }
    return { running: running.length, infinite: running.filter((x) => x.effect && x.effect.getTiming && x.effect.getTiming().iterations === Infinity).length, by };
  });
}
if (only.includes("motion")) {
  R.motion = {};
  for (const reduce of [false, true]) {
    const key = reduce ? "reduce" : "default";
    console.log(`\n=== motion, phone, prefers-reduced-motion: ${key}`);
    const v = await visitor(browser, { run: `a11y-${tag}-motion-${key}`, viewport: PH, ua: IOS_UA, reducedMotion: reduce,
                                        caps: { chat: 0, speech: 0, transcribe: 0 } });
    const { page } = v;
    const M = {};
    await page.goto(base + "/", { waitUntil: "load" });
    await sleep(2500);
    M.hub = await cssAnims(page);
    M.hub_matchMedia = await page.evaluate(() => matchMedia("(prefers-reduced-motion: reduce)").matches);
    await page.goto(base + "/sim", { waitUntil: "domcontentloaded" });
    await simReady(page);
    await sleep(1000);
    M.sim_css = await cssAnims(page);
    M.sim_idle_10s = await sampleMotors(page, 10000);
    // the first tap (audio unlock) starts the ambient performer: big gestures live here
    const box = await page.evaluate(() => { const r = document.getElementById("stage").getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height * 0.45 }; });
    await page.touchscreen.tap(box.x, box.y);
    M.after_tap_25s = await sampleMotors(page, 25000);
    M.ambient_lines = await page.evaluate(() => (window.__tl || []).filter((e) => e.k === "bubble" && e.v).map((e) => e.v));
    await shot(page, `a11y-${tag}-motion-${key}-after-tap`);
    R.motion[key] = M;
    save();
    await v.context.close();
  }
}

save();
console.log(JSON.stringify({ kbd: R.kbd && { hub_cta: R.kbd.hub_cta_tab_index, sim_input: R.kbd.sim_input_tab_index, sim_mic: R.kbd.sim_mic_tab_index,
  invisible: R.kbd.sim_invisible_stops, no_ring: R.kbd.sim_no_ring, focus_after_reply: R.kbd.focus_after_reply && (R.kbd.focus_after_reply.id || R.kbd.focus_after_reply.tag),
  focus_after_mic: R.kbd.focus_after_mic_reply && (R.kbd.focus_after_mic_reply.id || R.kbd.focus_after_mic_reply.tag) },
  motion: R.motion && Object.fromEntries(Object.entries(R.motion).map(([k, m]) => [k, { hub: m.hub.running, sim_css: m.sim_css.running, idle_travel: m.sim_idle_10s.total_travel, tap_travel: m.after_tap_25s.total_travel }])) }, null, 1));
await browser.close();
