/* test_a11y.mjs — the Sim page as a screen reader and a keyboard actually meet it.
 *
 * The robot is built for children, including children with communication differences, so
 * the demo's accessibility semantics are asserted like layout: in real Chrome, real files.
 * Every check is an IDENTITY check, never a count: names are read out of Chrome's own a11y
 * tree against a table of selector -> exact expected name, and the unnamed-control sweep
 * reports the offenders themselves.
 *
 * ZERO GATEWAY SPEND: `/api/chat`, `/api/speech`, `/api/transcribe` are aborted and the
 * suite FAILS if the page asks for one; `/api/health` is fulfilled locally.
 */
import { requireBrowser, serveWeb, makeChecks, finish, notable, launchBrowser, openSim }
  from "./browser_harness.mjs";

const LABEL = "a11y";
const { puppeteer, chrome } = await requireBrowser(LABEL);
const { fails, ok, eq, count } = makeChecks();
const deep = (a, b, m) => eq(JSON.stringify(a), JSON.stringify(b), m);
const srv = await serveWeb({ headers: true });

/** A `/api/health` body that puts mode.js in `live` — the branch the hosted site is in. */
const HEALTH_LIVE = JSON.stringify({
  mode: "live", reason: null, voice: true, ears: true,
  load: { level: "ok", inflight: 0, capacity: 4 },
  limits: { max_input_chars: 500, max_tts_chars: 300 },
});

// `moxie.hosted.test` plays the public hostname (env.js reads hosted) for block 4b.
const browser = await launchBrowser(puppeteer, chrome, { autoplay: true, hosts: { "moxie.hosted.test": srv.port } });

/** A settled /sim.html under the shipped CSP; spendy /api routes are aborted into `spent`. */
async function open(o = {}) {
  return openSim(browser, srv.url + "/sim.html", {
    viewport: { width: o.width || 1440, height: o.height || 900 }, health: o.health,
    beforeLoad: o.reducedMotion
      ? (p) => p.emulateMediaFeatures([{ name: "prefers-reduced-motion", value: "reduce" }]) : null,
  });
}

/* ==========================================================================
 * WHAT THE BROWSER ITSELF SAID
 *
 * On 127.0.0.1 under the real CSP, env.js's :8081/:8082 sidecar probes are refused (each
 * reported twice) — four errors the page is RIGHT to produce, forgiven only when they name
 * those ports AND a CSP refusal. Any other refusal fails: it is how this page breaks.
 * ======================================================================= */
const SIDECAR_CSP = /127\.0\.0\.1:(8081|8082)\/health/;
const CSP_REFUSAL = /Content Security Policy|Refused to connect/;
const isSidecarProbe = (e) => SIDECAR_CSP.test(e) && CSP_REFUSAL.test(e);

/** One page's console: the sidecar-probe refusals capped at four, and NOTHING else. */
function eyes(label, { errs, aborted }) {
  const probes = errs.filter(isSidecarProbe);
  const rest = notable(errs.filter((e) => !isSidecarProbe(e)), aborted);
  ok(probes.length <= 4,
     `${label}: env.js's two sidecar probes cost at most 4 CSP refusals — got ` +
     `${probes.length}: ${probes.slice(0, 5).join(" | ")}`);
  eq(rest.length, 0,
     `${label}: the page raised console errors nobody asked for — ${rest.length}, ` +
     `first: ${rest.slice(0, 3).join(" | ")}`);
}

/** The accessible name Chrome computes for one element, or null when it has no AX node. */
async function axName(page, selector) {
  const h = await page.$(selector);
  if (!h) return undefined;
  const s = await page.accessibility.snapshot({ root: h, interestingOnly: false });
  return s ? s.name : null;
}

/** `el.textContent`, or null when the element is not there — so a MISSING element is
 *  reported as a failed assertion rather than an exception that ends the run. */
async function textOf(page, selector) {
  const h = await page.$(selector);
  return h ? page.evaluate((e) => e.textContent, h) : null;
}

