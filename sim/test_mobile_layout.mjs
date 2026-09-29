/* test_mobile_layout.mjs — on a phone, is the control under your thumb the one you meant?
 *
 * A control can be visible, sized, unclipped and `pointer-events:auto` and still be untappable
 * because something else sits on top of it (the env banner once covered #rail-toggle; the
 * Turnstile challenge once did too). Only `document.elementFromPoint()` catches that, so that
 * is the assertion here — plus TEETH that restore each pre-fix geometry and require the
 * collision to REAPPEAR, so a selector matching nothing cannot read as green.
 *   1 per phone, cold: the composer is reachable on first paint; the banner does not cover
 *     the drawer handle; the drawer really opens (teeth: --eb-lift back at 0).
 *   2 the Turnstile challenge on screen, both directions (teeth: the bottom:16px holder).
 *   3 the rail is optional: an opener and a typed line are whole turns with it shut; it still
 *     opens and works (teeth: the composer back inside the collapsed rail).
 *   4 the challenge measured with the log at its cap, when it can actually be in the way.
 *
 *   node sim/test_mobile_layout.mjs
 */
import { requireBrowser, serveWeb, makeChecks, finish, launchBrowser, openSim, hitTest, notable }
  from "./browser_harness.mjs";

const LABEL = "mobile-layout test";
const { puppeteer, chrome } = await requireBrowser(LABEL);
const { fails, ok, eq, count } = makeChecks();
const site = await serveWeb();
/* The banner only renders on a NON-local host (env.js), the deployment the collision was
 * measured on, so phones are driven against a mapped hostname. */
const HOSTED = `http://moxie.hosted.test:${site.port}/sim.html`;
const browser = await launchBrowser(puppeteer, chrome, { hosts: { "moxie.hosted.test": site.port } });

const PHONES = [
  ["iPhone 12  390x844", 390, 844],
  ["iPhone SE  360x640", 360, 640],
  ["iPhone 8   375x667", 375, 667],
  ["Pixel 5    393x851", 393, 851],
  ["iPhone XR  414x896", 414, 896],
];
const phone = (w, h) => ({ width: w, height: h, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
const views = new WeakMap();
const eyes = (label, page) => {
  const v = views.get(page), left = notable(v.errs, v.aborted);
  eq(left.length, 0, `${label}: the page raised console errors nobody asked for — ${left.slice(0, 3).join(" | ")}`);
};
const hit = (page, sel) => page.evaluate(hitTest, sel);
/** Evaluate `fn` after a page-side mutation, with the hitTest source re-hydrated in the page. */
const hitAfter = (page, mutate, sel) => page.evaluate(`(${mutate})(); (${hitTest})(${JSON.stringify(sel)})`);

/** `degraded` keeps the banner and makes the turn SCRIPTED (stub.js): nothing leaves the page. */
async function load(w, h) {
  const v = await openSim(browser, HOSTED, { viewport: phone(w, h),
    health: JSON.stringify({ ok: false, reason: "gateway_not_configured", mode: "degraded" }) });
  views.set(v.page, v);
  // env.js measures the lift after the banner mounts; wait for a real measurement.
  await v.page.waitForFunction(() => !!document.getElementById("env-banner") &&
    parseInt(getComputedStyle(document.documentElement).getPropertyValue("--eb-lift"), 10) > 0,
  { timeout: 10000 }).catch(() => {});
  return v.page;
}

/** The CHALLENGED visitor (the only state in which the widget takes space): `/api/health`
 *  publishes a sitekey and Cloudflare's api.js is a fake whose `render()` injects 300x65. */
async function loadChallenged(w, h) {
  const v = await openSim(browser, HOSTED, { viewport: phone(w, h),
    health: JSON.stringify({ ok: true, reason: null, mode: "live", turnstile: "1x00000000000000000000BB",
                             voice: false, ears: false }),
    route: (r, u) => /^https:\/\/challenges\.cloudflare\.com\//.test(u) && (r.respond({
      status: 200, contentType: "text/javascript", headers: { "Access-Control-Allow-Origin": "*" },
      body: `window.turnstile = {
        render: function (box) {
          var d = document.createElement("div");
          d.id = "fake-cf-widget";
          d.setAttribute("style", "min-width:300px;width:300px;height:65px;background:#345");
          box.appendChild(d);
          return "w1";
        },
        reset: function () {}, execute: function () {}, getResponse: function () { return ""; },
      };` }), true) });
  views.set(v.page, v);
  await v.page.waitForFunction(() => !!document.getElementById("fake-cf-widget"), { timeout: 10000 }).catch(() => {});
  await v.page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
  return v.page;
}
const tapDrawer = async (page) => {
  await page.tap("#rail-toggle");       // tap() REFUSES an obscured element: the strongest form
  await page.waitForFunction(() => document.getElementById("rail-toggle").getAttribute("aria-expanded") === "true",
                             { timeout: 5000 }).catch(() => {});
};
const railState = (page) => page.evaluate(() => ({
  expanded: document.getElementById("rail-toggle").getAttribute("aria-expanded"),
  display: getComputedStyle(document.getElementById("rail-scroll")).display,
  railH: Math.round(document.getElementById("rail-scroll").getBoundingClientRect().height),
  groups: document.querySelectorAll("#rail-scroll .group").length,
}));

