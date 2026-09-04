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
   * 4. TEETH. Put the pre-fix geometry back and require the bug to return.
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