/** Every AX node of the page (or of `root`), flattened. */
async function axNodes(page, root) {
  const snap = await page.accessibility.snapshot({ interestingOnly: false, ...(root ? { root } : {}) });
  const flat = [];
  (function walk(n) { if (!n) return; flat.push(n); (n.children || []).forEach(walk); })(snap);
  return flat;
}

/** Every interactive AX node with an EMPTY name, as `role=… value=…` strings. */
async function unnamedControls(page) {
  const snap = await page.accessibility.snapshot({ interestingOnly: false });
  const flat = [];
  (function walk(n) { if (!n) return; flat.push(n); (n.children || []).forEach(walk); })(snap);
  const INTERACTIVE = new Set(["button", "link", "textbox", "combobox", "checkbox", "slider",
    "radio", "searchbox", "switch", "spinbutton", "ColorWell", "menuitem"]);
  return flat.filter((n) => INTERACTIVE.has(n.role) && !n.name)
             .map((n) => `${n.role}=${JSON.stringify(n.value ?? "")}`);
}

/** Infinite CSS animations actually RUNNING (the ALIVE lamp pulses on every load). */
const looping = (page) => page.evaluate(() => document.getAnimations()
  .filter((a) => a.playState === "running" && a.effect &&
                 a.effect.getComputedTiming().iterations === Infinity)
  .map((a) => (a.effect.target && (a.effect.target.id || a.effect.target.className)) + ":" + a.animationName));
let scriptedNote = null;

/** Every element a Tab press can land on (visible, or fixed), by id or tag.class. */
const tabbablesOf = (page) => page.evaluate(() => {
  const sel = "a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), " +
              "textarea:not([disabled]), summary, [tabindex]:not([tabindex='-1'])";
  return [...document.querySelectorAll(sel)]
    .filter((e) => e.offsetParent !== null || getComputedStyle(e).position === "fixed")
    .map((e) => e.id || e.tagName.toLowerCase() + "." + (e.className || "").split(" ")[0]);
});
// Controls that are genuinely INSIDE the rail, one from each of its four groups.
const RAIL_ONLY = ["center-btn", "tts-base", "stt-base", "bus-host", "rec-toggle"];

/** The REAL tab order: Tab pressed from the top until focus leaves the page or comes round
 *  again. An element is named by id, else tag.class, else tag:its text. */
async function tabWalk(page, max = 30) {
  await page.evaluate(() => { if (document.activeElement && document.activeElement.blur) document.activeElement.blur(); });
  const seen = [];
  for (let i = 0; i < max; i++) {
    await page.keyboard.press("Tab");
    const d = await page.evaluate(() => {
      const e = document.activeElement;
      if (!e || e === document.body) return null;
      const cls = typeof e.className === "string" && e.className.trim() ? e.className.trim().split(/\s+/)[0] : "";
      return e.id || e.tagName.toLowerCase() + (cls ? "." + cls : ":" + e.textContent.replace(/\s+/g, " ").trim().slice(0, 24));
    });
    if (d === null) { if (seen.length) break; continue; }
    if (seen.length && d === seen[0]) break;
    seen.push(d);
  }
  return seen;
}
// The hosted desktop page's twelve stops, rail shut: measured before W4-S6 (the visitor-journey
// a11y probe pressed Tab 14 times: these 12, the page itself, and round again).
const HOSTED_STOPS = ["hub-back", "alive-toggle", "a:Matthew Valancy", "a.notice-link", "rail-toggle",
                      "transcript", "button.opener", "button.opener", "button.opener",
                      "speech-input", "mic-btn", "speech-btn"];
// What she keeps, as sim.html says it under the composer (W4-S6).
const MEMORY_LINE = "Moxie remembers this chat only while this page is open.";

