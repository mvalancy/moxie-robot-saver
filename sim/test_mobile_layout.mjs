/* test_mobile_layout.mjs — on a phone, is the control under your thumb the one you meant?
 *
 * A control can be visible, sized, unclipped and `pointer-events:auto` and still be untappable
 * because something else sits on top of it (the env banner once covered #rail-toggle; the
 * Turnstile challenge once did too). Only `document.elementFromPoint()` catches that, so that
 * is the assertion here — plus TEETH that restore each pre-fix geometry and require the
 * collision to REAPPEAR, so a selector matching nothing cannot read as green.
 *   0 the HUB: "Talk to Moxie" and the linked picture of her are in the first screen
 *     (teeth: the pre-fix order puts the button below the fold again).
 *   1 per phone, cold: the composer is reachable on first paint; the banner hangs under the
 *     header, clear of her torso and of the drawer handle; the drawer really opens (teeth:
 *     the pre-fix placement covers her torso and, unlifted, the message box).
 *   1b the degraded banner of a deployment WITH a brain (napping/resting) at 390x844.
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

/** Her head and torso on screen, from the frame the bubble placed itself from (one instant):
 *  `torso` runs from just under her chin (`chest`) down one head-height. Saying a word is
 *  how a frame gets placed; a hidden bubble freezes the stash. */
const herBody = (page) => page.evaluate(() => new Promise((resolve) => {
  window.moxie.setSpeech("Hi.");
  const t0 = performance.now();
  const poll = () => {
    const a = window.__bubbleAnchor();
    if (a && a.exact && !a.frozen) {
      const crown = a.exact.crown.y, chest = a.exact.chest.y;
      return resolve({ crown: Math.round(crown), chest: Math.round(chest),
                       torso: [Math.round(chest), Math.round(chest + (chest - crown))] });
    }
    if (performance.now() - t0 > 8000) return resolve(null);
    requestAnimationFrame(poll);
  };
  poll();
}));
/** The banner's box, and the header's bottom (the top of #stage) it should hang under. */
const bannerBox = (page) => page.evaluate(() => {
  const b = document.getElementById("env-banner");
  if (!b) return null;
  const r = b.getBoundingClientRect(), cs = getComputedStyle(b);
  return { top: Math.round(r.top), bottom: Math.round(r.bottom), shown: r.height > 0 && cs.visibility !== "hidden",
           header: Math.round(document.getElementById("stage").getBoundingClientRect().top),
           text: (b.querySelector(".eb-text") || {}).textContent || "",
           link: !!b.querySelector(".eb-link") && !b.querySelector(".eb-link").hidden };
});
const overlap = (a, lo, hi) => Math.max(0, Math.min(a.bottom, hi) - Math.max(a.top, lo));
/** The placement the banner had before it moved under the header: bottom-anchored, lifted
 *  above the dock (or, with `lift` false, the original unlifted 14 px). */
