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
import { requireBrowser, serveWeb, makeChecks, finish, watchPage, notable, launchBrowser }
  from "./browser_harness.mjs";

const LABEL = "a11y";
const { puppeteer, chrome } = await requireBrowser(LABEL);
const { fails, ok, eq, count } = makeChecks();
const srv = await serveWeb({ headers: true });

const SPENDY = /\/api\/(chat|speech|transcribe)\b/;
/** A `/api/health` body that puts mode.js in `live` — the branch the hosted site is in. */
const HEALTH_LIVE = JSON.stringify({
  mode: "live", reason: null, voice: true, ears: true,
  load: { level: "ok", inflight: 0, capacity: 4 },
  limits: { max_input_chars: 500, max_tts_chars: 300 },
});

const browser = await launchBrowser(puppeteer, chrome, { autoplay: true });

/**
 * A loaded /sim.html.
 * @param {{width?:number,height?:number,health?:string|null,reducedMotion?:boolean}} o
 *   `health` non-null fulfils GET /api/health with that body (mode.js -> live).
 */
async function open(o = {}) {
  const page = await browser.newPage();
  await page.setViewport({ width: o.width || 1440, height: o.height || 900 });
  const spent = [];
  const { errs, aborted } = watchPage(page);
  await page.setRequestInterception(true);
  page.on("request", (r) => {
    const u = r.url();
    if (SPENDY.test(u)) { spent.push(u); aborted.n++; return r.abort(); }   // never spend
    if (o.health != null && /\/api\/health\b/.test(u))
      return r.respond({ status: 200, contentType: "application/json", body: o.health });
    // No fixture: the probe 404s at the static server; counted so `notable()` forgives just it.
    if (o.health == null && /\/api\/health\b/.test(u)) aborted.refused++;
    return r.continue();
  });
  if (o.reducedMotion)
    await page.emulateMediaFeatures([{ name: "prefers-reduced-motion", value: "reduce" }]);
  await page.goto(srv.url + "/sim.html", { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => !!window.moxie, { timeout: 30000 });
  await new Promise((r) => setTimeout(r, 2500));       // sidecar probe + first mode render
  return { page, spent, errs, aborted };
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

/* ==========================================================================
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
  await new Promise((r) => setTimeout(r, 150));
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

  eq(spent.length, 0, "no request to a spendy /api route");
  eyes("names", view);
  await page.close();
}

/* ==========================================================================
 * 2. THE LIVE REGION — and what it deliberately does NOT announce
 * ======================================================================= */
{
  const view = await open();
  const { page, spent } = view;

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
   * region) AND behaviour (five real quips leave the log byte-identical while the bubble
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
    for (let i = 0; i < 5; i++) {
      window.moxieAmbient.say();
      await new Promise((r) => setTimeout(r, 1400));
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
  // (a) scripted / no backend: typing reaches a SCRIPTED Moxie, not the browser's voice.
  const view = await open();
  const { page, spent } = view;
  const s = await textOf(page, "#voice-note");
  const btn = await page.$eval("#speech-btn", (e) => e.textContent.trim());
  eq(btn, "Ask", "with no Piper the Say button is the typed turn");
  ok(/press Ask/i.test(s || ""), `scripted note names the Ask button — got ${JSON.stringify(s)}`);
  ok(/scripted/i.test(s || ""), `scripted note says the answer is scripted — got ${JSON.stringify(s)}`);
  ok(s !== null && !/browser's voice|browser&#39;s voice|browser’s voice/i.test(s),
     `scripted note must not still claim free text uses the browser's voice — got ${JSON.stringify(s)}`);
  ok(s !== null && !/in her own voice/i.test(s), "scripted note must not promise a live voice");
  eq(spent.length, 0, "no spend while reading the scripted copy");
  eyes("scripted copy", view);
  await page.close();

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
  ok(l !== null && !/browser's voice|browser&#39;s voice|browser’s voice/i.test(l),
     `live note must not claim free text uses the browser's voice — got ${JSON.stringify(l)}`);
  ok(l !== null && !/scripted/i.test(l), "live note must not call the answer scripted");
  ok(l !== s, "the note actually differs between live and scripted");
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

  const tabbables = () => page.evaluate(() => {
    const sel = "a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), " +
                "textarea:not([disabled]), summary, [tabindex]:not([tabindex='-1'])";
    return [...document.querySelectorAll(sel)]
      .filter((e) => e.offsetParent !== null || getComputedStyle(e).position === "fixed")
      .map((e) => e.id || e.tagName.toLowerCase() + "." + (e.className || "").split(" ")[0]);
  });

  const closed = await page.$eval("#rail-toggle", (e) => e.getAttribute("aria-expanded"));
  eq(closed, "false", "the drawer starts collapsed on a phone, and SAYS so");
  eq(await page.$eval("#rail-scroll", (e) => getComputedStyle(e).display), "none",
     "a collapsed rail is display:none — anything else leaves invisible tab stops");
  // Controls that are genuinely INSIDE the rail, one from each of its four groups.
  const RAIL_ONLY = ["center-btn", "tts-base", "stt-base", "bus-host", "rec-toggle"];
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
  await new Promise((r) => setTimeout(r, 300));
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
 * 5. REDUCED MOTION — the chrome holds still, and the control proves it would not
 * ======================================================================= */
{
  // Infinite CSS animations actually RUNNING (the ALIVE lamp pulses on every load).
  const looping = (page) => page.evaluate(() => document.getAnimations()
    .filter((a) => a.playState === "running" && a.effect &&
                   a.effect.getComputedTiming().iterations === Infinity)
    .map((a) => (a.effect.target && (a.effect.target.id || a.effect.target.className)) + ":" + a.animationName));
  const plain = await open();
  const base = await looping(plain.page);
  ok(base.length > 0, `CONTROL: without the preference something really does loop (${JSON.stringify(base)})`);
  await plain.page.close();

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

await browser.close();
srv.close();
finish(LABEL, { fails, count });