/* ==========================================================================
 * ONE PAGE, scripted mode (no /api/health): 1 names, 3a the scripted voice note, the
 * reduced-motion CONTROL, then 2 the live region and a scripted typed turn.
 * ==========================================================================
 * 1. NAMES — the finding, re-derived, and asserted by identity
 * ======================================================================= */
{
  const view = await open();
  const { page, spent } = view;

  const bare = await unnamedControls(page);
  ok(bare.length === 0, `interactive controls with NO accessible name: [${bare.join(", ")}]`);

  // The seven motor sliders, in panel order: asserts the exact names the HUD glue copies
  // from the non-labelling <label> text.
  const sliderNames = await page.$$eval('#motors .motor input[type="range"]',
    (els) => els.map((e) => e.getAttribute("aria-label")));
  const WANT_SLIDERS = [
    "L shoulder (up/down), motor 0", "L shoulder (in/out), motor 1",
    "R shoulder (up/down), motor 2", "R shoulder (in/out), motor 3",
    "Head tilt (nod), motor 4", "Body turn (yaw), motor 5", "Body lean (F/B), motor 6",
  ];
  eq(JSON.stringify(sliderNames), JSON.stringify(WANT_SLIDERS), "motor slider names");
  // ...and that the browser agrees they are the sliders' NAMES, not just attributes.
  eq(await axName(page, "#motors .motor:nth-of-type(5) input"),
     "Head tilt (nod), motor 4", "AX name of the head-tilt slider");

  // The rest of the table: selector -> the exact name a screen reader should read.
  const NAMED = {
    "#led-color": "Heart LED colour",
    "#qr-kind": "QR code type",
    "#speech-input": "Message to Moxie",
    "#tts-base": "Local Piper text-to-speech server address",
    "#stt-base": "Local speech-to-text server address",
    "#bus-host": "MQTT broker host",
  };
  for (const [sel, want] of Object.entries(NAMED))
    eq(await axName(page, sel), want, `accessible name of ${sel}`);

  // The two Wi-Fi boxes only exist in the tree once the QR kind reveals them (they are
  // `display:none` until then, so an unrevealed check would pass on an absent node).
  await page.select("#qr-kind", "wifi");
  await page.waitForFunction(() => document.getElementById("qr-wifi").style.display === "", { timeout: 5000 })
    .catch(() => {});
  eq(await page.$eval("#qr-wifi", (e) => e.style.display), "", "choosing wi-fi reveals its fields");
  eq(await axName(page, "#qr-ssid"), "Wi-Fi network name (SSID)", "accessible name of #qr-ssid");
  eq(await axName(page, "#qr-pass"), "Wi-Fi password", "accessible name of #qr-pass");
  await page.select("#qr-kind", "OPEN_MOXIE");

  // A phrase chip's visible text is truncated to fit; its NAME must be the whole line.
  const chip = await page.$$eval("#speech-chips .chip", (els) =>
    els.map((e) => ({ text: e.textContent, aria: e.getAttribute("aria-label"), title: e.title }))
       .find((c) => c.text.endsWith("…")) || null);
  ok(chip !== null, "at least one phrase chip is visually truncated");
  if (chip) {
    eq(chip.aria, chip.title, "a truncated chip's name is the full phrase, not the ellipsis");
    // Guarded, not indexed: with no aria-label at all this must REPORT a missing name,
    // not throw — a suite that crashes proves nothing about the page it crashed on.
    ok(!!chip.aria && !chip.aria.endsWith("…"),
       `chip name is the whole phrase — got ${JSON.stringify(chip.aria)}`);
  }

  /* ---- the canvas: named as a live view, NOT hidden ---- */
  const app = await page.$eval("#app", (e) => ({
    role: e.getAttribute("role"), label: e.getAttribute("aria-label"),
    hidden: e.getAttribute("aria-hidden"), canvases: e.querySelectorAll("canvas").length,
  }));
  eq(app.role, "img", "#app (the WebGL stage) carries role=img");
  ok(app.canvases === 1, "the renderer canvas is inside #app");
  ok(app.hidden !== "true", "the stage is NOT aria-hidden — it is the subject of the page");
  ok(/moxie/i.test(app.label || "") && /comms log/i.test(app.label || ""),
     `stage name names Moxie and points at the text log — got ${JSON.stringify(app.label)}`);

  /* ---- what she keeps: said only where it is exactly true (block 4b, a hosted live page) ---- */
  eq(await page.$eval("#memory-hint", (e) => e.hidden).catch(() => "missing"), true,
     "a scripted local page makes no memory claim (#memory-hint stays hidden)");

  /* ---- <noscript> ---- */
  const ns = await page.$eval("noscript", (e) => e.textContent).catch(() => null);
  ok(ns !== null, "the page has a <noscript> block");
  if (ns !== null) {
    ok(/javascript/i.test(ns), "<noscript> says the page needs JavaScript");
    // <noscript> content is not parsed as DOM while scripting is on, so read the source.
    const raw = await page.$eval("noscript", (e) => e.innerHTML);
    ok(/href="\.\/"/.test(raw), "<noscript> links back to the hub");
    ok(/docs/i.test(raw), "<noscript> points at the docs");
  }

  /* ---- 3a. the standing voice note describes what the composer's button does: scripted ---- */
  const note = scriptedNote = await textOf(page, "#voice-note");
  eq(await page.$eval("#speech-btn", (e) => e.textContent.trim()), "Ask",
     "with no Piper the Say button is the typed turn");
  ok(/press Ask/i.test(note || "") && /scripted/i.test(note || ""),
     `scripted note names the Ask button and says the answer is scripted — got ${JSON.stringify(note)}`);
  ok(note !== null && !/browser.{1,6}s voice|in her own voice/i.test(note),
     `scripted note must not promise the browser's voice or her live voice — got ${JSON.stringify(note)}`);

  // Reduced-motion CONTROL: without the preference something really does loop (section 5).
  const base = await looping(page);
  ok(base.length > 0, `CONTROL: without the preference something really does loop (${JSON.stringify(base)})`);

  /* ---- 2. THE LIVE REGION — and what it deliberately does NOT announce ---- */

  const t = await page.$eval("#transcript", (e) => ({
    role: e.getAttribute("role"), live: e.getAttribute("aria-live"),
    relevant: e.getAttribute("aria-relevant"), label: e.getAttribute("aria-label"),
    tabindex: e.getAttribute("tabindex"),
    overflow: getComputedStyle(e).overflowY,
  }));
  eq(t.role, "log", "#transcript is role=log");
  eq(t.live, "polite", "#transcript announces politely (never assertive)");
  ok(/\btext\b/.test(t.relevant || ""),
     `aria-relevant must include 'text' — a streamed reply appends to the SAME row; got ${JSON.stringify(t.relevant)}`);
  ok(!!t.label, "#transcript has a name of its own");
  eq(t.tabindex, "0", "#transcript is focusable — it scrolls and holds nothing focusable");
  eq(t.overflow, "auto", "#transcript really is a scroll container (so the tab stop earns itself)");

  // ...and the ring that tab stop needs.
  await page.focus("#transcript");
  const ring = await page.$eval("#transcript", (e) => {
    const cs = getComputedStyle(e);
    return { w: cs.outlineWidth, style: cs.outlineStyle, focused: document.activeElement === e };
  });
  ok(ring.focused, "#transcript takes focus");
  ok(ring.style !== "none" && parseFloat(ring.w) > 0,
     `focused #transcript shows an outline — got ${ring.style} ${ring.w}`);

  /* ---- AMBIENT MUST NOT BE ANNOUNCED ----
   * Unprompted quips go to #bubble, never the log. Structure (the bubble is in no live
   * region) AND behaviour (real quips leave the log byte-identical while the bubble
   * changes) — "the log did not grow" alone would pass if ambient said nothing. */
  const bubble = await page.$eval("#bubble", (e) => {
    let n = e, live = null, log = false;
    while (n && n.getAttribute) {
      if (!live && n.getAttribute("aria-live")) live = n.getAttribute("aria-live");
      if (n.getAttribute("role") === "log") log = true;
      n = n.parentElement;
    }
    return { live, log, inTranscript: !!e.closest("#transcript") };
  });
  eq(bubble.live, null, "#bubble is in NO live region — every idle quip would be announced");
  ok(!bubble.log && !bubble.inTranscript, "#bubble is not inside the comms log");

  const before = await page.$$eval("#transcript .turn", (els) => els.map((e) => e.textContent));
  const quips = await page.evaluate(async () => {
    const seen = [];
    const bt = document.getElementById("bubble-text");
    for (let i = 0; i < 3; i++) {
      bt.textContent = "";
      window.moxieAmbient.say();
      // the quip's bubble text, waited for (bounded) rather than slept for
      for (let t = 0; t < 100 && !bt.textContent; t++) await new Promise((r) => setTimeout(r, 50));
      if (bt.textContent) seen.push(bt.textContent);
    }
    return seen;
  });
  ok(quips.length > 0, "ambient self-talk actually ran (otherwise the next check is vacuous)");
  const after = await page.$$eval("#transcript .turn", (els) => els.map((e) => e.textContent));
  eq(JSON.stringify(after), JSON.stringify(before),
     `ambient quips ${JSON.stringify(quips.slice(0, 2))} must not enter the live region`);

  /* ---- a visitor-directed turn MUST be announced, by its exact text ----
   * Scripted mode (no /api/health), so this turn goes to stub.js and costs nothing. */
  const MINE = "does the live region carry my words";
  await page.$eval("#speech-input", (e, v) => { e.value = v; }, MINE);
  await page.click("#speech-btn");
  await page.waitForFunction(
    (v) => [...document.querySelectorAll("#transcript .turn")].some((r) => r.textContent.includes(v)),
    { timeout: 15000 }, MINE);
  // ...and wait for HER side of it: stub.js answers a beat later, and the answer is what
  // the live region exists to deliver. Waiting only for the echo would assert half of it.
  await page.waitForSelector("#transcript .turn.moxie", { timeout: 15000 });
  const rows = await page.$$eval("#transcript .turn",
    (els) => els.map((e) => ({ who: e.className, msg: e.querySelector(".msg").textContent })));
  ok(rows.some((r) => /\buser\b/.test(r.who) && r.msg === MINE),
     `the visitor's own line lands in the log verbatim — got ${JSON.stringify(rows)}`);
  ok(rows.some((r) => /\bmoxie\b/.test(r.who) && r.msg.length > 0),
     `Moxie's answer lands in the log — got ${JSON.stringify(rows)}`);
  ok(!quips.some((q) => rows.some((r) => r.msg === q)),
     "no ambient quip leaked into the log alongside the answer");

  eq(spent.length, 0, "a scripted typed turn spends nothing");
  eyes("live region", view);
  await page.close();
}