/** Reachable on a cold load: a real box, INSIDE the first viewport, owning its centre, unscrolled.
 *  The production defect measured #speech-input 262x40 at y=2095 of 844, inside a shut drawer. */
function reachable(label, m, what) {
  ok(m.found && m.shown, `${label}: ${what} (${m.sel}) has a real box — got ${m.w}x${m.h}`);
  ok(m.inFold, `${label}: ${what} is INSIDE the first viewport — y=${m.top}..${m.bottom} of ${m.vh}`);
  ok(m.self, `${label}: …and a tap at its centre reaches it, not ${m.hit}`);
  eq(m.scrollY, 0, `${label}: …with the page never scrolled`);
}

const typedTurn = async (page, fire, words) => {
  const n = await page.evaluate(() => document.querySelectorAll("#transcript .turn.moxie").length);
  await fire();
  await page.waitForFunction((k) => document.querySelectorAll("#transcript .turn.moxie").length > k,
                             { timeout: 15000 }, n).catch(() => {});
  const rows = await page.evaluate(() => [...document.querySelectorAll("#transcript .turn")].map((r) => ({
    who: r.className, msg: (r.querySelector(".msg") || r).textContent.trim() })));
  ok(rows.some((r) => /\buser\b/.test(r.who) && r.msg === words) && rows.filter((r) => /\bmoxie\b/.test(r.who)).length > n,
     `${JSON.stringify(words)} landed in the log as the visitor's turn and Moxie answered — ${JSON.stringify(rows)}`);
};

