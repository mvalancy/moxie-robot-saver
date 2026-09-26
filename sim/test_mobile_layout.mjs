/* test_mobile_layout.mjs — on a phone, is the control under your thumb the one you meant?
 *
 * A control can be visible, sized, unclipped and `pointer-events:auto` and still be
 * untappable because something else sits on top of it (the env banner once covered
 * #rail-toggle; the Turnstile challenge once did too). Only `document.elementFromPoint()`
 * catches that, so that is the assertion here — plus TEETH blocks that restore each
 * pre-fix geometry and require the collision to REAPPEAR, so a selector matching nothing
 * cannot read as green. Block 8 covers the three chat openers and their height budget.
 *
 *   node sim/test_mobile_layout.mjs
 */
import { requireBrowser, serveWeb, makeChecks, finish, pageEyes, launchBrowser }
  from "./browser_harness.mjs";

const LABEL = "mobile-layout test";
const { puppeteer, chrome } = await requireBrowser(LABEL);
const { fails, ok, eq, count } = makeChecks();

/* EYES: a 404'd or throwing page script fails a block instead of timing out silently. */
const EYES = pageEyes(eq);
const eyes = EYES.check;

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

const browser = await launchBrowser(puppeteer, chrome, { hosts: { "moxie.hosted.test": site.port } });

/** Who receives a tap at the centre of `sel`? `self` counts descendants: a tap on the
 *  `<span class="tick">` inside a button is a tap on the button. */
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