/* ==========================================================================
 * 3. The standing voice note must describe what the composer's button actually does —
 *    the note and the button cannot describe different pages.
 * ======================================================================= */
{
  // (b) live: /api/health says the brain and the voice are on.
  const live = await open({ health: HEALTH_LIVE });
  const snap = await live.page.evaluate(() => window.moxieMode.snapshot());
  eq(snap.state, "live", "mode.js reached the live state from the fulfilled health route");
  ok(snap.liveTurns, "live turns are spendable in this fixture");
  const l = await textOf(live.page, "#voice-note");
  const lbtn = await live.page.$eval("#speech-btn", (e) => e.textContent.trim());
  eq(lbtn, "Ask", "the live page's button is Ask");
  ok(/press Ask/i.test(l || ""), `live note names the Ask button — got ${JSON.stringify(l)}`);
  ok(/in her own voice/i.test(l || ""), `live note says she answers in her own voice — got ${JSON.stringify(l)}`);
  ok(l !== null && !/browser.{1,6}s voice|scripted/i.test(l),
     `live note must not claim the browser's voice or a scripted answer — got ${JSON.stringify(l)}`);
  ok(l !== scriptedNote, "the note actually differs between live and scripted");
  eq(live.spent.length, 0, "reading the live copy never posts a turn");
  eyes("live copy", live);
  await live.page.close();
}

