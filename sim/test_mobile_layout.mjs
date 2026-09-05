/* test_mobile_layout.mjs — on a phone, is the control under your thumb the one you meant?
 *
 * THE DEFECT THIS FILE EXISTS FOR (measured in Chrome against the live site, 2026-09-03,
 * 375x667 with touch emulation):
 *
 *     #rail-toggle           357x48 at y=610, visible, pointer-events:auto
 *     elementFromPoint(centre of #rail-toggle) -> div#env-banner
 *     tap()      -> refused, element obscured
 *     force-click-> aria-expanded STAYS false (the hit landed on the banner)
 *     JS .click()-> works, drawer opens, mic records
 *
 * `#env-banner` is `position: fixed; bottom: …; z-index: 30` and stretches to
 * `left:10px; right:10px` on phones — landing exactly on top of the bottom-anchored rail
 * handle. On the demo's most likely device, "open the controls" did nothing, with no
 * feedback of any kind, until the visitor dismissed a notice that never said it was in the
 * way. Everything about the toggle looked fine: it was visible, sized, unclipped, not
 * `display:none`, `pointer-events:auto`. **A visibility check could not have caught this.**
 * `document.elementFromPoint()` is the assertion that could, so that is the assertion here.
 *
 * IT HAS TEETH, and block 3 is why: it forces `--eb-lift` back to 0 (the pre-fix geometry)
 * and requires the collision to REAPPEAR. A layout test that only shows the fixed state is
 * green is indistinguishable from one whose selector silently matches nothing.
 *
 *   node sim/test_mobile_layout.mjs
 */
import { requireBrowser, serveWeb, makeChecks, finish } from "./browser_harness.mjs";

const LABEL = "mobile-layout test";
const { puppeteer, chrome } = await requireBrowser(LABEL);
const { fails, ok, eq, count } = makeChecks();

const site = await serveWeb();

/* The banner only renders on a NON-local host (env.js), which is the deployment the
 * collision was measured on, so the phone viewports are driven against a mapped hostname. */
const HOSTED = `http://moxie.hosted.test:${site.port}/sim.html`;

const PHONES = [
  ["iPhone SE  360x640", 360, 640],
  ["iPhone 8   375x667", 375, 667],
  ["Pixel 5    393x851", 393, 851],
  ["iPhone XR  414x896", 414, 896],
];

const browser = await puppeteer.launch({
  executablePath: chrome, headless: "new",
  args: ["--no-sandbox", "--use-gl=swiftshader", "--enable-unsafe-swiftshader",
         `--host-resolver-rules=MAP moxie.hosted.test 127.0.0.1:${site.port}`],
});

/**
 * Who would actually receive a tap at the centre of `sel`?
 *
 * Returns the hit element's own identity AND whether it is `sel` or something inside it —
 * a tap that lands on the `<span class="tick">` inside the button is a tap on the button,
 * and a test that demanded strict identity would fail on a correct page.
 */
const hitTest = (sel) => {
  const el = document.querySelector(sel);
  if (!el) return { found: false };
  const r = el.getBoundingClientRect();
  if (r.width <= 0 || r.height <= 0) return { found: true, sized: false };
  const hit = document.elementFromPoint(Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2));
  const id = hit ? (hit.id ? "#" + hit.id : hit.tagName.toLowerCase() + "." + (hit.className || "")) : "null";
  return {
    found: true, sized: true, w: Math.round(r.width), h: Math.round(r.height), y: Math.round(r.top),
    self: !!hit && (hit === el || el.contains(hit)),
    hit: id,
  };
};