const PRE_FIX = (lift) => `() => { const b = document.getElementById("env-banner");
  b.style.top = "auto"; b.style.zIndex = "30";
  b.style.bottom = ${lift ? '"calc(var(--eb-lift) + 14px)"' : '"14px"'}; }`;

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
   * 0. THE HUB ON A PHONE: THE WAY IN IS IN THE FIRST SCREEN. Measured on the live site at
   * 390x844: the picture of her was not a link and "Open the simulator" sat at y=962, under
   * a 70-word pitch. Now the picture links to her and says so, and "Talk to Moxie" comes
   * before the pitch — on the two short phones too (the picture yields to 40svh).
   * =================================================================== */
  for (const [label, w, h] of [PHONES[0], PHONES[2], PHONES[1]]) {
    const page = await browser.newPage();
    await page.setViewport(phone(w, h));
    const errs = [];
    page.on("console", (m) => { if (m.type() === "error") errs.push(m.text()); });
    page.on("pageerror", (e) => errs.push("PAGEERR " + e.message));
    await page.goto(`http://moxie.hosted.test:${site.port}/`, { waitUntil: "load", timeout: 30000 });
    await page.evaluate(() => document.fonts.ready.then(() => new Promise((r) => requestAnimationFrame(() => r()))));
    const cta = await hit(page, "a.btn.primary");
    reachable(`${label} hub`, cta, "the primary button");
    ok(/^Talk to Moxie$/.test(cta.text || ""), `${label} hub: …and it says what you will do there — ${JSON.stringify(cta.text)}`);
    const hero = await hit(page, "#stage a.frame");
    reachable(`${label} hub`, hero, "the picture of her, as a link");
    const hrefs = await page.evaluate(() => ({
      cta: document.querySelector("a.btn.primary").getAttribute("href"),
      hero: (document.querySelector("#stage a.frame") || { getAttribute: () => null }).getAttribute("href"),
      tap: (() => { const t = document.querySelector("#stage a.frame .tap");
        return t ? { text: t.textContent.trim(), shown: getComputedStyle(t).display !== "none" && t.getBoundingClientRect().height > 0 } : null; })(),
    }));
    eq(JSON.stringify([hrefs.cta, hrefs.hero]), '["sim","sim"]',
       `${label} hub: both go straight to /sim (sim.html is a 308 on Cloudflare Pages)`);
    ok(hrefs.tap && hrefs.tap.shown && /^Tap to talk to Moxie$/.test(hrefs.tap.text),
       `${label} hub: the picture SAYS it is the way in (${JSON.stringify(hrefs.tap)})`);
    eq(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1),
       false, `${label} hub: no horizontal page scroll`);
    if (w === 390) {
      /* TEETH: the pre-fix order (the button after the pitch) and it is below the fold again. */
      const broken = await hitAfter(page, `() => document.querySelector(".hero-copy .sub")
        .after(document.querySelector(".hero-copy .cta"))`, "a.btn.primary");
      ok(broken.found && !broken.inFold,
         `teeth — with the button back under the pitch it leaves the first screen again (y=${broken.top} of ${broken.vh})`);
    }
    eq(errs.length, 0, `${label} hub: console errors — ${errs.slice(0, 3).join(" | ")}`);
    await page.close();
  }

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
    /* …and it hangs UNDER THE HEADER, clear of her torso. Lifted above the dock (the old
     * placement) the card covered her from the chin down: at 390x844, 366..527 over a torso
     * of 350..463, and higher still once the log grew. */
    const body = await herBody(page);
    const ban = await bannerBox(page);
    ok(!!body, `${label}: her body was placed on screen (the bubble anchor stamped a frame)`);
    ok(ban && ban.top >= ban.header - 1 && ban.top <= ban.header + 24,
       `${label}: the banner hangs just under the header (banner top ${ban && ban.top}, header ${ban && ban.header})`);
    if (body && ban) {
      eq(overlap(ban, body.torso[0], body.torso[1]), 0,
         `${label}: the banner does not cover her torso (banner ${ban.top}..${ban.bottom}, torso ${body.torso.join("..")})`);
      ok(ban.bottom < body.crown,
         `${label}: …nor her head — it fits in her headroom (banner bottom ${ban.bottom}, crown ${body.crown})`);
    }
    /* Her speech bubble shares that headroom on a short phone: her words draw OVER the note. */
    // Said again in the same task as the hit test: a hidden bubble is pointer-events:none.
    const bub = await hitAfter(page, `() => window.moxie.setSpeech("Hi.")`, "#bubble");
    ok(bub.self, `${label}: a tap at the centre of her speech bubble reaches the bubble, not ${bub.hit}`);
    if (w === 375) {
      /* TEETH: the pre-fix placement (bottom-anchored, lifted above the dock) lands on her
       * torso — so the check above can see a covered robot. */
      await page.evaluate(`(${PRE_FIX(true)})()`);
      const lifted = await bannerBox(page);
      ok(body && lifted && overlap(lifted, body.torso[0], body.torso[1]) > 0,
         `teeth: lifted above the dock the banner covers her torso again (banner ${lifted && lifted.top}..${lifted && lifted.bottom}, torso ${body && body.torso.join("..")})`);
      /* TEETH: env.js still measures a real lift (the wider layouts use it), and without one
       * the bottom-anchored banner swallows the message box. */
      const lift = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--eb-lift").trim());
      ok(/^\d+px$/.test(lift) && parseInt(lift, 10) > 0, `teeth: env.js measured a real lift, not a constant (${lift})`);
      const broken = await hitAfter(page, PRE_FIX(false), "#speech-input");
      ok(!broken.self && /env-banner|\beb-/.test(broken.hit),
         `teeth: at the bottom with no lift the banner LAYER swallows the tap again (hit ${broken.hit})`);
      await page.evaluate(() => { const b = document.getElementById("env-banner");
                                  b.style.top = ""; b.style.bottom = ""; b.style.zIndex = ""; });
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
   * 1b. A DEPLOYMENT WITH A BRAIN, DEGRADED, at 390x844: the banner says she is napping or
   * resting — never "need a locally-run backend" or "Run it locally" (that is true only of a
   * deployment with no brain, block 1's fixture) — and it covers no part of her. Both ways
   * in: the budget answered by /api/health, and a turn the brain fails mid-conversation.
   * =================================================================== */
  for (const [kind, fixture] of [
    ["budget spent", { health: JSON.stringify({ ok: true, degraded: true, reason: "budget_exhausted", retry_after_s: 1800,
                                                mode: "degraded", voice: true, ears: true }) }],
    ["brain down mid-chat", { health: JSON.stringify({ ok: true, reason: null, mode: "live", voice: true, ears: true }),
                              chat: JSON.stringify({ ok: false, reason: "upstream_down", retry_after_s: 0, mode: "degraded",
                                                     messages: [], speech: [], context: "" }) }],
  ]) {
    const L = `iPhone 12  390x844, ${kind}`;
    const failed = { n: 0 };            // the 503s answered on purpose, forgiven by `eyes`
    const v = await openSim(browser, HOSTED, { viewport: phone(390, 844), health: fixture.health,
      route: (r, u) => fixture.chat && /\/api\/chat\b/.test(u)
        ? (failed.n++, r.respond({ status: 503, contentType: "application/json", body: fixture.chat }), true) : false });
    const page = v.page;
    views.set(page, v);
    if (fixture.chat) {
      await page.evaluate(() => { document.getElementById("speech-input").value = "hello moxie";
                                  document.getElementById("speech-btn").click(); });
      await page.waitForFunction(() => window.moxieMode.state() === "degraded", { timeout: 15000 }).catch(() => {});
    }
    await page.waitForFunction(() => { const b = document.getElementById("env-banner");
      return !!b && getComputedStyle(b).visibility !== "hidden"; }, { timeout: 10000 }).catch(() => {});
    const ban = await bannerBox(page);
    const body = await herBody(page);
    ok(ban && ban.shown, `${L}: the degraded banner is showing (${JSON.stringify(ban)})`);
    ok(ban && /napping|resting/.test(ban.text) && !/locally/i.test(ban.text) && !ban.link,
       `${L}: it says she is napping or resting, with no "locally-run backend" and no "Run it locally" (${JSON.stringify(ban && ban.text)})`);
    ok(ban && body && ban.bottom < body.crown,
       `${L}: …and it covers NO part of her, head included (banner ${ban && ban.top}..${ban && ban.bottom}, crown ${body && body.crown})`);
    v.aborted.refused += failed.n;
    eyes(L, page);
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
   * 4. THE CHALLENGE, MEASURED WHEN IT CAN ACTUALLY BE IN THE WAY: #chat-dock at its tallest,
   * which lifts everything above it (~128 px at 390x844). Self-talk rows are appended until the
   * dock's height stops changing (a measurement, not a sleep); assertions are gated on `atCap`
   * and use RECT INTERSECTION — a centre hit test stayed green while the challenge covered a
   * third of the handle. At cap the handle and a viewport-centred challenge overlap only for
   * 683 < vh < 909: 844 and 851 are inside; 375x667 is below and pins that nothing already
   * clear was moved.
   * The rows are appended BY HAND: ambient.js no longer grows the log before a turn (one row,
   * re-worded in place), and a turn hides the cue and the openers, so a full log WITH both is
   * an envelope taller than any state the page now reaches — clear of it is clear of all.
   * =================================================================== */
  {
    const fillLog = (page) => page.evaluate(() => {
      const dock = document.getElementById("chat-dock"), log = document.getElementById("transcript");
      if (!dock || !log) return { drove: false };
      const H = () => Math.round(dock.getBoundingClientRect().height);
      const before = H();
      let said = 0, stable = 0;
      while (said < 60 && stable < 4) {
        const was = H();
        const row = document.createElement("div");
        row.className = "mutter";
        row.innerHTML = '<span class="who">Moxie · to herself</span><span class="msg">' +
          "I counted the ceiling tiles. Twice. Same answer both times. · " + said++ + "</span>";
        log.appendChild(row);
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