/* ==========================================================================
 * 4. KEYBOARD — the rail drawer, its announced state, and no phantom tab stops.
 * The composer (#speech-input, #transcript) lives OUTSIDE the rail and must be reachable
 * with the drawer shut (backlog/mobile-first-visit.md); a collapsed drawer must leave no
 * tab stops behind it.
 * ======================================================================= */
{
  const view = await open({ width: 390, height: 780 });
  const { page } = view;

  const tabbables = () => tabbablesOf(page);

  const closed = await page.$eval("#rail-toggle", (e) => e.getAttribute("aria-expanded"));
  eq(closed, "false", "the drawer starts collapsed on a phone, and SAYS so");
  eq(await page.$eval("#rail-scroll", (e) => getComputedStyle(e).display), "none",
     "a collapsed rail is display:none — anything else leaves invisible tab stops");
  const shut = await tabbables();
  const leaked = RAIL_ONLY.filter((id) => shut.includes(id));
  eq(JSON.stringify(leaked), "[]",
     `nothing inside the collapsed rail is tabbable — leaked ${JSON.stringify(leaked)} ` +
     `out of ${JSON.stringify(shut)}`);
  ok(shut.includes("rail-toggle"), "the toggle itself is reachable");

  /* With the drawer shut and nothing tapped, a keyboard visitor reaches the whole
   * conversation — no engineering panel needed to say hello. */
  for (const id of ["transcript", "speech-input", "mic-btn", "speech-btn"])
    ok(shut.includes(id),
       `#${id} is reachable with the rail SHUT — the composer is not panel content ` +
       `(got ${JSON.stringify(shut)})`);
  ok(shut.indexOf("speech-input") < shut.indexOf("speech-btn"),
     "…in the order a visitor uses them: the box, then the buttons");

  await page.click("#rail-toggle");
  await page.waitForFunction(() => getComputedStyle(document.getElementById("rail-scroll")).display !== "none",
                             { timeout: 5000 }).catch(() => {});
  eq(await page.$eval("#rail-toggle", (e) => e.getAttribute("aria-expanded")), "true",
     "aria-expanded flips when the drawer opens");
  const open2 = await tabbables();
  const back = RAIL_ONLY.filter((id) => open2.includes(id));
  eq(JSON.stringify(back), JSON.stringify(RAIL_ONLY),
     `the opened rail's controls are reachable — got ${JSON.stringify(open2.slice(0, 14))}`);
  // …and in DOM order the toggle comes before what it controls.
  ok(open2.indexOf("rail-toggle") < open2.indexOf("center-btn"),
     "focus reaches the toggle before the panel it discloses");
  // …while the composer stayed where it was, after the whole panel it is not part of.
  ok(open2.includes("speech-input") && open2.indexOf("center-btn") < open2.indexOf("speech-input"),
     "the composer is still reachable with the rail open, and still comes after it");
  eq(await page.$eval("#rail-toggle", (e) => e.getAttribute("aria-controls")), "rail-scroll",
     "aria-controls names the disclosed region");

  eyes("keyboard/phone", view);
  await page.close();
}