/** A phone-sized page on HOSTED; `route(r, url)` may answer a request (truthy = handled). */
async function phonePage(w, h, route) {
  const page = await browser.newPage();
  await page.setViewport({ width: w, height: h, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
  const seen = EYES.watch(page);
  await page.setRequestInterception(true);
  page.on("request", (r) => {
    if (r.isInterceptResolutionHandled()) return;
    const u = r.url();
    if (route(r, u)) return;
    if (/:808[12]\//.test(u)) { seen.aborted.n++; return r.abort("connectionrefused"); }
    return r.continue();
  });
  await page.goto(HOSTED, { waitUntil: "domcontentloaded", timeout: 20000 });
  return page;
}
const health = (r, body) => r.respond({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

async function load(w, h) {
  // `degraded` keeps the banner and fires exactly one request — the state the collision was
  // measured in. No live turn is reachable from this suite.
  const page = await phonePage(w, h, (r, u) => /\/api\/health\b/.test(u) &&
    (health(r, { ok: false, reason: "gateway_not_configured", mode: "degraded" }), true));
  await page.waitForFunction("!!document.getElementById('env-banner')", { timeout: 10000 }).catch(() => {});
  await new Promise((r) => setTimeout(r, 1200));   // mode probe + the lift measurement
  return page;
}

/** The CHALLENGED visitor (the only state in which the widget takes space): `/api/health`
 *  publishes a sitekey and Cloudflare's api.js is a fake whose `render()` injects 300x65. */
async function loadChallenged(w, h) {
  const page = await phonePage(w, h, (r, u) => {
    if (/\/api\/health\b/.test(u))
      return health(r, { ok: true, reason: null, mode: "live", turnstile: "1x00000000000000000000BB",
                         voice: false, ears: false }), true;
    if (/^https:\/\/challenges\.cloudflare\.com\//.test(u))
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
                         };` }), true;
    return false;
  });
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
    // `page.tap()` refuses on an obscured element — the strongest form of the assertion above.
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

    /* Scroll a control that is still genuinely INSIDE the drawer into view and check it the
     * same way (the mic lives in the composer; block 6 hit-tests it there). */
    await page.evaluate(() => document.getElementById("center-btn").scrollIntoView({ block: "center" }));
    const ctrl = await page.evaluate(hitTest, "#center-btn");
    ok(ctrl.self, `${label}: #center-btn inside the open drawer is hittable (got ${ctrl.hit})`);
    ok(ctrl.h >= 40, `${label}: …at a real touch size (${ctrl.w}x${ctrl.h})`);

    /* --- 3. no horizontal overflow, at every one of these widths ---------- */
    const hscroll = await page.evaluate(() =>
      document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
    eq(hscroll, false, `${label}: no horizontal page scroll`);

    eyes(`${label}: hit-testing the phone page`, page);
    await page.close();
  }

  /* =====================================================================
   * 4. THE TURNSTILE CHALLENGE IS NOT ON TOP OF THE CONTROLS EITHER.
   * A bottom-anchored widget holder once covered #rail-toggle, so a challenged visitor could
   * not open the drawer. BOTH DIRECTIONS are asserted: the controls own their centres AND the
   * challenge itself stays hittable — an unsolvable challenge would be a worse bug.
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
    /* `drew.drew &&` is part of the assertion: with no challenge on screen both checks are
     * trivially true (page_teeth_check.py's `turnstilejs-inert` row proved it). */
    ok(drew.drew && toggle.self,
       `${label}: with a challenge ON SCREEN (drawn=${drew.drew}), a tap at #rail-toggle ` +
       `STILL reaches the toggle (got ${toggle.hit})`);
    // ...and `page.tap()` refuses on an obscured element, which is the strongest form of it.
    await page.tap("#rail-toggle");
    await new Promise((r) => setTimeout(r, 600));
    const expanded = await page.evaluate(() =>
      document.getElementById("rail-toggle").getAttribute("aria-expanded"));
    ok(drew.drew && expanded === "true",
       `${label}: …and tapping it really opens the drawer, challenge and all ` +
       `(drawn=${drew.drew}, aria-expanded=${JSON.stringify(expanded)})`);

    // The challenge is CLICKABLE, which is the other half of being usable.
    const widget = await page.evaluate(hitTest, "#fake-cf-widget");
    ok(widget.self,
       `${label}: …while the challenge itself is hittable, not decoration (got ${widget.hit})`);

    /* In the open stage, asserted as CLEARANCE from the chrome strips (clear of the top chrome,
     * not touching the drawer handle) rather than a viewport fraction — turnstile.js centres in
     * the space ABOVE the bottom controls, which moves as #chat-dock grows (block 9). */
    const between = await page.evaluate(() => {
      const cf = document.getElementById("fake-cf-widget");
      const top = document.getElementById("notice") || document.getElementById("topbar");
      const ctl = document.getElementById("rail-toggle");
      if (!cf || !top || !ctl) return { ok: false };
      const a = cf.getBoundingClientRect(), t = top.getBoundingClientRect(),
            c = ctl.getBoundingClientRect();
      return { ok: true, cfTop: Math.round(a.top), cfBottom: Math.round(a.bottom),
               chrome: Math.round(t.bottom),
               ctl: [Math.round(c.top), Math.round(c.bottom)],
               oy: Math.round(Math.max(0, Math.min(a.bottom, c.bottom) - Math.max(a.top, c.top))) };
    });
    ok(between.ok && between.cfTop >= between.chrome && between.oy === 0,
       `${label}: …in the open stage — the challenge (y=${between.cfTop}..${between.cfBottom}) ` +
       `starts below the top chrome (ends ${between.chrome}) and does not touch ` +
       `#rail-toggle (y=${between.ctl}) — ${between.oy}px of bleed`);

    // The composer is bottom-anchored at every width: where a `bottom: 16px` widget lands.
    const box = await page.evaluate(hitTest, "#speech-input");
    ok(box.self,
       `${label}: …and the message box owns its own centre too (got ${box.hit})`);

    /* TEETH: put the pre-fix geometry back and require the collision to return. Aimed at
     * `#speech-input`, which is where the pre-fix holder lands now that the composer is the
     * bottom row. */
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

    eyes(`${label}: the challenged page`, page);
    await page.close();
  }

  /* =====================================================================
   * 5. TEETH. Put the pre-fix geometry back and require the bug to return, so a green suite
   * cannot mean "the selector matched nothing".
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

    /* Aimed at `#speech-input`: the composer is the bottom row, so a `--eb-lift: 0` banner
     * reaches it rather than the rail handle. */
    const broken = await page.evaluate((fn) => {
      document.documentElement.style.setProperty("--eb-lift", "0px");
      // eslint-disable-next-line no-eval
      return (0, eval)("(" + fn + ")")("#speech-input");
    }, hitTest.toString());
    eq(broken.self, false,
       `teeth: with --eb-lift back at 0 the collision RETURNS (hit ${broken.hit}) — ` +
       "if this passes, the assertion above is not measuring anything");
    /* The banner or anything INSIDE it (here `span.eb-text`): a tap swallowed by the banner's
     * text is swallowed by the banner. */
    ok(/env-banner|\beb-/.test(broken.hit),
       `teeth: …and it is the banner LAYER that swallows the tap (got ${broken.hit})`);
    eyes("teeth: the --eb-lift page", page);
    await page.close();
  }

  /* =====================================================================
   * 6. THE COMPOSER — REACHABLE ON THE FIRST PAINTED FRAME.
   * #speech-input once existed in the DOM but was 0x0 on load and ~2000 px below the fold
   * inside the drawer (backlog/mobile-first-visit.md). So never `element.exists`: every check
   * needs a non-zero rect, INSIDE the initial viewport, whose centre hit-tests to itself — on
   * a COLD load (no tap, no scroll, no drawer), with `railShut` re-read after every measure.
   * =================================================================== */

  /** Is `sel` reachable on a cold load? Raw numbers too, for the failure message. */
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
     * A real visible element with real words — a placeholder vanishes on typing and is not
     * read as page copy. */
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

    eyes(`${label}: the cold first-visit page`, page);
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
    /* A control inside it still works. `#axes-on` is chosen for its deterministic DOM effect
     * (`#axis-legend` loses `hidden`); a motor assertion would race the liveness loop. */
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

    /* ---- TEETH. Put the composer back inside `#rail-scroll`, drawer shut, and require the
     * bug to return. */
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

    eyes("the rail-free typed turn", page);
    await page.close();
  }


  /* =====================================================================
   * 8. THE THREE OPENERS — a first turn that costs ONE TAP and no typing.
   * `#chat-openers` (backlog/gamify-the-public-sim.md) must live in the dock and SEND A TURN;
   * the rail's `#speech-chips` look similar but only play shipped audio, so assertions are
   * about where each control lives and what a tap produces. The composer is measured with the
   * openers present, and the teeth inflate them until it really leaves the fold. The turn is
   * SCRIPTED (`/api/health` answers `degraded`); the over-the-wire claim is
   * `sim/test_typed_turn.mjs` block 7's.
   * =================================================================== */
  {
    const L = "iPhone 12  390x844";
    const page = await load(390, 844);

    /* ---- (a) three openers, in the DOCK, on a cold load ---- */
    const openers = await page.evaluate(() => {
      const box = document.getElementById("chat-openers");
      const chips = document.getElementById("speech-chips");
      if (!box) return { found: false, n: 0, labels: [], heights: [] };
      const btns = [...box.querySelectorAll("button.opener")];
      return {
        found: true,
        n: btns.length,
        labels: btns.map((b) => b.textContent.replace(/\s+/g, " ").trim()),
        heights: btns.map((b) => Math.round(b.getBoundingClientRect().height)),
        inDock: !!box.closest("#chat-dock"),
        inRail: !!box.closest("#panel"),
        chipsInDock: !!(chips && chips.closest("#chat-dock")),
        chipsInRail: !!(chips && chips.closest("#panel")),
        boxH: Math.round(box.getBoundingClientRect().height),
        // Rows counted from the buttons' own tops, not from a class or a computed
        // `grid-template`: it is the LAID-OUT arrangement that costs height.
        rows: new Set(btns.map((b) => Math.round(b.getBoundingClientRect().top))).size,
      };
    });
    ok(openers.found, `${L}: #chat-openers exists at all`);
    eq(openers.n, 3, `${L}: three openers, no more and no fewer (got ${openers.n})`);
    eq(JSON.stringify(openers.labels),
       JSON.stringify(["Tell me a silly joke", "What makes you happy?", "Surprise me!"]),
       `${L}: …and they are the three the brief names — got ${JSON.stringify(openers.labels)}`);
    ok(openers.inDock && !openers.inRail,
       `${L}: they sit in #chat-dock, NOT in the engineering rail (dock=${openers.inDock} rail=${openers.inRail})`);
    ok(openers.chipsInRail && !openers.chipsInDock,
       `${L}: …and #speech-chips STAYED in the rail — the shipped-audio chips are a ` +
       `different control and were not repurposed (dock=${openers.chipsInDock} rail=${openers.chipsInRail})`);
    ok(openers.heights.length === 3 && openers.heights.every((h) => h >= 44),
       `${L}: every opener is a 44 px touch target, like the controls beside it — ` +
       `got ${JSON.stringify(openers.heights)}`);
    /* ONE ROW — a height budget, not a style: dock growth comes out of the stage row, so a
     * second row of openers lifted #rail-toggle into the centred Turnstile challenge (block 4
     * went red). */
    ok(openers.rows === 1,
       `${L}: the three openers share ONE row (${openers.rows} row(s), ${openers.boxH}px). ` +
       "Two rows cost ~100px of an 844px phone, all of it taken from the stage, and it " +
       "puts #rail-toggle inside the Turnstile challenge at 375x667 — see block 4");

    for (let i = 1; i <= 3; i++)
      reachable(L, await page.evaluate(reach, `#chat-openers .opener:nth-of-type(${i})`), `opener ${i}`);

    /* ---- (b) …and the composer they sit above did NOT move out of the fold ---- */
    reachable(L, await page.evaluate(reach, "#speech-input"), "the message box, with the openers above it");
    reachable(L, await page.evaluate(reach, "#speech-btn"), "the send button, with the openers above it");
    const doc = await page.evaluate(() => ({
      sh: document.documentElement.scrollHeight, ch: document.documentElement.clientHeight,
      sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth,
    }));
    ok(doc.sh <= doc.ch + 1,
       `${L}: the page still does not scroll vertically with the openers on it ` +
       `(scrollHeight ${doc.sh} vs clientHeight ${doc.ch})`);
    ok(doc.sw <= doc.cw + 1, `${L}: …nor horizontally (${doc.sw} vs ${doc.cw})`);

    /* ---- (c) ONE TAP, and a real turn happens ----
     * `page.tap()`, not `.click()`: it REFUSES on an obscured element, which is the
     * strongest available form of "a thumb can actually reach this". */
    if (openers.found) {
      await page.tap("#chat-openers .opener:nth-of-type(1)");
      await page.waitForFunction(
        () => [...document.querySelectorAll("#transcript .turn")].some((r) => /\buser\b/.test(r.className)),
        { timeout: 15000 }).catch(() => {});
      await page.waitForSelector("#transcript .turn.moxie", { timeout: 15000 }).catch(() => {});
    }
    const turn = await page.evaluate(() => ({
      rows: [...document.querySelectorAll("#transcript .turn")].map((r) => ({
        who: r.className, msg: (r.querySelector(".msg") || r).textContent.trim() })),
      inputValue: document.getElementById("speech-input").value,
      expanded: document.getElementById("rail-toggle").getAttribute("aria-expanded"),
      railDisplay: getComputedStyle(document.getElementById("rail-scroll")).display,
      scrollY: window.scrollY,
    }));
    ok(turn.rows.some((r) => /\buser\b/.test(r.who) && r.msg === "Tell me a silly joke"),
       `${L}: ONE TAP put the opener's words in the log as the VISITOR's turn — ` +
       `got ${JSON.stringify(turn.rows)}`);
    ok(turn.rows.some((r) => /\bmoxie\b/.test(r.who) && r.msg.length > 0),
       `${L}: …and Moxie answered it — got ${JSON.stringify(turn.rows)}`);
    eq(turn.inputValue, "",
       `${L}: …without writing into the message box: an opener sends, it does not pre-fill ` +
       `(a second control that can disagree with the first is the trap this dock exists to avoid)`);
    eq(turn.expanded, "false", `${L}: THE WHOLE TURN COMPLETED WITH THE RAIL NEVER OPENED`);
    eq(turn.railDisplay, "none", `${L}: …and the rail was display:none for all of it`);
    eq(turn.scrollY, 0, `${L}: …and the page never scrolled (scrollY=${turn.scrollY})`);

    const boxAfter = await page.evaluate(reach, "#speech-input");
    ok(boxAfter.inFold && boxAfter.self,
       `${L}: the composer is STILL in the fold after the turn — y=${boxAfter.top}..${boxAfter.bottom} ` +
       `of ${boxAfter.vh}, hit ${boxAfter.hit}`);
    /* They step aside once there is a conversation — the same `:has()` rule and the same
     * reasoning as `#chat-cue`, and the room they give back goes to the log. */
    const stepped = await page.evaluate(() => {
      const box = document.getElementById("chat-openers");
      return box ? getComputedStyle(box).display : "MISSING";
    });
    eq(stepped, "none",
       `${L}: …because an opener has done its job once the log has a turn in it (got ${stepped})`);

    /* ---- TEETH. Inflate the openers (CSS only, on the shipped elements) and require the
     * composer to leave the fold, so `inFold` is seen to be able to fail. */
    const broken = await page.evaluate((fn) => {
      const s = document.createElement("style");
      s.textContent = "#chat-dock:has(#transcript .turn) #chat-openers { display: grid }" +
                      "#chat-openers .opener { min-height: 700px }";
      document.head.appendChild(s);
      // eslint-disable-next-line no-eval
      return (0, eval)("(" + fn + ")")("#speech-input");
    }, reach.toString());
    eq(broken.inFold, false,
       `teeth — 700 px openers really do push the composer out of the 844 px fold ` +
       `(${broken.w}x${broken.h} at y=${broken.top}..${broken.bottom} of ${broken.vh}); ` +
       "if this passes, none of the fold assertions above is measuring anything");

    eyes(`${L}: the openers`, page);
    await page.close();
  }

  /* =====================================================================
   * 9. THE CHALLENGE, MEASURED AT THE MOMENT IT CAN ACTUALLY BE IN THE WAY.
   * Block 4 measures while the log is empty; ambient self-talk later grows #chat-dock to its
   * cap and lifts everything above it (~128 px at 390x844). This drives the page's own
   * `window.__ambient.say()` until the dock height stops changing (a measurement, not a
   * sleep), gates every assertion on `atCap`, and asserts RECT INTERSECTION — a centre hit
   * test stayed green while the challenge covered a third of the handle.
   * With the dock at cap the handle and a viewport-centred challenge overlap only for
   * 683 < vh < 909: 844 and 851 are inside; 375x667 is below and pins that nothing already
   * clear was moved.
   * =================================================================== */
  {
    /* Drive the log to the dock's cap with the page's own mutter writer; returns what it
     * achieved so assertions refuse a state that never arrived. */
    const fillLog = (page) => page.evaluate(() => {
      const dock = document.getElementById("chat-dock");
      const log = document.getElementById("transcript");
      if (!dock || !log || !window.__ambient || typeof window.__ambient.say !== "function")
        return { drove: false, said: 0 };
      const H = () => Math.round(dock.getBoundingClientRect().height);
      const LINES = [
        "Do you ever think about how many teeth you have?",
        "I counted the ceiling tiles. Twice. Same answer both times.",
        "If I hold still enough, the room forgets I am in it.",
        "My battery dreams in percentages.",
        "Somewhere a refrigerator is humming my song.",
      ];
      const before = H();
      let said = 0, stable = 0;
      while (said < 60 && stable < 4) {
        const was = H();
        window.__ambient.say(LINES[said % LINES.length] + " · " + said);
        said++;
        if (H() === was) stable++; else stable = 0;
      }
      const r = log.getBoundingClientRect();
      const max = parseFloat(getComputedStyle(log).maxHeight);
      return {
        drove: true, said, dockBefore: before, dockAfter: H(),
        logH: Math.round(r.height), logMax: Math.round(max),
        rows: log.querySelectorAll(".mutter").length,
        // The dock is at its cap when the LOG is at its own max-height and scrolling.
        atCap: isFinite(max) && r.height >= max - 1 && log.scrollHeight > log.clientHeight + 1,
      };
    });

    /** Does the challenge's box intersect `sel`'s box, and who owns `sel`'s centre? */
    const clearOf = (sel) => {
      const cf = document.getElementById("fake-cf-widget");
      const el = document.querySelector(sel);
      if (!cf || !el) return { found: false, sel };
      const a = cf.getBoundingClientRect(), b = el.getBoundingClientRect();
      if (!(b.width > 0 && b.height > 0)) return { found: true, sel, sized: false };
      const ox = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
      const oy = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
      const hit = document.elementFromPoint(Math.round(b.left + b.width / 2),
                                            Math.round(b.top + b.height / 2));
      const id = hit ? (hit.id ? "#" + hit.id : hit.tagName.toLowerCase()) : "null";
      return {
        found: true, sized: true, sel,
        cf: [Math.round(a.top), Math.round(a.bottom)],
        el: [Math.round(b.top), Math.round(b.bottom)],
        overlap: Math.round(ox * oy), oy: Math.round(oy),
        hit: id, onTurnstile: id === "#fake-cf-widget" || id === "#turnstile-holder",
      };
    };

    // Every bottom-anchored control a challenge could land on.
    const CONTROLS = ["#rail-toggle", "#chat-openers", "#speech-input", "#speech-btn"];

    for (const [label, w, h, inWindow] of [
      ["iPhone 12  390x844", 390, 844, true],
      ["Pixel 5    393x851", 393, 851, true],
      ["iPhone 8   375x667", 375, 667, false],
    ]) {
      const page = await loadChallenged(w, h);

      const cold = await page.evaluate(clearOf, "#rail-toggle");
      const filled = await fillLog(page);
      ok(filled.drove,
         `${label}: ambient.js's own test seam drove the log — window.__ambient.say()`);
      ok(filled.atCap,
         `${label}: THE STATE UNDER TEST WAS REACHED — the log is pinned at its cap and ` +
         `scrolling (${filled.rows} mutters, log ${filled.logH}/${filled.logMax}px, ` +
         `dock ${filled.dockBefore} -> ${filled.dockAfter}px). If this fails, every ` +
         "assertion below is measuring the same too-early moment block 4 does.");

      const hot = await page.evaluate(clearOf, "#rail-toggle");
      ok(cold.el[0] - hot.el[0] >= 100,
         `${label}: …and the handle really rode up with it — y=${cold.el[0]} cold -> ` +
         `${hot.el[0]} full (${cold.el[0] - hot.el[0]}px). This is the movement no suite ` +
         "in this repo had ever sampled.");

      for (const sel of CONTROLS) {
        const m = await page.evaluate(clearOf, sel);
        ok(m.found && m.sized, `${label}: ${sel} is laid out with a challenge on screen`);
        eq(m.overlap, 0,
           `${label}: the challenge (y=${m.cf[0]}..${m.cf[1]}) must not touch ${sel} ` +
           `(y=${m.el[0]}..${m.el[1]}) with the log at its cap — ${m.oy}px of vertical bleed`);
        eq(m.onTurnstile, false,
           `${label}: …and a tap at ${sel}'s centre must not land on the challenge layer ` +
           `(got ${m.hit})`);
      }

      /* The other half: the challenge must stay hittable, or no turn can ever be sent. */
      const cfm = await page.evaluate(reach, "#fake-cf-widget");
      ok(cfm.shown && cfm.self,
         `${label}: the challenge itself is still hittable (${cfm.w}x${cfm.h}, hit ${cfm.hit})`);
      ok(cfm.inFold,
         `${label}: …and wholly inside the viewport — y=${cfm.top}..${cfm.bottom} of ${cfm.vh}`);
      ok(cfm.top > 0 && cfm.bottom < cfm.vh,
         `${label}: …not flush against either edge (y=${cfm.top}..${cfm.bottom} of ${cfm.vh})`);

      /* TEETH: centre the challenge in the WHOLE viewport again and require the collision on
       * the viewports the arithmetic says — and NOT on 375x667, which is outside 683..909. */
      const broken = await page.evaluate((fn) => {
        const holder = document.getElementById("turnstile-holder");
        holder.style.bottom = "0px";              // the pre-fix, whole-viewport layer
        holder.style.alignItems = "center";
        // eslint-disable-next-line no-eval
        return (0, eval)("(" + fn + ")")("#rail-toggle");
      }, clearOf.toString());
      if (inWindow) {
        ok(broken.overlap > 0,
           `${label}: teeth — centred in the whole viewport the challenge ` +
           `(y=${broken.cf[0]}..${broken.cf[1]}) DOES land on #rail-toggle ` +
           `(y=${broken.el[0]}..${broken.el[1]}, ${broken.oy}px); if this passes, nothing ` +
           "above is being measured");
      } else {
        eq(broken.overlap, 0,
           `${label}: teeth — …and at vh=${h}, below the 683..909 window, the same ` +
           `mutation does NOT collide (challenge y=${broken.cf[0]}..${broken.cf[1]}, ` +
           `handle y=${broken.el[0]}..${broken.el[1]}) — the filing's own device is the ` +
           "one where the dock's growth carries the handle clear ABOVE the band");
      }

      eyes(`${label}: the challenged page with a full log`, page);
      await page.close();
    }
  }

} catch (e) {
  fails.push("threw: " + (e && e.stack ? e.stack.split("\n").slice(0, 4).join(" / ") : e));
} finally {
  await browser.close().catch(() => {});
  site.close();
}

finish(LABEL, { fails, count });
