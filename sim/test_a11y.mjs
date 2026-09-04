/* test_a11y.mjs — the Sim page as a screen reader and a keyboard actually meet it.
 *
 * WHAT THIS SUITE IS FOR. https://moxie.mattvalancy.com/sim is a demo whose subject is a
 * robot built for children, including children with communication differences. A page
 * about that robot that a screen reader cannot navigate is a failure on the merits, not
 * an audit line — so this suite asserts the page's accessibility semantics the way the
 * other browser suites assert layout: in a real Chrome, against the real files.
 *
 * HOW IT ASSERTS, and why it matters. Every check here is an IDENTITY check, never a
 * count. "Nine controls are unnamed" is a number that goes green the moment somebody
 * deletes a control; "the slider for `Head tilt (nod)` is named `Head tilt (nod), motor 4`"
 * can only go green by being true. So the name assertions walk a table of
 * selector -> exact expected accessible name and read each one out of Chrome's own
 * accessibility tree (`page.accessibility.snapshot({ root })`), and the sweep for
 * unnamed controls REPORTS the offenders rather than their number.
 *
 * MEASURED BASELINE (live site, 2026-09-04, Chrome headless): nine interactive nodes had
 * an empty accessible name — the seven motor sliders, `#led-color` and `#qr-kind` — the
 * comms log was in no live region, and there was no <noscript> anywhere in the bundle.
 *
 * ZERO GATEWAY SPEND. `/api/chat`, `/api/speech` and `/api/transcribe` are aborted by the
 * request interceptor and the suite FAILS if the page ever asked for one. `/api/health`
 * is fulfilled locally, which is also how the live-mode copy branch is exercised without
 * a backend.
 *
 * NOT WIRED INTO CI YET — registered in sim/tests/test_ci_test_coverage.py::KNOWN_UNRUN
 * (a concurrent pass owns sim/ci/ci.yml). Run it directly:
 *     PUPPETEER_PATH=~/Code/valancy-resume node sim/test_a11y.mjs
 */
import { requireBrowser, serveWeb, makeChecks, finish } from "./browser_harness.mjs";

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

const browser = await puppeteer.launch({
  executablePath: chrome, headless: "new",
  args: ["--no-sandbox", "--use-gl=swiftshader", "--enable-unsafe-swiftshader",
         "--autoplay-policy=no-user-gesture-required"],
});

/**
 * A loaded /sim.html.
 * @param {{width?:number,height?:number,health?:string|null,reducedMotion?:boolean}} o
 *   `health` non-null fulfils GET /api/health with that body (mode.js -> live).
 */
async function open(o = {}) {
  const page = await browser.newPage();
  await page.setViewport({ width: o.width || 1440, height: o.height || 900 });
  const spent = [];
  await page.setRequestInterception(true);
  page.on("request", (r) => {
    const u = r.url();
    if (SPENDY.test(u)) { spent.push(u); return r.abort(); }          // never spend
    if (o.health != null && /\/api\/health\b/.test(u))
      return r.respond({ status: 200, contentType: "application/json", body: o.health });
    return r.continue();
  });
  if (o.reducedMotion)
    await page.emulateMediaFeatures([{ name: "prefers-reduced-motion", value: "reduce" }]);
  await page.goto(srv.url + "/sim.html", { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => !!window.moxie, { timeout: 30000 });
  await new Promise((r) => setTimeout(r, 2500));       // sidecar probe + first mode render
  return { page, spent };
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
  const { page, spent } = await open();

  const bare = await unnamedControls(page);
  ok(bare.length === 0, `interactive controls with NO accessible name: [${bare.join(", ")}]`);

  // The seven motor sliders, in panel order. moxie.js writes the joint names into a
  // <label> that labels nothing (no `for`, and the <input> is its SIBLING) — this asserts
  // the names the HUD glue copies across, not that "seven sliders have some name".
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
  await page.close();
}

/* ==========================================================================
 * 2. THE LIVE REGION — and what it deliberately does NOT announce
 * ======================================================================= */
{
  const { page, spent } = await open();

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
   * Moxie says an unprompted quip every 11-24 s. Those go to #bubble, never to the log.
   * Two assertions, because either alone can be satisfied by accident: the bubble is in
   * no live region (structure), AND driving five real quips leaves the log's CONTENT
   * byte-identical while the bubble's content changes (behaviour). Identity, not counts —
   * "the log did not grow" would also pass if ambient were broken and said nothing. */
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
  await page.close();
}

/* ==========================================================================
 * 3. FINDING 2 — the Voice panel must describe the button beside it
 * ======================================================================= */
{
  // (a) scripted / no backend: typing reaches a SCRIPTED Moxie, not the browser's voice.
  const { page, spent } = await open();
  const s = await textOf(page, "#voice-note");
  const btn = await page.$eval("#speech-btn", (e) => e.textContent.trim());
  eq(btn, "Ask", "with no Piper the Say button is the typed turn");
  ok(/press Ask/i.test(s || ""), `scripted note names the Ask button — got ${JSON.stringify(s)}`);
  ok(/scripted/i.test(s || ""), `scripted note says the answer is scripted — got ${JSON.stringify(s)}`);
  ok(s !== null && !/browser's voice|browser&#39;s voice|browser’s voice/i.test(s),
     `scripted note must not still claim free text uses the browser's voice — got ${JSON.stringify(s)}`);
  ok(s !== null && !/in her own voice/i.test(s), "scripted note must not promise a live voice");
  eq(spent.length, 0, "no spend while reading the scripted copy");
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
  await live.page.close();
}

/* ==========================================================================
 * 4. KEYBOARD — the rail drawer, its announced state, and no phantom tab stops
 * ======================================================================= */
{
  const { page } = await open({ width: 390, height: 780 });

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
  const shut = await tabbables();
  ok(!shut.includes("transcript") && !shut.includes("speech-input"),
     `nothing inside the collapsed rail is tabbable — got ${JSON.stringify(shut)}`);
  ok(shut.includes("rail-toggle"), "the toggle itself is reachable");

  await page.click("#rail-toggle");
  await new Promise((r) => setTimeout(r, 300));
  eq(await page.$eval("#rail-toggle", (e) => e.getAttribute("aria-expanded")), "true",
     "aria-expanded flips when the drawer opens");
  const open2 = await tabbables();
  ok(open2.includes("speech-input") && open2.includes("transcript"),
     `the opened rail's controls are reachable — got ${JSON.stringify(open2.slice(0, 12))}`);
  // …and in DOM order the toggle comes before what it controls.
  ok(open2.indexOf("rail-toggle") < open2.indexOf("speech-input"),
     "focus reaches the toggle before the panel it discloses");
  eq(await page.$eval("#rail-toggle", (e) => e.getAttribute("aria-controls")), "rail-scroll",
     "aria-controls names the disclosed region");

  await page.close();
}

await browser.close();
srv.close();
finish(LABEL, { fails, count });