/* ==========================================================================
 * 4b. HOSTED DESKTOP — the rail's new default under the same keyboard contract. On a hosted
 * page the >= 900 px column starts COLLAPSED (rail.js: a stranger meets a toy, not servo
 * sliders), so it must SAY so, leave no tab stops behind it, keep the whole conversation
 * reachable, and still disclose its controls. (Block 4 is the phone drawer; the local
 * desktop column still starts open, which block 1 above relies on.)
 * ======================================================================= */
{
  const view = await openSim(browser, `http://moxie.hosted.test:${srv.port}/sim.html`,
                             { viewport: { width: 1440, height: 900 }, health: HEALTH_LIVE });
  const { page, spent } = view;
  eq(await page.evaluate(() => document.body.getAttribute("data-env")), "hosted",
     "precondition: the mapped hostname reads as a hosted page");

  /* WHAT SHE KEEPS (W4-S6). The one line that said it ended the banner, which a live page
   * hides (style.css), so it was in no screen reader's tree and on nobody's screen. */
  const keeps = await page.evaluate(() => {
    const e = document.getElementById("memory-hint");
    return e ? { hidden: e.hidden, text: e.textContent.trim(),
                 shown: e.getClientRects().length > 0 && getComputedStyle(e).visibility !== "hidden",
                 inDock: !!e.closest("#chat-dock") } : null;
  });
  ok(!!keeps && !keeps.hidden && keeps.shown && keeps.inDock,
     `hosted live: what she keeps is SHOWN, in the dock under the composer (${JSON.stringify(keeps)})`);
  eq(keeps && keeps.text, MEMORY_LINE, "hosted live: …in exactly these words");
  ok((await axNodes(page)).some((n) => (n.name || "").trim() === MEMORY_LINE),
     "hosted live: …and a screen reader reaches it (it is in Chrome's accessibility tree)");
  // …and it added no tab stop: the REAL order, Tab by Tab, is exactly the twelve it was.
  eq(JSON.stringify(await tabWalk(page)), JSON.stringify(HOSTED_STOPS),
     "hosted desktop: the tab order is unchanged — the same twelve stops, in the same order");

  eq(await page.$eval("#rail-toggle", (e) => e.getAttribute("aria-expanded")), "false",
     "hosted desktop: the rail starts collapsed, and SAYS so");
  eq(await page.$eval("#rail-scroll", (e) => getComputedStyle(e).display), "none",
     "hosted desktop: the collapsed rail is display:none — no invisible tab stops");
  const shut = await tabbablesOf(page);
  eq(JSON.stringify(RAIL_ONLY.filter((id) => shut.includes(id))), "[]",
     `hosted desktop: nothing inside the collapsed rail is tabbable (${JSON.stringify(shut)})`);
  for (const id of ["rail-toggle", "transcript", "speech-input", "mic-btn", "speech-btn"])
    ok(shut.includes(id), `hosted desktop: #${id} is reachable with the rail shut (${JSON.stringify(shut)})`);
  await page.click("#rail-toggle");
  await page.waitForFunction(() => getComputedStyle(document.getElementById("rail-scroll")).display !== "none",
                             { timeout: 5000 }).catch(() => {});
  eq(await page.$eval("#rail-toggle", (e) => e.getAttribute("aria-expanded")), "true",
     "hosted desktop: aria-expanded flips when the visitor opens it");
  // The localhost-only inputs (Piper / STT / broker addresses) are DISABLED on a hosted page by
  // design (env.js: CSP refuses their only job), so the tab stops are the rail's live controls.
  const open = await tabbablesOf(page);
  const LIVE_HERE = ["center-btn", "rec-toggle"];
  eq(JSON.stringify(LIVE_HERE.filter((id) => open.includes(id))), JSON.stringify(LIVE_HERE),
     `hosted desktop: …and its controls are reachable again (${JSON.stringify(open.slice(0, 14))})`);
  await page.evaluate(() => { try { localStorage.removeItem("moxie.railOpen"); } catch (e) {} });
  eq(spent.length, 0, "the hosted desktop page spends nothing");
  eyes("hosted desktop", view);
  await page.close();
}