async function load(w, h) {
  const page = await browser.newPage();
  await page.setViewport({ width: w, height: h, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
  await page.setRequestInterception(true);
  page.on("request", (r) => {
    if (r.isInterceptResolutionHandled()) return;
    // `degraded` keeps today's copy AND the banner, and fires exactly one request — the
    // state the collision was measured in. No live turn is reachable from this suite.
    if (/\/api\/health\b/.test(r.url()))
      return r.respond({ status: 200, contentType: "application/json",
                         body: JSON.stringify({ ok: false, reason: "gateway_not_configured", mode: "degraded" }) });
    if (/:808[12]\//.test(r.url())) return r.abort("connectionrefused");
    return r.continue();
  });
  await page.goto(HOSTED, { waitUntil: "domcontentloaded", timeout: 20000 });
  await page.waitForFunction("!!document.getElementById('env-banner')", { timeout: 10000 }).catch(() => {});
  await new Promise((r) => setTimeout(r, 1200));   // mode probe + the lift measurement
  return page;
}

/**
 * The same page, but with the BOT CONTROL ARMED and Cloudflare's script stubbed.
 *
 * `/api/health` publishes a sitekey (which is the browser's only source of one) and the
 * request for `challenges.cloudflare.com/turnstile/v0/api.js` is answered with a fake
 * `window.turnstile` whose `render()` injects a box of the size Turnstile's own
 * `size: "flexible"` produces — min-width 300px, height 65px. That is the CHALLENGED
 * visitor, which is the only state in which the widget occupies any space at all, and
 * therefore the only state in which it can be in the way of anything.
 *
 * A REAL SITEKEY IS NOT NEEDED AND MUST NOT BE USED: no challenge is solved here and no
 * network is touched — the interceptor answers before the request leaves.
 */
async function loadChallenged(w, h) {
  const page = await browser.newPage();
  await page.setViewport({ width: w, height: h, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
  await page.setRequestInterception(true);
  page.on("request", (r) => {
    if (r.isInterceptResolutionHandled()) return;
    const u = r.url();
    if (/\/api\/health\b/.test(u)) {
      return r.respond({ status: 200, contentType: "application/json",
                         body: JSON.stringify({ ok: true, reason: null, mode: "live",
                                                turnstile: "1x00000000000000000000BB",
                                                voice: false, ears: false }) });
    }
    if (/^https:\/\/challenges\.cloudflare\.com\//.test(u)) {
      return r.respond({ status: 200, contentType: "text/javascript",
                         headers: { "Access-Control-Allow-Origin": "*" },
                         body: `window.turnstile = {
                           render: function (box) {
                             var d = document.createElement("div");
                             d.id = "fake-cf-widget";
                             d.setAttribute("style",
                               "min-width:300px;width:300px;height:65px;background:#345");
                             box.appendChild(d);
                             return "w1";
                           },
                           reset: function () {}, execute: function () {},
                           getResponse: function () { return ""; },
                         };` });
    }
    if (/:808[12]\//.test(u)) return r.abort("connectionrefused");
    return r.continue();
  });
  await page.goto(HOSTED, { waitUntil: "domcontentloaded", timeout: 20000 });
  await page.waitForFunction("!!document.getElementById('fake-cf-widget')", { timeout: 10000 })
    .catch(() => {});
  await new Promise((r) => setTimeout(r, 800));
  return page;
}

try {
  for (const [label, w, h] of PHONES) {
    const page = await load(w, h);

    /* --- 1. the banner is up, and it is NOT on top of the handle ---------- */
    const shown = await page.evaluate(() => !!document.getElementById("env-banner"));
    ok(shown, `${label}: the hosted banner is showing (the fixture the collision needs)`);

    const closed = await page.evaluate(hitTest, "#rail-toggle");
    ok(closed.found && closed.sized, `${label}: #rail-toggle is laid out (${closed.w}x${closed.h})`);
    ok(closed.self,
       `${label}: a tap at the centre of #rail-toggle reaches the TOGGLE, not ${closed.hit}`);

    const x = await page.evaluate(hitTest, "#env-banner .eb-x");
    ok(x.self, `${label}: …and the banner's own dismiss X is still hittable (got ${x.hit})`);

    const alive = await page.evaluate(hitTest, "#alive-toggle");
    ok(alive.self, `${label}: the topbar ALIVE toggle is hittable (got ${alive.hit})`);

    /* --- 2. open the drawer FOR REAL and drive a control ------------------ */
    // `page.tap()` refuses on an obscured element, which is the whole point: before the
    // fix this line threw. It is the strongest form of the assertion above.
    await page.tap("#rail-toggle");
    await new Promise((r) => setTimeout(r, 600));
    const open = await page.evaluate(() => ({
      expanded: document.getElementById("rail-toggle").getAttribute("aria-expanded"),
      railShown: !!document.getElementById("rail-scroll").getBoundingClientRect().height,
    }));
    eq(open.expanded, "true", `${label}: tapping the handle really opens the drawer`);
    ok(open.railShown, `${label}: …and the rail has height`);

    // With the drawer open the panel is taller, so the banner has to have moved again.
    const reopened = await page.evaluate(hitTest, "#rail-toggle");
    ok(reopened.self,
       `${label}: the handle is STILL reachable with the drawer open (got ${reopened.hit})`);

    /* Scroll a real control into the rail's view and check the same way. This used to
     * drive `#mic-btn`; the mic moved out of the drawer and into the page's composer on
     * 2026-09-05 (block 6 hit-tests it there, on a page nobody has tapped), so the
     * control driven here is one that is still genuinely INSIDE the drawer. */
    const ctrl = await page.evaluate(() => {
      const b = document.getElementById("center-btn");
      b.scrollIntoView({ block: "center" });
      const r = b.getBoundingClientRect();
      const hit = document.elementFromPoint(Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2));
      return { self: !!hit && (hit === b || b.contains(hit)),
               hit: hit ? (hit.id ? "#" + hit.id : hit.tagName) : "null",
               w: Math.round(r.width), h: Math.round(r.height) };
    });
    ok(ctrl.self, `${label}: #center-btn inside the open drawer is hittable (got ${ctrl.hit})`);
    ok(ctrl.h >= 40, `${label}: …at a real touch size (${ctrl.w}x${ctrl.h})`);

    /* --- 3. no horizontal overflow, at every one of these widths ---------- */
    const hscroll = await page.evaluate(() =>
      document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
    eq(hscroll, false, `${label}: no horizontal page scroll`);

    await page.close();
  }

  /* =====================================================================
   * 4. THE TURNSTILE CHALLENGE IS NOT ON TOP OF THE CONTROLS EITHER.
   *
   * THE DEFECT THIS BLOCK EXISTS FOR — the same one as block 1, by a different element.
   * The first version of `sim/web/turnstile.js` put its widget holder at
   * `position: fixed; left: 50%; bottom: 16px; z-index: 70`, which on a phone is exactly
   * where `#rail-toggle` lives. Measured at 393x851: the holder at (47,770) 300x65 and the
   * toggle at (9,794) 375x48, with `elementFromPoint()` at the toggle's centre returning
   * `div#fake-cf-widget`. So a visitor Cloudflare decided to challenge could not open the
   * drawer that contains the text box — the one control that matters — and the widget was
   * two z-index layers above the banner that had needed `--eb-lift` for the identical
   * reason.
   *
   * It could not be caught here before because this suite's `/api/health` published no
   * sitekey, so no holder was ever created. `loadChallenged()` publishes one.
   *
   * BOTH DIRECTIONS ARE ASSERTED, and the second is what keeps the first honest: the
   * controls must own their own centres, AND THE CHALLENGE ITSELF MUST BE HITTABLE. Moving
   * the widget somewhere harmless by making it unclickable would be a worse bug than the
   * one being fixed — an unsolvable challenge is a page that can never send anything.
   * =================================================================== */
  for (const [label, w, h] of [PHONES[1], PHONES[2]]) {
    const page = await loadChallenged(w, h);

    const drew = await page.evaluate(() => {
      const d = document.getElementById("fake-cf-widget");
      const holder = document.getElementById("turnstile-holder");
      if (!d || !holder) return { drew: false, holder: !!holder };
      const r = d.getBoundingClientRect();
      return { drew: r.width > 0 && r.height > 0, holder: true,
               w: Math.round(r.width), h: Math.round(r.height),
               y: Math.round(r.top), x: Math.round(r.left),
               vh: window.innerHeight,
               pe: getComputedStyle(holder).pointerEvents };
    });
    ok(drew.holder, `${label}: the widget holder exists once a sitekey is published`);
    ok(drew.drew, `${label}: …and a challenge is drawn in it (${drew.w}x${drew.h} at ${drew.y})`);
    eq(drew.pe, "none",
       `${label}: the holder LAYER is pointer-events:none — an empty one cannot swallow a tap`);

    // The whole point: the bottom-anchored controls still own their own centres.
    const toggle = await page.evaluate(hitTest, "#rail-toggle");
    ok(toggle.self,
       `${label}: with a challenge on screen, a tap at #rail-toggle STILL reaches the toggle ` +
       `(got ${toggle.hit})`);
    // ...and `page.tap()` refuses on an obscured element, which is the strongest form of it.
    await page.tap("#rail-toggle");
    await new Promise((r) => setTimeout(r, 600));
    eq(await page.evaluate(() => document.getElementById("rail-toggle").getAttribute("aria-expanded")),
       "true", `${label}: …and tapping it really opens the drawer, challenge and all`);

    // The challenge is CLICKABLE, which is the other half of being usable.
    const widget = await page.evaluate(hitTest, "#fake-cf-widget");
    ok(widget.self,
       `${label}: …while the challenge itself is hittable, not decoration (got ${widget.hit})`);

    // It is in the middle of the viewport, which is where no control lives at any width.
    ok(drew.y > drew.vh * 0.25 && drew.y + drew.h < drew.vh * 0.75,
       `${label}: …centred vertically, clear of both strips (y=${drew.y} h=${drew.h} vh=${drew.vh})`);

    // The composer is bottom-anchored at every width since 2026-09-05, so it is the
    // control a `bottom: 16px` widget would land on now. Both are asserted.
    const box = await page.evaluate(hitTest, "#speech-input");
    ok(box.self,
       `${label}: …and the message box owns its own centre too (got ${box.hit})`);

    /* TEETH, in the same block: put the pre-fix geometry back and require the collision to
     * return. Without this the assertions above are equally consistent with "the fix works"
     * and "the fake widget has no size". It is aimed at `#speech-input` rather than
     * `#rail-toggle` because that is where the pre-fix holder now lands: the handle moved
     * up the page when the composer took the bottom row, so a test still aimed at the
     * handle would report "no collision" and quietly stop having teeth. */
    const broken = await page.evaluate((fn) => {
      const holder = document.getElementById("turnstile-holder");
      holder.setAttribute("style",
        "position:fixed;left:50%;bottom:16px;transform:translateX(-50%);z-index:70;" +
        "display:flex;justify-content:center;pointer-events:auto");
      // eslint-disable-next-line no-eval
      return (0, eval)("(" + fn + ")")("#speech-input");
    }, hitTest.toString());
    eq(broken.self, false,
       `${label}: teeth — with the holder back at bottom:16px the collision RETURNS ` +
       `(hit ${broken.hit}); if this passes, nothing above is being measured`);
    ok(/fake-cf-widget|turnstile-holder/.test(broken.hit),
       `${label}: teeth — …and it is the TURNSTILE LAYER that swallows it (got ${broken.hit}) — ` +
       "either the holder or the challenge inside it, which is why the shipped holder is " +
       "pointer-events:none AND is not down here");

    await page.close();
  }

  /* =====================================================================
   * 5. TEETH. Put the pre-fix geometry back and require the bug to return.
   *
   * Without this block, a green suite would be equally consistent with "the fix works"
   * and "the selector matched nothing" — and this repo has shipped that mistake before.
   * =================================================================== */
  {
    const page = await load(375, 667);
    const fixed = await page.evaluate(hitTest, "#rail-toggle");
    ok(fixed.self, "teeth: with the lift applied the toggle owns its own centre");
    // ...and so does the box that is actually bottom-anchored now.
    const boxFixed = await page.evaluate(hitTest, "#speech-input");
    ok(boxFixed.self, "teeth: …and so does the message box below it");
    const lift = await page.evaluate(() =>
      getComputedStyle(document.documentElement).getPropertyValue("--eb-lift").trim());
    ok(/^\d+px$/.test(lift) && parseInt(lift, 10) > 0,
       `teeth: env.js measured a real lift, not a constant (--eb-lift: ${JSON.stringify(lift)})`);

    /* Aimed at `#speech-input`: `#chat-dock` is the bottom row of the HUD grid since
     * 2026-09-05, so the rail handle is no longer the lowest thing on the page and a
     * `--eb-lift: 0` banner reaches the composer instead. A teeth block still aimed at
     * the handle would find no collision and report green while measuring nothing. */
    const broken = await page.evaluate((fn) => {
      document.documentElement.style.setProperty("--eb-lift", "0px");
      // eslint-disable-next-line no-eval
      return (0, eval)("(" + fn + ")")("#speech-input");
    }, hitTest.toString());
    eq(broken.self, false,
       `teeth: with --eb-lift back at 0 the collision RETURNS (hit ${broken.hit}) — ` +
       "if this passes, the assertion above is not measuring anything");
    /* The banner or anything INSIDE it. Aimed at the composer this now resolves to
     * `span.eb-text` — the banner's own copy, which is a wider box than the dismiss row
     * the rail handle used to collide with. A tap swallowed by the banner's text is
     * swallowed by the banner; demanding the exact `div#env-banner` would be asserting
     * which child happened to be under one particular coordinate. */
    ok(/env-banner|\beb-/.test(broken.hit),
       `teeth: …and it is the banner LAYER that swallows the tap (got ${broken.hit})`);
    await page.close();
  }

  /* =====================================================================
   * 6. THE COMPOSER — REACHABLE ON THE FIRST PAINTED FRAME.
   *
   * THE DEFECT THIS BLOCK EXISTS FOR (measured against
   * `https://moxie.mattvalancy.com/sim` in a fresh incognito profile, real iOS UA,
   * 390x844, and written up in docs/architecture/backlog/mobile-first-visit.md):
   *
   *     #speech-input on load            0 x 0        (present in the DOM, inside <aside id="panel">)
   *     after tapping CONTROLS           262x40 at y = 2095   (~2000 px below an 844 px fold)
   *     after scrollIntoView             y = 663, and the turn COMPLETES normally
   *
   * So the turn always worked. It was UNREACHABLE — buried at the bottom of a
   * scrolling engineering drawer behind a button labelled `CONTROLS`, on a page whose
   * six visible controls (Hub, ALIVE, GITHUB, CONTROLS, Run it locally, X) said nothing
   * about talking to Moxie at all. She speaks unprompted at ~7 s, so a visitor heard her
   * and had no visible way to answer.
   *
   * WHY THE ASSERTION IS A RECT AND A HIT TEST, NEVER `element.exists`. That distinction
   * IS the finding: `document.getElementById("speech-input")` was truthy the entire time
   * the box was 0x0 and two thousand pixels below the fold. So every check below asks
   * three separate questions and needs all three: does the box have a non-zero rect, does
   * that rect lie INSIDE the initial viewport, and does `elementFromPoint()` at its centre
   * come back as the box itself (the banner and the Turnstile challenge have each already
   * swallowed a bottom-anchored control on this page — blocks 1 and 4 above).
   *
   * AND IT IS MEASURED ON A COLD LOAD: no `page.tap()`, no `scrollIntoView`, no drawer.
   * `railShut` is re-read after every measurement so a check can never be satisfied by a
   * rail that quietly opened itself — "the composer is reachable" and "the rail is
   * required" must not both be true.
   * =================================================================== */

  /**
   * Is `sel` reachable by a visitor who has done NOTHING but load the page?
   *
   * Deliberately returns the raw numbers as well as the verdicts: a failure message that
   * says `262x40 at y=2095 of 844` is the measurement this whole slice exists to fix,
   * and a bare `false` would make the next reader take the same production screenshots
   * again.
   */
  const reach = (sel) => {
    const el = document.querySelector(sel);
    if (!el) return { found: false, sel };
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    const vw = window.innerWidth, vh = window.innerHeight;
    const cx = Math.round(r.left + r.width / 2), cy = Math.round(r.top + r.height / 2);
    const hit = (r.width > 0 && r.height > 0) ? document.elementFromPoint(cx, cy) : null;
    return {
      found: true, sel,
      w: Math.round(r.width), h: Math.round(r.height),
      top: Math.round(r.top), bottom: Math.round(r.bottom),
      left: Math.round(r.left), right: Math.round(r.right),
      vw, vh,
      shown: cs.display !== "none" && cs.visibility !== "hidden" && r.width > 0 && r.height > 0,
      // `+0.5` because a fractional layout can put `bottom` a hair past an integer height.
      inFold: r.width > 0 && r.height > 0 &&
              r.top >= 0 && r.bottom <= vh + 0.5 && r.left >= -0.5 && r.right <= vw + 0.5,
      self: !!hit && (hit === el || el.contains(hit)),
      hit: hit ? (hit.id ? "#" + hit.id : hit.tagName.toLowerCase()) : "null",
      text: (el.textContent || "").replace(/\s+/g, " ").trim().slice(0, 90),
      scrollY: window.scrollY,
    };
  };

  /** The whole verdict for one control, in one line, with the numbers in the message. */
  function reachable(label, m, what) {
    ok(m.found, `${label}: ${what} (${m.sel}) exists at all`);
    ok(m.shown,
       `${label}: ${what} has a real box — got ${m.w}x${m.h} (inside a collapsed rail it is 0x0)`);
    ok(m.inFold,
       `${label}: ${what} is INSIDE the first viewport — y=${m.top}..${m.bottom} of ${m.vh} ` +
       `(the production defect measured 262x40 at y=2095 of 844)`);
    ok(m.self, `${label}: …and a tap at its centre reaches it, not ${m.hit}`);
    eq(m.scrollY, 0, `${label}: …with the page never scrolled (scrollY=${m.scrollY})`);
  }

  for (const [label, w, h] of [["iPhone 12  390x844", 390, 844], ...PHONES]) {
    const page = await load(w, h);

    // Nothing has been tapped. Say so out loud, and keep saying it.
    const railShut = () => page.evaluate(() => ({
      expanded: document.getElementById("rail-toggle").getAttribute("aria-expanded"),
      scroll: getComputedStyle(document.getElementById("rail-scroll")).display,
    }));
    const cold = await railShut();
    eq(cold.expanded, "false", `${label}: the engineering rail is CLOSED on a cold load`);
    eq(cold.scroll, "none", `${label}: …and its contents are display:none, not merely off-screen`);

    /* ---- AC1: the text field and the send button, on first paint ---- */
    reachable(label, await page.evaluate(reach, "#speech-input"), "the message box");
    reachable(label, await page.evaluate(reach, "#speech-btn"), "the send button");

    /* ---- AC4: the mic is BESIDE send, not in a panel three screens away ---- */
    const mic = await page.evaluate(reach, "#mic-btn");
    reachable(label, mic, "the mic button");
    const beside = await page.evaluate(() => {
      const m = document.getElementById("mic-btn"), s = document.getElementById("speech-btn");
      const i = document.getElementById("speech-input");
      if (!m || !s || !i) return { ok: false };
      const mr = m.getBoundingClientRect(), sr = s.getBoundingClientRect(), ir = i.getBoundingClientRect();
      return {
        ok: true,
        sameRow: m.parentElement === s.parentElement && m.parentElement === i.parentElement,
        // "beside", measured: their vertical centres agree and the horizontal gap is a
        // gutter, not a layout away.
        gap: Math.round(Math.min(Math.abs(sr.left - mr.right), Math.abs(mr.left - sr.right))),
        dy: Math.round(Math.abs((mr.top + mr.height / 2) - (sr.top + sr.height / 2))),
      };
    });
    ok(beside.ok && beside.sameRow,
       `${label}: the mic, the box and send are ONE row — the same parent, not three panels`);
    ok(beside.gap >= 0 && beside.gap <= 24,
       `${label}: the mic sits beside send — ${beside.gap}px between them`);
    ok(beside.dy <= 6, `${label}: …on the same line (${beside.dy}px of vertical drift)`);

    /* ---- AC2: something on first paint TELLS a stranger they can talk ----
     * The measured gap was not only geometric: of the six controls a phone visitor could
     * see, not one named the action. A placeholder inside a box is not enough on its own
     * — it disappears the moment anything is typed and it is not read as page copy — so
     * the affordance asserted here is a real, visible element with real words in it. */
    const cue = await page.evaluate(reach, "#chat-cue");
    reachable(label, cue, "the 'talk to Moxie' cue");
    ok(/talk to moxie/i.test(cue.text || ""),
       `${label}: …and it names the action in plain language — got ${JSON.stringify(cue.text)}`);
    const ph = await page.evaluate(() =>
      (document.getElementById("speech-input") || {}).placeholder || "");
    ok(/moxie/i.test(ph), `${label}: the box's own placeholder names her too — got ${JSON.stringify(ph)}`);

    const after = await railShut();
    eq(after.expanded, "false",
       `${label}: NOTHING above opened the rail — every measurement was on the cold page`);

    await page.close();
  }

  /* =====================================================================
   * 7. THE RAIL IS OPTIONAL — a whole turn without it, and it still works.
   *
   * Two halves, and the second is what stops "optional" from becoming "removed".
   * The turn here is SCRIPTED: `/api/health` answers `degraded` (see `load()`), so
   * `cloud-transport.js` delegates to `stub.js` and not one request leaves the page.
   * =================================================================== */
  {
    const page = await load(390, 844);

    // ---- (a) a full typed turn with the drawer NEVER opened ----
    await page.evaluate(() => { document.getElementById("speech-input").value = "hello moxie"; });
    await page.tap("#speech-btn");            // tap(), so an obscured button still fails here
    await page.waitForFunction(
      () => [...document.querySelectorAll("#transcript .turn")].some((r) => /\buser\b/.test(r.className)),
      { timeout: 15000 });
    await page.waitForSelector("#transcript .turn.moxie", { timeout: 15000 });
    const turn = await page.evaluate(() => ({
      rows: [...document.querySelectorAll("#transcript .turn")].map((r) => ({
        who: r.className, msg: (r.querySelector(".msg") || r).textContent.trim() })),
      expanded: document.getElementById("rail-toggle").getAttribute("aria-expanded"),
      railDisplay: getComputedStyle(document.getElementById("rail-scroll")).display,
      inputCleared: document.getElementById("speech-input").value === "",
    }));
    ok(turn.rows.some((r) => /\buser\b/.test(r.who) && r.msg === "hello moxie"),
       `a typed line lands in the log verbatim — got ${JSON.stringify(turn.rows)}`);
    ok(turn.rows.some((r) => /\bmoxie\b/.test(r.who) && r.msg.length > 0),
       `…and Moxie answers it — got ${JSON.stringify(turn.rows)}`);
    ok(turn.inputCleared, "…and the box empties, so the next line does not double up");
    eq(turn.expanded, "false", "THE WHOLE TURN COMPLETED WITH THE RAIL NEVER OPENED");
    eq(turn.railDisplay, "none", "…and the rail was display:none for all of it");
    // The conversation is where the visitor is looking, not in a drawer.
    const log = await page.evaluate(reach, "#transcript");
    ok(log.inFold, `the comms log is in the first viewport too — y=${log.top}..${log.bottom} of ${log.vh}`);

    // ---- (b) OPTIONAL IS NOT REMOVED: the rail still opens and still works ----
    await page.tap("#rail-toggle");
    await new Promise((r) => setTimeout(r, 600));
    const opened = await page.evaluate(() => ({
      expanded: document.getElementById("rail-toggle").getAttribute("aria-expanded"),
      railH: Math.round(document.getElementById("rail-scroll").getBoundingClientRect().height),
      groups: document.querySelectorAll("#rail-scroll .group").length,
    }));
    eq(opened.expanded, "true", "the rail still opens on demand");
    ok(opened.railH > 0 && opened.groups >= 4,
       `…with all its groups intact (${opened.groups} groups, ${opened.railH}px)`);
    /* A control inside it still does its job. `#axes-on` is chosen because its effect is
     * a DETERMINISTIC DOM change (`#axis-legend` loses `hidden`) rather than an eased
     * animation — a motor assertion would race the liveness loop and teach the next
     * reader to widen a timeout. */
    const worked = await page.evaluate(async () => {
      const cb = document.getElementById("axes-on");
      const lg = document.getElementById("axis-legend");
      if (!cb || !lg) return { hit: "missing" };
      cb.scrollIntoView({ block: "center" });
      const r = cb.getBoundingClientRect();
      const hit = document.elementFromPoint(Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2));
      if (!hit || !(hit === cb || cb.contains(hit))) return { hit: hit ? hit.id || hit.tagName : "null" };
      const was = lg.hidden;
      cb.click();
      await new Promise((s) => setTimeout(s, 200));
      return { hit: "self", was, now: lg.hidden };
    });
    eq(worked.hit, "self", `…and a control inside it is hittable (got ${worked.hit})`);
    ok(worked.was === true && worked.now === false,
       "…and really works — ticking 'show axes' revealed the axis legend");

    // ...and the composer did not move out from under the visitor when the rail opened.
    const stillThere = await page.evaluate(reach, "#speech-input");
    ok(stillThere.inFold && stillThere.self,
       `the message box is STILL reachable with the rail open — y=${stillThere.top}..${stillThere.bottom} ` +
       `of ${stillThere.vh}, hit ${stillThere.hit}`);

    /* ---- TEETH. Put the composer back where it was and require the bug to return. ----
     * Without this, everything in blocks 6-7 is equally consistent with "the fix works"
     * and "the selectors match nothing" — a mistake this repo has shipped before. The
     * mutation is the exact pre-2026-09-05 arrangement: the whole dock inside
     * `#rail-scroll`, drawer shut. */
    const broken = await page.evaluate((fn) => {
      document.getElementById("hud").classList.add("rail-closed");
      document.getElementById("rail-scroll").appendChild(document.getElementById("chat-dock"));
      // eslint-disable-next-line no-eval
      return (0, eval)("(" + fn + ")")("#speech-input");
    }, reach.toString());
    eq(broken.inFold, false,
       `teeth — back inside the collapsed rail the box is unreachable again (${broken.w}x${broken.h} ` +
       `at y=${broken.top}); if this passes, nothing in blocks 6-7 is being measured`);
    eq(broken.shown, false,
       `teeth — …and it is 0x0, which is exactly what production measured (${broken.w}x${broken.h})`);

    await page.close();
  }

} catch (e) {
  fails.push("threw: " + (e && e.stack ? e.stack.split("\n").slice(0, 4).join(" / ") : e));
} finally {
  await browser.close().catch(() => {});
  site.close();
}

finish(LABEL, { fails, count });
