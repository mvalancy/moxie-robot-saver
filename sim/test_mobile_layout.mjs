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

    // Scroll a real control into the rail's view and check the same way.
    const ctrl = await page.evaluate(() => {
      const b = document.getElementById("mic-btn");
      b.scrollIntoView({ block: "center" });
      const r = b.getBoundingClientRect();
      const hit = document.elementFromPoint(Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2));
      return { self: !!hit && (hit === b || b.contains(hit)),
               hit: hit ? (hit.id ? "#" + hit.id : hit.tagName) : "null",
               w: Math.round(r.width), h: Math.round(r.height) };
    });
    ok(ctrl.self, `${label}: #mic-btn inside the open drawer is hittable (got ${ctrl.hit})`);
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

    /* TEETH, in the same block: put the pre-fix geometry back and require the collision to
     * return. Without this the assertions above are equally consistent with "the fix works"
     * and "the fake widget has no size". */
    const broken = await page.evaluate((fn) => {
      const holder = document.getElementById("turnstile-holder");
      holder.setAttribute("style",
        "position:fixed;left:50%;bottom:16px;transform:translateX(-50%);z-index:70;" +
        "display:flex;justify-content:center;pointer-events:auto");
      // eslint-disable-next-line no-eval
      return (0, eval)("(" + fn + ")")("#rail-toggle");
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
    const lift = await page.evaluate(() =>
      getComputedStyle(document.documentElement).getPropertyValue("--eb-lift").trim());
    ok(/^\d+px$/.test(lift) && parseInt(lift, 10) > 0,
       `teeth: env.js measured a real lift, not a constant (--eb-lift: ${JSON.stringify(lift)})`);

    const broken = await page.evaluate((fn) => {
      document.documentElement.style.setProperty("--eb-lift", "0px");
      // eslint-disable-next-line no-eval
      return (0, eval)("(" + fn + ")")("#rail-toggle");
    }, hitTest.toString());
    eq(broken.self, false,
       `teeth: with --eb-lift back at 0 the collision RETURNS (hit ${broken.hit}) — ` +
       "if this passes, the assertion above is not measuring anything");
    ok(/env-banner/.test(broken.hit),
       `teeth: …and it is the banner that swallows the tap (got ${broken.hit})`);
    await page.close();
  }
} catch (e) {
  fails.push("threw: " + (e && e.stack ? e.stack.split("\n").slice(0, 4).join(" / ") : e));
} finally {
  await browser.close().catch(() => {});
  site.close();
}

finish(LABEL, { fails, count });