/* ==========================================================================
 * 5. REDUCED MOTION — the chrome holds still, and the control proves it would not
 * ======================================================================= */
{
  const view = await open({ reducedMotion: true });
  const still = await looping(view.page);
  eq(JSON.stringify(still), "[]", `prefers-reduced-motion: no endlessly looping animation (${JSON.stringify(still)})`);
  const shown = await view.page.evaluate(() => {
    window.moxie.setSpeech("Hello there, friend.");
    return new Promise((r) => requestAnimationFrame(() =>
      r(document.getElementById("bubble-text").textContent)));
  });
  eq(shown, "Hello there, friend.", "…and her words appear whole, with no typewriter");
  eyes("reduced motion", view);
  await view.page.close();
}

/* ==========================================================================
 * 6. THE MIC STATUS IS ANNOUNCED (W4-S6). "listening…", "transcribing…", what she heard
 * and the no-speech line answer a tap on Listen, but #mic-status was in no live region, so
 * a screen reader said none of them. Driven for real on a hosted live page through a
 * stand-in capture (no device); its one upload is answered at the browser.
 * ======================================================================= */
{
  const EMPTY_TRANSCRIPT = JSON.stringify({ ok: true, degraded: false, reason: null, mode: "live", transcript: "" });
  const view = await openSim(browser, `http://moxie.hosted.test:${srv.port}/sim.html`, {
    viewport: { width: 390, height: 844 }, health: HEALTH_LIVE,
    beforeLoad: (p) => p.evaluateOnNewDocument(() => {
      window.__micLines = [];
      const watch = () => {
        const el = document.getElementById("mic-status");
        if (!el) return;
        new MutationObserver(() => window.__micLines.push(el.textContent))
          .observe(el, { childList: true, characterData: true, subtree: true });
      };
      document.addEventListener("DOMContentLoaded", watch, { once: true });
    }),
    route: (r, u) => /\/api\/transcribe\b/.test(u)
      ? (r.respond({ status: 200, contentType: "application/json", body: EMPTY_TRANSCRIPT }), true) : false,
  });
  const { page, spent } = view;
  const mic = await page.$eval("#mic-status", (e) => ({ live: e.getAttribute("aria-live"), role: e.getAttribute("role") }));
  deep([mic.role, mic.live], ["status", "polite"], "#mic-status is a polite status region — every mic outcome is announced");
  const ax = await axNodes(page, await page.$("#mic-status"));
  eq(ax.length ? ax[0].role : null, "status", "…and Chrome's accessibility tree says so");

  const lines = await page.evaluate(async () => {
    const wait = async (fn, ms = 6000) => { for (let i = 0; i < ms / 50 && !fn(); i++) await new Promise((r) => setTimeout(r, 50)); };
    window.moxieMic.setCapture(() => Promise.resolve({ stream: null,
      setLevelListener: (fn) => { window.__level = fn; },
      recorder: { state: "inactive", mimeType: "audio/wav", ondataavailable: null, onstop: null,
        start() { this.state = "recording"; },
        stop() { if (this.state === "inactive") return; this.state = "inactive";
                 if (this.ondataavailable) this.ondataavailable({ data: new Blob([new Uint8Array(4000)], { type: "audio/wav" }) });
                 if (this.onstop) this.onstop(); } } }));
    // A clip with speech in it: listening, then transcribing, then what came back.
    await window.moxieMic.start();
    window.__level(0.2);
    window.moxieMic.stop();
    await wait(() => /nothing heard/.test(document.getElementById("mic-status").textContent));
    // …and one with none: the no-speech line, nothing sent.
    await window.moxieMic.start();
    window.__level(0.001);
    window.moxieMic.stop();
    await wait(() => /did not hear/.test(document.getElementById("mic-status").textContent));
    return window.__micLines.slice();
  });
  for (const [what, rx] of [["listening…", /listening/], ["transcribing…", /transcribing/],
                            ["the no-speech line", /did not hear anything/]])
    ok(lines.some((l) => rx.test(l)), `${what} reaches the live region (#mic-status wrote ${JSON.stringify(lines)})`);
  eq(spent.length, 0, "the mic block spends nothing (its one upload was answered at the browser)");
  eyes("mic status", view);
  await page.close();
}

/* ==========================================================================
 * 7. THE HUB (W4-S6): no image without a name, and what she keeps, said before anyone talks.
 * Its sixteen decorative icons (logo, arrows, card glyphs, GitHub marks) were unnamed images
 * to a screen reader; the hero picture is the one image with something to say.
 * ======================================================================= */
{
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });
  await page.goto(srv.url + "/", { waitUntil: "load" });
  const nodes = await axNodes(page);
  const images = nodes.filter((n) => /^(image|img|graphics-symbol|graphics-document)$/.test(n.role || ""));
  const unnamed = images.filter((n) => !(n.name || "").trim());
  eq(unnamed.length, 0, `the hub exposes no unnamed image (got ${unnamed.length} of ${images.length})`);
  ok(images.some((n) => /friendly teal robot/.test(n.name || "")), "…and her picture keeps its description");
  ok(nodes.some((n) => (n.name || "").trim() === "Moxie remembers your chat only while her page is open."),
     "the hub says what she keeps, in the accessibility tree, next to the way in");
  await page.close();
}

await browser.close();
srv.close();
finish(LABEL, { fails, count });