try {
  /* =====================================================================
   * 1. EVERY PHONE, COLD — nothing tapped, nothing scrolled, the drawer shut.
   * =================================================================== */
  for (const [label, w, h] of PHONES) {
    const page = await load(w, h);
    const cold = await railState(page);
    ok(cold.expanded === "false" && cold.display === "none",
       `${label}: the engineering rail is CLOSED on a cold load, its contents display:none (${JSON.stringify(cold)})`);

    /* The composer, on first paint — never `element.exists`. */
    reachable(label, await hit(page, "#speech-input"), "the message box");
    reachable(label, await hit(page, "#speech-btn"), "the send button");
    reachable(label, await hit(page, "#mic-btn"), "the mic button");
    const beside = await page.evaluate(() => {
      const m = document.getElementById("mic-btn"), s = document.getElementById("speech-btn");
      const i = document.getElementById("speech-input");
      const mr = m.getBoundingClientRect(), sr = s.getBoundingClientRect();
      return { sameRow: m.parentElement === s.parentElement && m.parentElement === i.parentElement,
               gap: Math.round(Math.min(Math.abs(sr.left - mr.right), Math.abs(mr.left - sr.right))),
               dy: Math.round(Math.abs((mr.top + mr.height / 2) - (sr.top + sr.height / 2))) };
    });
    ok(beside.sameRow && beside.gap <= 24 && beside.dy <= 6,
       `${label}: the mic, the box and send are ONE row, the mic beside send (${JSON.stringify(beside)})`);
    /* Something on first paint TELLS a stranger they can talk: real words, not a placeholder. */
    const cue = await hit(page, "#chat-cue");
    reachable(label, cue, "the 'talk to Moxie' cue");
    ok(/talk to moxie/i.test(cue.text || ""), `${label}: …naming the action in plain language — ${JSON.stringify(cue.text)}`);
    ok(/moxie/i.test(await page.$eval("#speech-input", (e) => e.placeholder || "")), `${label}: the box's placeholder names her too`);

    /* The hosted banner is up and NOT on top of the drawer handle (its lift is measured). */
    ok(await page.evaluate(() => !!document.getElementById("env-banner")), `${label}: the hosted banner is showing (the fixture the collision needs)`);
    for (const [sel, what] of [["#rail-toggle", "the drawer handle"], ["#env-banner .eb-x", "the banner's own dismiss X"],
                               ["#alive-toggle", "the topbar ALIVE toggle"]]) {
      const m = await hit(page, sel);
      ok(m.sized && m.self, `${label}: a tap at the centre of ${what} reaches it, not ${m.hit}`);
    }
    if (w === 375) {
      /* TEETH: put the pre-fix `--eb-lift: 0` back and require the banner to swallow the tap
       * again (at the message box, now the bottom row), then restore it. */
      const lift = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--eb-lift").trim());
      ok(/^\d+px$/.test(lift) && parseInt(lift, 10) > 0, `teeth: env.js measured a real lift, not a constant (${lift})`);
      const broken = await hitAfter(page, `() => document.documentElement.style.setProperty("--eb-lift", "0px")`, "#speech-input");
      ok(!broken.self && /env-banner|\beb-/.test(broken.hit),
         `teeth: with --eb-lift back at 0 the banner LAYER swallows the tap again (hit ${broken.hit})`);
      await page.evaluate((l) => document.documentElement.style.setProperty("--eb-lift", l), lift);
    }

    /* Open the drawer FOR REAL and drive what is inside it. */
    await tapDrawer(page);
    const open = await railState(page);
    ok(open.expanded === "true" && open.railH > 0, `${label}: tapping the handle really opens the drawer (${JSON.stringify(open)})`);
    const again = await hit(page, "#rail-toggle");
    ok(again.self, `${label}: the handle is STILL reachable with the drawer open (got ${again.hit})`);
    await page.evaluate(() => document.getElementById("center-btn").scrollIntoView({ block: "center" }));
    const ctrl = await hit(page, "#center-btn");
    ok(ctrl.self && ctrl.h >= 40, `${label}: #center-btn inside the open drawer is hittable at touch size (${ctrl.w}x${ctrl.h}, ${ctrl.hit})`);
    eq(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1),
       false, `${label}: no horizontal page scroll`);
    eyes(`${label}: the phone page`, page);
    await page.close();
  }

  /* =====================================================================
   * 2. THE TURNSTILE CHALLENGE IS NOT ON TOP OF THE CONTROLS — both directions: the controls
   * own their centres AND the challenge stays hittable (an unsolvable challenge is worse).
   * =================================================================== */
  for (const [label, w, h] of [PHONES[2], PHONES[3]]) {
    const page = await loadChallenged(w, h);
    const drew = await page.evaluate(() => {
      const d = document.getElementById("fake-cf-widget"), holder = document.getElementById("turnstile-holder");
      const r = d ? d.getBoundingClientRect() : { width: 0, height: 0 };
      return { holder: !!holder, drew: r.width > 0 && r.height > 0, pe: holder ? getComputedStyle(holder).pointerEvents : "" };
    });
    ok(drew.holder && drew.drew, `${label}: a sitekey builds the holder and a challenge is drawn in it`);
    eq(drew.pe, "none", `${label}: the holder LAYER is pointer-events:none — an empty one cannot swallow a tap`);
    /* `drew.drew &&` is part of each assertion: with no challenge on screen they are trivially
     * true (page_teeth_check.py's `turnstilejs-inert` row proved it). */
    for (const sel of ["#rail-toggle", "#speech-input"]) {
      const m = await hit(page, sel);
      ok(drew.drew && m.self, `${label}: with a challenge ON SCREEN, a tap at ${sel} reaches it (got ${m.hit})`);
    }
    ok((await hit(page, "#fake-cf-widget")).self, `${label}: …while the challenge itself is hittable, not decoration`);
    /* Clear of the top chrome and of the drawer handle: turnstile.js centres it in the space
     * ABOVE the bottom controls, which moves as #chat-dock grows (block 4). */
    const between = await page.evaluate(() => {
      const a = document.getElementById("fake-cf-widget").getBoundingClientRect();
      const t = (document.getElementById("notice") || document.getElementById("topbar")).getBoundingClientRect();
      const c = document.getElementById("rail-toggle").getBoundingClientRect();
      return { cf: [Math.round(a.top), Math.round(a.bottom)], chrome: Math.round(t.bottom),
               oy: Math.round(Math.max(0, Math.min(a.bottom, c.bottom) - Math.max(a.top, c.top))) };
    });
    ok(between.cf[0] >= between.chrome && between.oy === 0,
       `${label}: …in the open stage, below the top chrome and clear of #rail-toggle (${JSON.stringify(between)})`);
    await tapDrawer(page);
    const exp = (await railState(page)).expanded;
    ok(drew.drew && exp === "true", `${label}: …and tapping the handle really opens the drawer, challenge and all (${exp})`);
    /* TEETH: the pre-fix holder (bottom:16px, pointer-events:auto) must swallow the message box again. */
    const broken = await hitAfter(page, `() => document.getElementById("turnstile-holder").setAttribute("style",
      "position:fixed;left:50%;bottom:16px;transform:translateX(-50%);z-index:70;display:flex;justify-content:center;pointer-events:auto")`,
      "#speech-input");
    ok(!broken.self && /fake-cf-widget|turnstile-holder/.test(broken.hit),
       `${label}: teeth — with the holder back at bottom:16px the TURNSTILE LAYER swallows the box again (hit ${broken.hit})`);
    eyes(`${label}: the challenged page`, page);
    await page.close();
  }

  /* =====================================================================
   * 3. THE RAIL IS OPTIONAL — two whole turns with it shut, and it still works. SCRIPTED:
   * `degraded` sends the turn to stub.js; the over-the-wire claim is test_typed_turn's.
   * =================================================================== */
  {
    const L = "iPhone 12  390x844";
    const page = await load(390, 844);
    /* The three openers (backlog/gamify-the-public-sim.md): in the DOCK, one row, touch-sized.
     * The rail's #speech-chips look similar but only play shipped audio, and stay in the rail. */
    const openers = await page.evaluate(() => {
      const box = document.getElementById("chat-openers"), chips = document.getElementById("speech-chips");
      const btns = box ? [...box.querySelectorAll("button.opener")] : [];
      return { labels: btns.map((b) => b.textContent.replace(/\s+/g, " ").trim()),
               heights: btns.map((b) => Math.round(b.getBoundingClientRect().height)),
               rows: new Set(btns.map((b) => Math.round(b.getBoundingClientRect().top))).size,
               inDock: !!(box && box.closest("#chat-dock")) && !box.closest("#panel"),
               chipsInRail: !!(chips && chips.closest("#panel")) && !chips.closest("#chat-dock") };
    });
    eq(JSON.stringify(openers.labels), JSON.stringify(["Tell me a silly joke", "What makes you happy?", "Surprise me!"]),
       `${L}: the three openers the brief names`);
    ok(openers.inDock && openers.chipsInRail, `${L}: openers in #chat-dock, #speech-chips still in the rail (${JSON.stringify(openers)})`);
    ok(openers.heights.every((x) => x >= 44), `${L}: every opener is a 44 px touch target (${openers.heights})`);
    /* ONE ROW is a height budget: a second row lifted #rail-toggle into the Turnstile challenge. */
    eq(openers.rows, 1, `${L}: the three openers share ONE row`);
    for (let i = 1; i <= 3; i++) reachable(L, await hit(page, `#chat-openers .opener:nth-of-type(${i})`), `opener ${i}`);
    reachable(L, await hit(page, "#speech-input"), "the message box, with the openers above it");
    const doc = await page.evaluate(() => ({ sh: document.documentElement.scrollHeight, ch: document.documentElement.clientHeight }));
    ok(doc.sh <= doc.ch + 1, `${L}: the page does not scroll vertically with the openers on it (${doc.sh} vs ${doc.ch})`);

    /* ONE TAP is a real turn (tap() refuses an obscured element) — and it does not pre-fill. */
    await typedTurn(page, () => page.tap("#chat-openers .opener:nth-of-type(1)"), "Tell me a silly joke");
    eq(await page.$eval("#speech-input", (e) => e.value), "", `${L}: an opener SENDS, it does not pre-fill the box`);
    eq(await page.evaluate(() => getComputedStyle(document.getElementById("chat-openers")).display), "none",
       `${L}: the openers step aside once the log has a turn in it`);
    /* …and a typed line, tapped through the same composer. */
    await page.evaluate(() => { document.getElementById("speech-input").value = "hello moxie"; });
    await typedTurn(page, () => page.tap("#speech-btn"), "hello moxie");
    eq(await page.$eval("#speech-input", (e) => e.value), "", `${L}: the box empties, so the next line does not double up`);
    const shut = await railState(page);
    ok(shut.expanded === "false" && shut.display === "none" && await page.evaluate(() => window.scrollY) === 0,
       `${L}: BOTH TURNS COMPLETED WITH THE RAIL NEVER OPENED and the page never scrolled (${JSON.stringify(shut)})`);
    ok((await hit(page, "#transcript")).inFold, `${L}: the comms log is in the first viewport`);
    reachable(L, await hit(page, "#speech-input"), "the composer, after the turns");

    /* OPTIONAL IS NOT REMOVED: the rail still opens, and a control in it really works
     * (`#axes-on` has a deterministic DOM effect; a motor would race the liveness loop). */
    await tapDrawer(page);
    const opened = await railState(page);
    ok(opened.expanded === "true" && opened.railH > 0 && opened.groups >= 4,
       `${L}: the rail still opens on demand, all its groups intact (${JSON.stringify(opened)})`);
    await page.evaluate(() => document.getElementById("axes-on").scrollIntoView({ block: "center" }));
    const axes = await hit(page, "#axes-on");
    const was = await page.evaluate(() => document.getElementById("axis-legend").hidden);
    await page.tap("#axes-on");
    await page.waitForFunction(() => !document.getElementById("axis-legend").hidden, { timeout: 5000 }).catch(() => {});
    ok(axes.self && was === true && await page.evaluate(() => !document.getElementById("axis-legend").hidden),
       `${L}: …and a control inside it is hittable and works — 'show axes' revealed the legend (hit ${axes.hit})`);
    const still = await hit(page, "#speech-input");
    ok(still.inFold && still.self, `${L}: the message box is STILL reachable with the rail open (${still.top}..${still.bottom}, ${still.hit})`);

    /* TEETH: the composer back inside the collapsed rail is 0x0 and out of the fold again —
     * exactly what production measured. */
    const broken = await hitAfter(page, `() => { document.getElementById("hud").classList.add("rail-closed");
      document.getElementById("rail-scroll").appendChild(document.getElementById("chat-dock")); }`, "#speech-input");
    ok(!broken.inFold && !broken.shown,
       `teeth — back inside the collapsed rail the box is unreachable again (${broken.w}x${broken.h} at y=${broken.top})`);
    eyes(`${L}: the rail-free turns`, page);
    await page.close();
  }

  /* =====================================================================
   * 4. THE CHALLENGE, MEASURED WHEN IT CAN ACTUALLY BE IN THE WAY. Ambient self-talk grows
   * #chat-dock to its cap and lifts everything above it (~128 px at 390x844). The page's own
   * `window.__ambient.say()` drives the dock until its height stops changing (a measurement,
   * not a sleep); assertions are gated on `atCap` and use RECT INTERSECTION — a centre hit
   * test stayed green while the challenge covered a third of the handle. At cap the handle and
   * a viewport-centred challenge overlap only for 683 < vh < 909: 844 and 851 are inside;
   * 375x667 is below and pins that nothing already clear was moved.
   * =================================================================== */
  {
    const fillLog = (page) => page.evaluate(() => {
      const dock = document.getElementById("chat-dock"), log = document.getElementById("transcript");
      if (!dock || !log || !window.__ambient || typeof window.__ambient.say !== "function") return { drove: false };
      const H = () => Math.round(dock.getBoundingClientRect().height);
      const before = H();
      let said = 0, stable = 0;
      while (said < 60 && stable < 4) {
        const was = H();
        window.__ambient.say("I counted the ceiling tiles. Twice. Same answer both times. · " + said++);
        if (H() === was) stable++; else stable = 0;
      }
      const r = log.getBoundingClientRect(), max = parseFloat(getComputedStyle(log).maxHeight);
      return { drove: true, dock: [before, H()], log: [Math.round(r.height), Math.round(max)],
               atCap: isFinite(max) && r.height >= max - 1 && log.scrollHeight > log.clientHeight + 1 };
    });
    /** Does the challenge's box intersect `sel`'s box, and who owns `sel`'s centre? */
    const clearOf = (sel) => {
      const cf = document.getElementById("fake-cf-widget"), el = document.querySelector(sel);
      if (!cf || !el) return { found: false, sel };
      const a = cf.getBoundingClientRect(), b = el.getBoundingClientRect();
      if (!(b.width > 0 && b.height > 0)) return { found: true, sized: false, sel };
      const ox = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
      const oy = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
      const h = document.elementFromPoint(Math.round(b.left + b.width / 2), Math.round(b.top + b.height / 2));
      const id = h ? (h.id ? "#" + h.id : h.tagName.toLowerCase()) : "null";
      return { found: true, sized: true, sel, cf: [Math.round(a.top), Math.round(a.bottom)],
               el: [Math.round(b.top), Math.round(b.bottom)], overlap: Math.round(ox * oy), oy: Math.round(oy),
               hit: id, onTurnstile: id === "#fake-cf-widget" || id === "#turnstile-holder" };
    };
    for (const [label, w, h, inWindow] of [[...PHONES[0], true], [...PHONES[3], true], [...PHONES[2], false]]) {
      const page = await loadChallenged(w, h);
      const cold = await page.evaluate(clearOf, "#rail-toggle");
      const filled = await fillLog(page);
      ok(filled.drove && filled.atCap,
         `${label}: THE STATE UNDER TEST WAS REACHED — the log pinned at its cap and scrolling ` +
         `(${JSON.stringify(filled)}); otherwise every check below measures the too-early moment block 2 does`);
      const hot = await page.evaluate(clearOf, "#rail-toggle");
      ok(cold.el[0] - hot.el[0] >= 100, `${label}: …and the handle really rode up with it (y=${cold.el[0]} -> ${hot.el[0]})`);
      for (const sel of ["#rail-toggle", "#chat-openers", "#speech-input", "#speech-btn"]) {
        const m = await page.evaluate(clearOf, sel);
        ok(m.sized && m.overlap === 0 && !m.onTurnstile,
           `${label}: the challenge (y=${m.cf}) must not touch ${sel} (y=${m.el}) with the log at its cap, ` +
           `nor own its centre — ${m.oy}px of bleed, hit ${m.hit}`);
      }
      const cfm = await hit(page, "#fake-cf-widget");
      ok(cfm.shown && cfm.self && cfm.inFold && cfm.top > 0 && cfm.bottom < cfm.vh,
         `${label}: the challenge itself is still hittable and wholly on screen (${cfm.top}..${cfm.bottom} of ${cfm.vh}, ${cfm.hit})`);
      /* TEETH: centre it in the WHOLE viewport again — a collision exactly where the arithmetic
       * says, and NOT at 375x667, below the 683..909 window. */
      const broken = await page.evaluate(`(() => { const hd = document.getElementById("turnstile-holder");
        hd.style.bottom = "0px"; hd.style.alignItems = "center"; })(); (${clearOf})("#rail-toggle")`);
      if (inWindow) ok(broken.overlap > 0, `${label}: teeth — centred in the whole viewport the challenge DOES land on the handle (${JSON.stringify(broken)})`);
      else eq(broken.overlap, 0, `${label}: teeth — …and at vh=${h}, below the window, the same mutation does NOT collide`);
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
