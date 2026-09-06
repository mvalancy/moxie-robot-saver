/* check_deployed.mjs — drive a real browser against a DEPLOYED URL and ask the two
 * questions that only the deployed artifact can answer.
 *
 *   node sim/check_deployed.mjs                      # the site's own canonical origin
 *   node sim/check_deployed.mjs https://host/sim     # any deployment
 *   MOXIE_DEPLOYED_URL=https://host/sim node sim/check_deployed.mjs
 *   node sim/check_deployed.mjs --selftest           # hermetic; no network at all
 *
 * ────────────────────────────────────────────────────────────────────────────
 * WHY THIS FILE IS NOT `sim/test_*.mjs`, WHICH IS THE FIRST THING TO EXPLAIN.
 *
 * Every browser suite in this repo tests a LOCAL server. `sim/test_mobile_layout.mjs`
 * does `const site = await serveWeb()` and maps `moxie.hosted.test` to `127.0.0.1`;
 * `sim/test_csp.mjs` parses `sim/web/_headers` and replays the `/*` block itself.
 * `sim/test_preview_render.mjs` sounds like it might be the exception and is not — it
 * replays captured robot payloads through `bridge.js` with no browser and no network.
 * Nothing in the tree asserts anything about the bytes Cloudflare actually serves.
 *
 * That is a real gap here specifically, not a purist one, and `sim/web/_headers` records
 * why at length: **Cloudflare injects its Web Analytics beacon into the HTML on the way
 * out** — a `<script src="https://static.cloudflareinsights.com/beacon.min.js/…">` this
 * repo does not write and cannot edit. The first CSP we shipped refused it on every page
 * load in production, and no local suite could have seen that, because no local suite is
 * served by Cloudflare. The deployed artifact genuinely differs from the served-locally
 * one, and the difference has already bitten once.
 *
 * A file named `sim/test_*.mjs` is, in this repo, a promise: `test_ci_test_coverage.py`
 * and `test_ci_browser_suites_actually_run.py` both require that some tier — and for a
 * browser suite, THE FAST TIER — runs it on every push and every PR. This check needs the
 * public internet and a finished Pages build. Putting it behind that promise would either
 * gate every PR on an external service (see the CI note at the bottom of this comment) or
 * force a `KNOWN_UNRUN` exemption, and an exemption is how a guard quietly stops guarding.
 * So it is a TOOL that lives beside the suites, exactly like `browser_harness.mjs`, whose
 * header makes the same argument for the same reason.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * WHAT IT ASSERTS, AND WHY EACH CLAUSE IS SEPARATE.
 *
 * 1. THE COMPOSER IS REACHABLE ON A FRESH MOBILE LOAD. At 390×844 with a real iOS UA,
 *    with NO tap on CONTROLS and NO scrolling, `#speech-input` and `#speech-btn` must
 *    each (a) have a non-zero box, (b) lie entirely inside the FIRST viewport, and
 *    (c) receive their own centre point under `document.elementFromPoint`.
 *
 *    Those three are separate because the three ways this has actually failed are
 *    separate, and each one passes the other two's test:
 *
 *      · pre-PR #162, measured against the live site: `#speech-input` was `0 × 0` on load
 *        and `262 × 40 at y = 2095` after tapping CONTROLS — sized, hit-testable, and
 *        ~2 000 px below an 844 px fold;
 *      · `#env-banner` (test_mobile_layout.mjs's defect) sat ON TOP of `#rail-toggle` —
 *        visible, sized, in the viewport, `pointer-events: auto`, and swallowing every
 *        tap. Only a hit test can see that;
 *      · a `display: none` box is in no viewport and hit-tests to nothing at all.
 *
 * 2. THE BEACON REALITY. The injected `static.cloudflareinsights.com` script must LOAD —
 *    not merely be present in the HTML — and the page must fire ZERO
 *    `securitypolicyviolation` events during the load. This is the class of defect that
 *    only a deployed check can see, and it is the reason this file exists.
 *
 *    MEASURED WHILE WRITING THIS (2026-09-05), because both facts were surprising and
 *    both change the design:
 *
 *      · THE INJECTION IS ACCEPT-HEADER DEPENDENT. `curl https://…/sim` returns 23 425
 *        bytes with NO beacon; the same URL with a browser's
 *        `Accept: text/html,application/xhtml+xml,…` returns 23 792 bytes WITH it. So a
 *        `curl | grep` version of this check would have reported "no beacon, nothing to
 *        violate" and been green for the wrong reason. It has to be a real browser.
 *      · THE INJECTION IS ZONE-LEVEL, NOT PROJECT-LEVEL. `dev.<project>.pages.dev`,
 *        `<project>.pages.dev` and a branch preview all serve 23 425 bytes — no beacon,
 *        with the same `server: cloudflare` header. Only the custom domain, which is
 *        fronted by the zone that has Web Analytics enabled, gets one. That is why
 *        `expectBeacon` below is derived rather than asserted unconditionally, and it is
 *        also half of the CI judgement at the bottom of this comment: a PREVIEW
 *        DEPLOYMENT CANNOT EXERCISE CLAUSE 2 AT ALL.
 *
 * 3. EVERY PAGE ASSET ARRIVED. Same-origin, non-`/api/` requests must all come back below
 *    400 and none may fail at the network layer. See the clause itself for why it is not
 *    `consoleErrs.length === 0`.
 *
 * 4. AND THE SCRIPTS THAT ARRIVED ACTUALLY RAN. Clause 3's blind spot, and the last open
 *    member of its family: a file served **200 OK and inert** satisfies clause 3
 *    perfectly — real status, real body, no failure, no console line, no exception — and
 *    does nothing. Nothing generic can see that. So clause 4 names ONE cheap observable
 *    effect per script (`moxie.js` builds the stage canvas and the motor panel; `hud.js`
 *    puts the accessible name on each slider; `mode.js` moves `body[data-mode]` off
 *    "boot"; `env.js` creates the badge; `qr.js` draws real ink when Make is pressed) and
 *    asserts THOSE. Not a `window.__loaded` flag: an effect the page already has because
 *    of what it is for, so nobody deletes it as test scaffolding later. The clause lists
 *    the scripts it does NOT cover, out loud.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * IT SPENDS NOTHING. `/api/chat`, `/api/speech` and `/api/transcriptions` are ABORTED at
 * the browser, so no path through this file can reach the gateway even if a future page
 * decides to say hello on its own (she already speaks unprompted at ~7 s). `/api/health`
 * is allowed through on purpose: its own header promises it "makes no gateway call. EVER"
 * and it is what paints the hosted banner — the element that caused the collision
 * `test_mobile_layout.mjs` exists for — so blocking it would test a page no visitor sees.
 *
 * NO URL IS HARD-CODED. `sim/tests/test_no_deployment_defaults.py` forbids a specific
 * deployment's hostname as a default in shipped code, and that rule is right even though
 * this file sits outside the directories it scans. The default target is read at runtime
 * from the site's OWN `<link rel="canonical">` in `sim/web/index.html`: the artifact
 * names its own home, a fork that changes that line gets its own default for free, and
 * there is no second copy of the hostname to drift.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * `--selftest`: THE TEETH, AND WHY THEY ARE HERE RATHER THAN IN A DEPLOYMENT.
 *
 * A check nobody has watched fail is not a check. `--selftest` serves `sim/web` from
 * loopback under the real `_headers` policy and runs the same `probe()`:
 *
 *      baseline    the tree as committed            → every clause must PASS
 *      mutation A  `#chat-dock { display: none }`   → the 0×0 pre-#162 state
 *      mutation B  the dock pushed 2 000 px down    → sized, hit-testable, below the fold
 *      mutation C  a fixed banner laid over it      → sized, in view, and NOT tappable
 *      mutation D  `qr.js` deleted from the build   → clause 3, an asset that 404s
 *      mutation E  `moxie.js` served 200 OK, inert  → clause 4, and it takes hud.js's
 *                                                     mark with it (no sliders to name)
 *      mutation F  `hud.js`   served 200 OK, inert  → clause 4, HUD only: every moxie.js
 *                                                     mark still stands
 *      mutation G  `mode.js`  served 200 OK, inert  → clause 4, `data-mode` stuck at "boot"
 *      mutation H  `env.js`   served 200 OK, inert  → clause 4, no badge is ever built
 *      mutation I  `qr.js`    served 200 OK, inert  → clause 4, Make draws a blank canvas
 *
 * Each mutation must redden a DIFFERENT clause, which is the point: three assertions that
 * always fail together are one assertion with extra words. It is hermetic — loopback
 * only, no internet, no Cloudflare — so the fast tier runs it on every push, and what it
 * proves is that the instrument works. The instrument is then pointed at a real
 * deployment by the schedule below.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * THE CI JUDGEMENT, WHICH IS PART OF THE DELIVERABLE (2026-09-05).
 *
 * Playbook rule 21 says the branch preview settles deploy questions, so the obvious
 * wiring is "gate every PR on its own preview URL". THAT WAS INVESTIGATED AND REJECTED,
 * on measurements rather than taste:
 *
 *   · `gh api repos/<owner>/<repo>/deployments` returns `[]`. The Pages integration on
 *     this repo creates NO GitHub Deployment, so `on: deployment_status` — the one event
 *     that hands a workflow an authoritative `environment_url` — can never fire here.
 *   · The branch alias is not a synchronous fact. `https://feat-<branch>.<project>
 *     .pages.dev/sim` answers **HTTP 404 with a 16 KB HTML body** for a branch that has
 *     never been built, and serves the PREVIOUS build for one that has. A job that runs at
 *     push time therefore tests either nothing or yesterday's artifact — not flaky, WRONG,
 *     which is worse. (Rule 21's own corollary applies: probe by content, not by status.)
 *   · The Pages bot comment does carry an SHA-pinned, per-deployment URL — measured on
 *     PR #162: `Latest commit: 5617aa3`, `Preview URL: https://0721897a.<project>.pages.dev`,
 *     edited in place from "building" to "successful" 4m23s after it appeared. Polling it
 *     would work for this repo's own branches and would fail, every time, for a fork PR
 *     (no preview is built) and for a plain push to `dev` (no PR, no comment). A gate that
 *     is red for every outside contributor is a gate people learn to ignore.
 *   · And clause 2 — the whole reason this file exists — is UNTESTABLE on a preview, as
 *     measured above: no `*.pages.dev` host gets the beacon.
 *
 * So the wiring is deliberately split, and neither half is a merge gate on an external
 * service:
 *
 *     sim/ci/ci.yml       `--selftest`, hermetic, in the existing browser job. Gates PRs.
 *                         Proves the instrument has teeth on every push.
 *     sim/ci/deployed.yml the real thing, against the live deployment, on a SCHEDULE and
 *                         on `workflow_dispatch` (with a `url` input). Reliable because
 *                         production is settled long before the cron fires, and it is the
 *                         only place clause 2 can run. It gates nothing, so a Cloudflare
 *                         hiccup can never block a merge — it reddens a monitor instead,
 *                         which is what an external dependency deserves.
 *
 * Run it by hand against anything at any time:
 *
 *     node sim/check_deployed.mjs https://<a-branch-preview>.pages.dev/sim
 */
import { readFileSync, writeFileSync, existsSync, mkdtempSync, cpSync, appendFileSync,
         rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { requireBrowser, serveStatic, pagesHeaders, makeChecks, finish, web } from "./browser_harness.mjs";

const LABEL = "deployed-composer check";

/* ---- the target ----------------------------------------------------------- *
 * argv wins over the environment, and the environment wins over the site's own
 * canonical link. A `--flag` is never a URL, so the two can share argv[2]. */
const argv = process.argv.slice(2);
const SELFTEST = argv.includes("--selftest");
const cliUrl = argv.find((a) => !a.startsWith("-"));

/**
 * Where this bundle says it lives, read out of the page rather than typed here.
 *
 * `sim/web/index.html` carries `<link rel="canonical" href="…">` (so do setup, cloud and
 * docs; sim.html does not, which is why the hub is the one read). A fork that re-points
 * that line re-points this tool with it, and `test_no_deployment_defaults.py`'s rule —
 * no deployment hostname as a default in shipped code — is honoured rather than dodged.
 */
function canonicalOrigin() {
  const html = readFileSync(join(web, "index.html"), "utf8");
  const m = html.match(/<link\s+rel=["']canonical["']\s+href=["']([^"']+)["']/i);
  if (!m) return null;
  try { return new URL(m[1]).origin; } catch { return null; }
}

/**
 * `true` when this deployment SHOULD be carrying Cloudflare's injected beacon.
 *
 * Derived, because it is a property of the platform rather than of our code, and every
 * clause below was measured on 2026-09-05 rather than assumed:
 *
 *   · loopback / `.test` / `.localhost` — nothing injects anything; this is `--selftest`
 *     and a developer's own `python3 sim/serve.py`.
 *   · `*.pages.dev` — a Pages deployment served from Cloudflare's own domain. `server:
 *     cloudflare` is present and the beacon is NOT: 23 425 bytes on `dev.<project>
 *     .pages.dev`, `<project>.pages.dev` and a branch preview, against 23 792 on the
 *     custom domain. Web Analytics auto-injection belongs to the ZONE, and `pages.dev`
 *     is not our zone.
 *   · anything else over https — the custom domain, where the beacon is real and where
 *     the CSP defect this file exists for actually happened.
 *
 * `MOXIE_EXPECT_BEACON=1|0` overrides in both directions, for the deployment that turns
 * Web Analytics off (or on for a preview) without needing this heuristic edited.
 */
function expectsBeacon(url) {
  const env = process.env.MOXIE_EXPECT_BEACON;
  if (env === "1" || env === "true") return true;
  if (env === "0" || env === "false") return false;
  const u = new URL(url);
  if (u.protocol !== "https:") return false;
  const h = u.hostname.toLowerCase();
  if (/(^|\.)pages\.dev$/.test(h)) return false;
  return true;
}

/* The device the defect was measured on: iPhone 12/13/14-class logical size, a real iOS
 * Safari UA, touch, dsf 3. The UA matters twice over — `env.js` and the beacon injection
 * both read the request, and Chrome's default `HeadlessChrome/…` string is not a phone. */
const PHONE = { width: 390, height: 844, isMobile: true, hasTouch: true, deviceScaleFactor: 3 };
const IOS_UA =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 " +
  "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1";

/** Routes that cost money. Aborted at the browser so this file can promise it spends nothing. */
const SPENDING = /\/api\/(chat|speech|transcriptions|transcribe)\b/;

/**
 * Load `url` on a phone and report what a first-time visitor would find.
 *
 * Returns a plain record; the caller decides what is a failure. That split is what lets
 * `--selftest` demand a red from the same code path that demands a green from production,
 * instead of a second implementation that could disagree with this one.
 */
async function probe(browser, url, { settleMs = 2000 } = {}) {
  const page = await browser.newPage();
  await page.setViewport(PHONE);
  await page.setUserAgent(IOS_UA);

  /* The violation EVENT, not its console rendering, installed before a single page script
   * runs — `test_csp.mjs` argues this at length: a console line is a sentence to regex,
   * `securitypolicyviolation` carries the directive and the blocked URI, and it fires for
   * refusals that log nothing at all. */
  await page.evaluateOnNewDocument(() => {
    window.__csp = [];
    document.addEventListener("securitypolicyviolation", (e) => {
      window.__csp.push({
        directive: e.effectiveDirective || e.violatedDirective,
        blocked: e.blockedURI,
        sample: (e.sample || "").slice(0, 80),
      });
    });
  });

  const net = [];         // every response: url + status
  const failed = [];      // requests the network layer never completed
  const blocked = [];     // the spending routes we refused, so the report can say "none"
  await page.setRequestInterception(true);
  page.on("request", (r) => {
    if (r.isInterceptResolutionHandled()) return;
    if (SPENDING.test(r.url())) { blocked.push(r.url()); return r.abort("blockedbyclient"); }
    return r.continue();
  });
  page.on("response", (r) => net.push({ url: r.url(), status: r.status() }));
  page.on("requestfailed", (r) => {
    if (SPENDING.test(r.url())) return;                       // ours, on purpose
    failed.push({ url: r.url(), why: r.failure()?.errorText || "?" });
  });
  const consoleErrs = [];
  page.on("console", (m) => { if (m.type() === "error") consoleErrs.push(m.text()); });
  page.on("pageerror", (e) => consoleErrs.push("PAGEERR " + e.message));

  const res = await page.goto(url, { waitUntil: "load", timeout: 45000 });

  /* Settle, but not for long. `mode.js` polls `/api/health` and `env.js` paints the
   * banner off it (both affect the geometry), and `sim.html`'s stage offset is measured
   * after layout — 2 s covers all three. It must stay WELL under the ~7 s at which she
   * speaks unprompted: nothing here should be racing the page's own life. */
  await page.waitForFunction("!!document.getElementById('speech-input')", { timeout: 15000 })
            .catch(() => {});

  /* ---- clause 4's wait: every script on the page has to LEAVE A MARK ----------
   *
   * The one breakage nothing here could see, measured on 2026-09-06 by
   * `sim/tools/page_teeth_check.py`: serve `hud.js` — or `moxie.js`, or `mode.js`, or
   * `qr.js` — **200 OK and inert**, and this file exited 0 with 88 checks. The tag
   * resolves, the request log is clean, the status is 200, no exception is thrown and no
   * console line is printed, so clause 3 (an asset that FAILED) is blind by construction
   * and so is every listener a suite could install. There is no generic detector for it.
   * The only thing that separates a script that ran from one that was merely delivered is
   * AN EFFECT THAT SCRIPT IS SUPPOSED TO HAVE, named one file at a time.
   *
   * Each signal below is something the page already does because of what it is for — not
   * a flag added for the test. A `window.__loaded` marker or a `data-ran` attribute would
   * make this pass by making the product carry scaffolding, and the next person to read
   * `sim.html` deletes it as dead weight; then the check goes green forever.
   *
   * WHY IT IS A WAIT AND NOT JUST AN ASSERTION AFTER THE SETTLE. `moxie.js` is an ES
   * module (deferred, and it has three.js to fetch) and `mode.js` answers over the
   * network, so on a cold real deployment the marks land at their own pace. Sampling them
   * at a fixed 2 s is defect 2/3/4's shape all over again — a check that can only fail on
   * a slow machine. So: poll for all four, then measure. `.catch(() => {})` on purpose —
   * an expired wait must fall through to the assertions and REPORT which mark is missing,
   * not throw a timeout that says nothing about which script died.
   *
   * 8 s is chosen against `mode.js`'s own `PROBE_TIMEOUT_MS` (6 s): past that it has
   * given up on `/api/health` and settled into `offline`, so a deployment whose health
   * route hangs still resolves rather than racing this wait. */
  await page.waitForFunction(() => {
    const named = document.querySelectorAll('#motors input[type="range"][aria-label]').length;
    const mode = document.body && document.body.getAttribute("data-mode");
    return !!document.querySelector("#app canvas")               // moxie.js built the stage
        && document.querySelectorAll("#motors .motor").length > 0  // moxie.js built the panel
        && named > 0                                             // hud.js named the sliders
        && !!mode && mode !== "boot";                            // mode.js answered
  }, { timeout: 8000, polling: 200 }).catch(() => {});

  await new Promise((r) => setTimeout(r, settleMs));

  /* The measurement runs INSIDE the page, and it is written INLINE rather than passed in
   * as a source string to `eval()`. That is not a style choice: the shipped CSP has no
   * `'unsafe-eval'`, so an `eval()` here would be REFUSED — and it would be refused by
   * firing a `securitypolicyviolation`, i.e. the instrument would manufacture the exact
   * defect clause 2 is watching for. Puppeteer serialises this function and invokes it
   * through `Runtime.callFunctionOn`, which CSP has no opinion about.
   *
   * `self` is true when the hit is the element OR a descendant — a tap on the `<span>`
   * inside a button is a tap on the button, and demanding strict identity would fail a
   * correct page. Same rule `test_mobile_layout.mjs` settled on. */
  const view = await page.evaluate(() => {
    const measure = (sel) => {
      const el = document.querySelector(sel);
      if (!el) return { found: false };
      const r = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      const out = {
        found: true,
        w: Math.round(r.width), h: Math.round(r.height),
        top: Math.round(r.top), bottom: Math.round(r.bottom),
        left: Math.round(r.left), right: Math.round(r.right),
        display: cs.display, visibility: cs.visibility, pointerEvents: cs.pointerEvents,
      };
      if (r.width <= 0 || r.height <= 0) return { ...out, sized: false };
      const hit = document.elementFromPoint(
        Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2));
      return {
        ...out, sized: true,
        self: !!hit && (hit === el || el.contains(hit)),
        hit: hit ? (hit.id ? "#" + hit.id : hit.tagName.toLowerCase() +
                    (hit.className ? "." + String(hit.className).trim().split(/\s+/)[0] : "")) : "null",
      };
    };
    return {
      scrollY: Math.round(window.scrollY),
      innerW: window.innerWidth, innerH: window.innerHeight,
      title: document.title,
      hasHud: !!document.getElementById("hud"),
      hasDock: !!document.getElementById("chat-dock"),
      railOpen: document.getElementById("rail-toggle")?.getAttribute("aria-expanded") ?? null,
      input: measure("#speech-input"),
      button: measure("#speech-btn"),
      beaconTags: [...document.querySelectorAll("script[src]")]
        .map((s) => s.src).filter((s) => /cloudflareinsights\.com/.test(s)),
      csp: window.__csp || [],
      /* One cheap, specific, per-file mark. Each is picked because ONLY that script
       * produces it and the page ships without it:
       *   stage   `moxie.js` appends the three.js renderer's own <canvas> to #app.
       *   motors  `moxie.js::buildPanel` writes the motor rows into an EMPTY <div
       *           id="motors"> (sim.html:130) and the expression glyphs into #faces.
       *   named   `hud.js::labelMotors` copies each row's visible text onto its slider as
       *           an `aria-label` — the a11y fix it exists for. moxie.js writes the rows
       *           WITHOUT one, so the attribute is hud.js's signature and nothing else's.
       *   mode    `env.js::paintBadge` writes body[data-mode] from `mode.js`'s answer, and
       *           writes the literal "boot" when there is no answer to paint. So a value
       *           other than "boot" means mode.js decided something.
       *   badge   `env.js` CREATES the .env-badge span; sim.html has no such element.
       * Deliberately NOT used, having been checked against the markup: body[data-bus]
       * (sim.html ships data-bus="idle" and hud.js's first sync computes "idle" from "not
       * connected" — identical either way), #link-label (ships the exact text hud.js would
       * write), and #alive-toggle's class/aria-pressed (both ship set). Three signals that
       * look like witnesses and are markup. */
      ran: {
        stage: document.querySelectorAll("#app canvas").length,
        motors: document.querySelectorAll("#motors .motor").length,
        faces: document.querySelectorAll("#faces .face-emoji").length,
        named: document.querySelectorAll('#motors input[type="range"][aria-label]').length,
        mode: document.body ? document.body.getAttribute("data-mode") : null,
        badge: (document.querySelector("#topbar .env-badge") || {}).textContent || "",
      },
    };
  });

  /* The one PROVOKED signal, and it runs AFTER `view` on purpose: every clause above is a
   * claim about a page nobody has touched, so nothing may be clicked until the geometry
   * has been read.
   *
   * `qr.js` has no effect at all until someone asks for a code — it defines encoders and
   * a canvas renderer and does nothing on load — so it is the case the prompt's "assert an
   * observable effect" rule has to be provoked into. Pressing Make is free (no network, no
   * spend: the encoders are pure string work and `qrcode.js` draws locally) and the
   * default #qr-kind is OPEN_MOXIE, which needs no typed input.
   *
   * INK, NOT PIXEL VALUE. `d[i] < 128` alone counts a canvas NOBODY EVER DREW ON: an
   * untouched 2-D canvas is rgba(0,0,0,0) everywhere, so its red channel is 0 and every
   * pixel reads "dark" — 45000 of them on the 300x150 default. The alpha term is what
   * makes this a measurement of ink. Same trap, same fix, as test_csp.mjs's two QR checks.
   *
   * It is also the ONE signal that covers two files at once, which is a feature rather
   * than sloppiness: the button's listener lives in `hud.js` and bails at `!window.moxieQR`,
   * the encoder and the renderer live in `qr.js`, so an inert EITHER leaves the canvas
   * blank. `named` above is what separates the two when it happens. */
  const qr = await page.evaluate(async () => {
    const btn = document.getElementById("qr-make");
    const cv = document.getElementById("qr-canvas");
    const st = document.getElementById("qr-status");
    if (!btn || !cv) return { present: false, ink: 0, status: "" };
    btn.click();
    await new Promise((r) => setTimeout(r, 300));
    let ink = 0;
    try {
      const d = cv.getContext("2d").getImageData(0, 0, cv.width, cv.height).data;
      for (let i = 0; i < d.length; i += 4) if (d[i + 3] > 0 && d[i] < 128) ink++;
    } catch (e) {
      return { present: true, ink: 0, status: "readback failed: " + (e && e.message) };
    }
    return { present: true, ink, w: cv.width, h: cv.height,
             status: (st && st.textContent) || "" };
  });

  await page.close();
  return { url, status: res ? res.status() : 0, headers: res ? res.headers() : {},
           net, failed, blocked, consoleErrs, qr, ...view };
}

/* ---- the assertions, over one probe record -------------------------------- */
/**
 * @param {*} c        a `makeChecks()` bundle
 * @param {*} p        a `probe()` record
 * @param {string} tag prefix for every message, so a multi-target run reads clearly
 */
function assertReachable(c, p, tag, { expectBeacon }) {
  const { ok, eq } = c;
  const origin = new URL(p.url).origin;

  eq(p.status, 200, `${tag}: HTTP status of ${p.url}`);
  /* Rule 21's corollary: a MISSING route on Pages answers 200 with the STATIC HTML
   * FALLBACK, not 404, so a status code cannot tell you the SIM page loaded. Identity
   * therefore needs an anchor — and the anchor must NOT be `#chat-dock`, however tempting.
   * Verified against the pre-#162 tree: a build with no dock would then report "is this
   * really the SIM page?" for what is in fact the SIM page with the exact regression this
   * file hunts, i.e. the loudest evidence would be filed under the wrong heading. `#hud` is
   * the page's outermost furniture, predates every control under test here, and is not
   * something this check has an opinion about. The dock is reported as context instead. */
  ok(p.hasHud, `${tag}: #hud exists — is ${p.url} really the SIM page? (title ${JSON.stringify(p.title)})`);
  // Nothing below is meaningful if the page scrolled itself: "in the first viewport"
  // is a claim about a page nobody has touched.
  eq(p.scrollY, 0, `${tag}: the page must not have scrolled on its own`);
  // And nothing below is meaningful if the engineering drawer opened itself, which is
  // exactly the state the owner's instruction was about.
  ok(p.railOpen === null || p.railOpen === "false",
     `${tag}: the CONTROLS rail must still be closed on load (aria-expanded=${p.railOpen})`);

  for (const [sel, m] of [["#speech-input", p.input], ["#speech-btn", p.button]]) {
    ok(m.found, `${tag}: ${sel} exists`);
    if (!m.found) continue;
    ok(m.sized, `${tag}: ${sel} has a non-zero box — got ${m.w}×${m.h} ` +
                `(display:${m.display} visibility:${m.visibility})`);
    if (!m.sized) continue;
    ok(m.top >= 0 && m.bottom <= p.innerH,
       `${tag}: ${sel} is inside the first viewport — box y ${m.top}…${m.bottom} of ${p.innerH}`);
    ok(m.left >= 0 && m.right <= p.innerW,
       `${tag}: ${sel} is inside the viewport horizontally — x ${m.left}…${m.right} of ${p.innerW}`);
    ok(m.self, `${tag}: a tap at the centre of ${sel} reaches it — elementFromPoint gave ${m.hit}`);
  }

  /* ---- clause 3: every asset the PAGE asked for actually arrived ----------------
   * Until 2026-09-06 `net`, `failed` and `consoleErrs` were collected, PRINTED as
   * `failed requests: 0   console errors: 0` — and asserted NOWHERE. Three numbers that
   * read like a verdict and were decoration: a production build whose `sim.html`
   * referenced a script that 404s would print the 404 and still exit ✅. That is not
   * hypothetical for this page. Thirteen inline `<script>` blocks became thirteen
   * `<script src>` files in the CSP pass, and the file's own note for that work says a
   * page whose glue fails to load "still paints its markup and its CSS" — i.e. exactly
   * the failure every clause above survives.
   *
   * The proof it was live is in this file's own selftest: the BASELINE reported
   * `console errors: 1` and passed with `fired: NOTHING`.
   *
   * WHY THE CLAUSE IS NOT `consoleErrs.length === 0`, which is the obvious version and is
   * WRONG. That one 404 is `mode.js` polling same-origin `GET /api/health`, and on a
   * static origin with no Functions a 404 there is not a fault — it is HOW THE PAGE
   * DECIDES IT IS `offline`. Asserting zero console errors would redden the selftest
   * baseline, every fork with no Functions, and `sim/serve.py`. So the claim is narrower
   * and is about PAGE ASSETS: everything same-origin that is not under `/api/` must have
   * come back below 400 and must not have failed at the network layer. `/api/` is the
   * page's own feature detection and third parties are somebody else's uptime — neither
   * belongs in a clause that can redden a monitor. */
  const asset = (u) => {
    if (!u.startsWith(origin + "/")) return false;         // third parties are not ours
    return !/\/api\//.test(u);                             // /api/* is feature detection
  };
  const badAssets = p.net.filter((r) => asset(r.url) && r.status >= 400)
                         .map((r) => `${r.status} ${r.url}`);
  eq(badAssets.length, 0,
     `${tag}: every page asset loaded — ${badAssets.length} did not: ${JSON.stringify(badAssets)}`);
  const deadAssets = p.failed.filter((f) => asset(f.url)).map((f) => `${f.url} — ${f.why}`);
  eq(deadAssets.length, 0,
     `${tag}: no page asset failed at the network layer — ${JSON.stringify(deadAssets)}`);

  /* ---- clause 4: the scripts did not merely ARRIVE, they RAN -------------------
   *
   * The last open member of the family clause 3 closed half of. Clause 3 asks whether the
   * bytes turned up; a script served 200 OK and INERT answers yes and does nothing. That
   * is not hypothetical — it is what a bad minify, a truncated upload, a module that
   * throws before its first side effect, or a `<script>` whose dependency moved all look
   * like on the wire, and `sim/tools/page_teeth_check.py` reproduces it exactly (`gut`).
   *
   * There is deliberately NO generic rule here. "A global is defined" is brittle and most
   * of the page is ESM; "count the requests" gives the identical count (the file WAS
   * fetched); "diff the DOM" is huge, noisy, and gets loosened the first time it flaps.
   * One named mark per file is the only shape that stays true and stays cheap, and it is
   * the reason each clause below says which file it is about — a red here points at one
   * script, which is the whole value over a screenshot diff.
   *
   * WHAT THIS DOES NOT COVER, said out loud so nobody reads a green here as "every script
   * ran": `sw-reset.js`, `stub.js`, `bridge.js`, `audio.js`, `life.js`, `mic.js`,
   * `rail.js`, `turnstile.js`, `cloud-transport.js` and `ambient.js` are all loaded by
   * sim.html and none is asserted here. Some have no observable effect on an untouched
   * page (`stub.js` and `cloud-transport.js` answer a turn that has not been taken;
   * `turnstile.js` renders nothing without a sitekey); `ambient.js` has one but it is ~7 s
   * away and this file must stay well inside that. They are the honest remainder. */
  const r = p.ran;
  ok(r.stage > 0,
     `${tag}: moxie.js ran — the three.js renderer appended its <canvas> to #app (found ${r.stage})`);
  ok(r.motors > 0 && r.faces > 0,
     `${tag}: moxie.js ran — buildPanel() filled the empty #motors and #faces ` +
     `(${r.motors} motor rows, ${r.faces} expression glyphs)`);
  ok(r.motors > 0 && r.named === r.motors,
     `${tag}: hud.js ran — labelMotors() gave every motor slider its accessible name ` +
     `(${r.named} named of ${r.motors} rows)`);
  ok(!!r.mode && r.mode !== "boot",
     `${tag}: mode.js ran — body[data-mode] left "boot" for this deployment's own answer ` +
     `(got ${JSON.stringify(r.mode)})`);
  ok(!!r.badge,
     `${tag}: env.js ran — it built the environment badge in the topbar (got ${JSON.stringify(r.badge)})`);
  ok(p.qr.present && p.qr.ink > 500,
     `${tag}: qr.js ran — pressing Make drew a real code, opaque ink and not an untouched ` +
     `canvas (${p.qr.ink} ink px on ${p.qr.w}x${p.qr.h})`);
  ok(p.qr.present && /\{/.test(p.qr.status),
     `${tag}: qr.js ran — …and printed the JSON payload it encoded (${JSON.stringify(String(p.qr.status).slice(0, 48))})`);

  /* ---- the beacon reality (clause 2) ---- */
  eq(p.csp.length, 0, `${tag}: ZERO securitypolicyviolation events on load — ` +
     JSON.stringify(p.csp.slice(0, 4)));

  if (expectBeacon) {
    ok(p.beaconTags.length > 0,
       `${tag}: Cloudflare's Web Analytics beacon must be in the served HTML. ` +
       `None found. If this deployment has Web Analytics off, run with ` +
       `MOXIE_EXPECT_BEACON=0 — but then the CSP entry for static.cloudflareinsights.com ` +
       `in sim/web/_headers is guarding nothing and should be revisited.`);
  }
  for (const src of p.beaconTags) {
    const r = p.net.find((x) => x.url === src);
    ok(!!r, `${tag}: the beacon ${src} produced a response at all`);
    if (r) eq(r.status, 200, `${tag}: beacon ${src} loaded`);
    ok(!p.csp.some((v) => /cloudflareinsights/.test(v.blocked || "")),
       `${tag}: the beacon was not refused by the CSP`);
    ok(!p.failed.some((f) => f.url === src),
       `${tag}: the beacon request did not fail — ` +
       JSON.stringify(p.failed.filter((f) => f.url === src)));
  }
}

/** One line per measurement, so a run leaves numbers behind rather than a verdict. */
function report(p, { expectBeacon }) {
  const box = (m) => m.found
    ? (m.sized ? `${m.w}×${m.h} at y=${m.top}…${m.bottom}  hit=${m.hit}${m.self ? " (self)" : " ⚠ NOT SELF"}`
               : `${m.w}×${m.h}  display:${m.display} visibility:${m.visibility}`)
    : "ABSENT";
  console.log(`\n  ${p.url}`);
  console.log(`    HTTP ${p.status}   viewport ${p.innerW}×${p.innerH}   scrollY ${p.scrollY}` +
              `   rail aria-expanded=${p.railOpen}   #hud ${p.hasHud ? "yes" : "NO"}   #chat-dock ${p.hasDock ? "yes" : "NO"}`);
  console.log(`    #speech-input   ${box(p.input)}`);
  console.log(`    #speech-btn     ${box(p.button)}`);
  console.log(`    beacon          ${p.beaconTags.length ? p.beaconTags.map((s) => s.slice(0, 78)).join(", ") : "(none)"}` +
              `   expected: ${expectBeacon ? "yes" : "no"}`);
  console.log(`    CSP violations  ${p.csp.length}${p.csp.length ? "  " + JSON.stringify(p.csp.slice(0, 3)) : ""}`);
  // The marks clause 4 reads, printed as NUMBERS rather than as a verdict — the same
  // discipline the mutation list below records: a mutation that quietly mutates nothing is
  // only ever caught because a run leaves its measurements behind.
  console.log(`    scripts ran     moxie.js: ${p.ran.stage} stage canvas, ${p.ran.motors} motors, ` +
              `${p.ran.faces} faces   hud.js: ${p.ran.named} named sliders   ` +
              `mode.js: data-mode=${JSON.stringify(p.ran.mode)}   env.js: badge ${JSON.stringify(p.ran.badge)}`);
  console.log(`    qr.js           ${p.qr.present ? `${p.qr.ink} ink px on ${p.qr.w}x${p.qr.h}` : "NO #qr-make/#qr-canvas"}` +
              `   payload ${JSON.stringify(String(p.qr.status).slice(0, 46))}`);
  console.log(`    spending routes aborted: ${p.blocked.length}   failed requests: ${p.failed.length}` +
              `   console errors: ${p.consoleErrs.length}`);
  if (p.failed.length) for (const f of p.failed.slice(0, 5)) console.log(`      · failed ${f.url} — ${f.why}`);
  if (p.consoleErrs.length) for (const e of p.consoleErrs.slice(0, 5)) console.log(`      · console ${e.slice(0, 140)}`);
}

/* ─────────────────────────── selftest: the teeth ─────────────────────────── *
 * Mutated COPIES of `sim/web`, served from loopback under the real `_headers` policy.
 * Each mutation is CSS appended to `style.css` — appended rather than edited, so it
 * cannot silently fail to match a selector that moved, and it lands last so it wins the
 * cascade.
 *
 * They are not decorative. A is the pre-#162 state as MEASURED on the live site (`0 × 0`
 * on load); B is that state's other half (`262 × 40 at y = 2095` of an 844 px viewport,
 * after the drawer was opened); C is the `#env-banner` collision `test_mobile_layout.mjs`
 * was written for, aimed at the composer instead of the rail handle.
 *
 * C MOVES A REAL ELEMENT, AND TWO FAILED DRAFTS ARE WHY. It first hung off
 * `#notice::after` — no collision, because `#notice` is empty and unrendered until the
 * banner paints. It then hung off `body::after`, which the computed style confirmed was
 * live and winning the cascade (`position: fixed`, `z-index: 99`, `height: 320px`,
 * `pointer-events: auto`, laid over the composer) — and `document.elementFromPoint` STILL
 * returned `#speech-input`. Chrome does not hand back a pseudo-element's originating
 * element from that call, so a `::after` overlay is invisible to the very assertion this
 * mutation exists to trip. So C relocates `#topbar`, an always-rendered element with real
 * content, on top of the dock — which is also the honest shape of the defect being
 * imitated, since `#env-banner` was a real element too.
 *
 * Both drafts were mutations that quietly mutated nothing, which is the vacuous-teeth
 * shape this repo keeps finding, and both were caught only because a run PRINTS which
 * clause each mutation fired rather than merely that one did. Keep that output.
 *
 * SERVED UNDER A MAPPED HOSTNAME, NOT `127.0.0.1`, and that is load-bearing rather than
 * cosmetic — it cost a red baseline to learn. On a loopback origin `env.js`/`audio.js`
 * probe the OPTIONAL LOCAL SIDECARS at `:8081` and `:8082`, which `connect-src 'self'`
 * refuses (a different port is a different origin), so the unmutated tree fired two
 * `securitypolicyviolation` events and clause 2 went red against a page that is perfectly
 * healthy. Those probes are a local-development branch that no deployment ever takes.
 * `test_mobile_layout.mjs` and `test_csp.mjs` both map a `.test` hostname for the same
 * class of reason; this does it per-target so all four servers can share one browser.
 *
 * D IS NOT CSS, and that is the point of it. Clause 3 is about an asset that never
 * arrives, which no stylesheet can imitate, so D deletes a shipped script from the copied
 * tree instead. `qr.js` is chosen because losing it changes NO geometry — so D reddens
 * clause 3 and nothing else, which is what makes it a test of clause 3 rather than of the
 * layout clauses that were already covered.
 *
 * E…I ARE THE SUBTLER HALF OF D, and clause 4 exists because of them. A file served
 * **200 OK and inert** is a real 200 with a real body and no code: D's own instrument
 * cannot see it, because nothing failed. Each one gutts exactly one script and must
 * redden exactly the clause that names that script — which is also the only proof that
 * the marks clause 4 reads are attributable one file at a time rather than four
 * assertions that fail together.
 *
 * `wanted` for E and F is deliberately not the same string: gutting `moxie.js` takes
 * `hud.js`'s mark down with it (there are no sliders left to name), while gutting
 * `hud.js` leaves every moxie.js mark standing. That asymmetry is the discrimination
 * being tested, so E must fire the MOXIE clause and F the HUD one.
 */
const GUT = "/* --selftest mutation: this file was served 200 OK and did nothing. */\n";
const gut = (name) => (dir) => writeFileSync(join(dir, name), GUT);

const MUTATIONS = [
  // [name, appended CSS, the clause it MUST fire, a mutate(dir) fn, the file it needs]
  ["A · dock hidden (the pre-#162 0×0 state)",
   `#chat-dock { display: none !important; }`,
   /non-zero box/],
  ["B · dock pushed below the fold (y≈2095 of 844)",
   `#chat-dock { position: absolute !important; top: 2095px !important; left: 0; right: 0; }`,
   /inside the first viewport/],
  ["C · the title bar relocated over the composer (the #env-banner collision)",
   `#topbar { position: fixed !important; top: auto !important; left: 0; right: 0; bottom: 0;
      height: 320px; z-index: 99; pointer-events: auto; }`,
   /reaches it — elementFromPoint/],
  ["D · a shipped script is missing from the build (qr.js 404s)",
   null,
   /every page asset loaded/,
   (dir) => rmSync(join(dir, "qr.js"), { force: true }),
   "qr.js"],
  ["E · moxie.js served 200 OK and INERT (the stage never boots)",
   null, /moxie\.js ran/, gut("moxie.js"), "moxie.js"],
  ["F · hud.js served 200 OK and INERT (no HUD glue ever wires up)",
   null, /hud\.js ran/, gut("hud.js"), "hud.js"],
  ["G · mode.js served 200 OK and INERT (the deployment never decides what it is)",
   null, /mode\.js ran/, gut("mode.js"), "mode.js"],
  ["H · env.js served 200 OK and INERT (no badge, no banner, no needs-backend marks)",
   null, /env\.js ran/, gut("env.js"), "env.js"],
  ["I · qr.js served 200 OK and INERT (Make is wired and encodes nothing)",
   null, /qr\.js ran/, gut("qr.js"), "qr.js"],
];

/* `{ force: true }` on D's delete, and the anchor asserted separately in `selftest()`
 * below, because of a defect this file HAD: with `sim/web/qr.js` already missing from the
 * tree (which is exactly what `page_teeth_check.py`'s `qr-gone` row does), `rmSync` threw
 * ENOENT inside `mutatedCopy` — during server SETUP, before a single probe ran. The suite
 * exited non-zero with a stack trace and the audit scored the deletion as "caught". It was
 * not caught; it was a crash that happened to have the right exit code, and it would have
 * scored identically had every clause in this file been deleted. Now the copy cannot
 * crash, the missing anchor is its own named check, and the deletion is caught where it
 * always should have been: the BASELINE target 404s on qr.js and clause 3 reddens. */
function mutatedCopy(css, mutate) {
  const dir = mkdtempSync(join(tmpdir(), "moxie-deployed-"));
  cpSync(web, dir, { recursive: true });
  if (css) appendFileSync(join(dir, "style.css"), "\n/* --selftest mutation */\n" + css + "\n");
  if (mutate) mutate(dir);
  return dir;
}

async function selftest(puppeteer, chrome) {
  const c = makeChecks();
  const headers = pagesHeaders();

  /* Every server first, so every port is known before the browser starts: Chrome takes
   * its resolver rules at LAUNCH, and one browser for all four targets keeps the run to a
   * single profile (and a single set of GPU warnings). */
  /* A mutation whose target file is not in the tree mutates NOTHING, and the tree is not
   * a constant: `page_teeth_check.py` deletes shipped scripts on purpose, and so does a
   * bad merge. Asserted here, as a check, rather than being discovered as an exception
   * out of `mutatedCopy` — see its note. */
  for (const [name, , , , anchor] of MUTATIONS) {
    if (!anchor) continue;
    c.ok(existsSync(join(web, anchor)),
         `mutation ${name} needs sim/web/${anchor} to exist before it can break it — ` +
         `the file is missing from the tree, so this mutation proves nothing`);
  }

  const targets = [];
  for (const [i, entry] of [[0, null], ...MUTATIONS.map((m, n) => [n + 1, m])]) {
    const [name, css, wanted, mutate] = entry || ["baseline (the tree as committed)", null, null];
    const site = await serveStatic(mutatedCopy(css, mutate), { headers });
    targets.push({ name, wanted, site, host: `moxie-selftest-${i}.hosted.test` });
  }
  const rules = targets.map((t) => `MAP ${t.host} 127.0.0.1:${t.site.port}`).join(",");
  const browser = await puppeteer.launch({
    executablePath: chrome, headless: "new",
    args: ["--no-sandbox", "--use-gl=swiftshader", "--enable-unsafe-swiftshader",
           `--host-resolver-rules=${rules}`],
  });

  try {
    for (const [i, t] of targets.entries()) {
      const url = `http://${t.host}:${t.site.port}/sim.html`;
      const m = makeChecks();
      const p = await probe(browser, url);
      report(p, { expectBeacon: false });
      assertReachable(m, p, i === 0 ? "baseline" : "mutant", { expectBeacon: false });
      console.log(`    → ${t.name}\n      fired: ${m.fails.length ? m.fails.map((f) => "· " + f).join("\n      ") : "NOTHING"}`);

      if (i === 0) {
        // The control, and it is not ceremony: a mutation test whose baseline does not
        // pass proves nothing whatsoever about the mutations underneath it.
        c.ok(m.fails.length === 0,
             `the UNMUTATED tree must pass every clause — ${m.fails.length} failure(s): ` +
             JSON.stringify(m.fails));
      } else {
        c.ok(m.fails.length > 0, `mutation ${t.name} must make the check FAIL — it passed`);
        c.ok(m.fails.some((f) => t.wanted.test(f)),
             `mutation ${t.name} must fire the ${t.wanted} clause specifically — ` +
             `got ${JSON.stringify(m.fails)}`);
      }
    }
  } finally {
    try { await browser.close(); } catch {}
    for (const t of targets) t.site.close();
  }
  return c;
}

/* ───────────────────────────────── main ─────────────────────────────────── */
const { puppeteer, chrome } = await requireBrowser(LABEL);

if (SELFTEST) {
  const c = await selftest(puppeteer, chrome);
  finish(LABEL + " (selftest)", c);
}

const target = cliUrl || process.env.MOXIE_DEPLOYED_URL ||
               (canonicalOrigin() ? canonicalOrigin() + "/sim" : null);
if (!target) {
  // Not a skip: being unable to work out WHAT to check is a defect in the invocation, and
  // a silent exit(0) here would be the "green while asserting nothing" shape this repo has
  // been bitten by twice (browser_harness.mjs::skipper records both).
  console.error(`❌ ${LABEL}: no target. Pass a URL, set MOXIE_DEPLOYED_URL, or restore ` +
                `the <link rel="canonical"> in sim/web/index.html.`);
  process.exit(1);
}

const browser = await puppeteer.launch({
  executablePath: chrome, headless: "new",
  // swiftshader: sim.html is a three.js page and a software GL context is what every other
  // browser suite here uses. `--no-sandbox` is the runner's requirement.
  args: ["--no-sandbox", "--use-gl=swiftshader", "--enable-unsafe-swiftshader"],
});
try {
  const expectBeacon = expectsBeacon(target);
  const c = makeChecks();
  const p = await probe(browser, target);
  report(p, { expectBeacon });
  assertReachable(c, p, "deployed", { expectBeacon });
  await browser.close();
  finish(LABEL, c);
} catch (err) {
  try { await browser.close(); } catch {}
  // A network failure against a real deployment is a RESULT, not an excuse: the clean skip
  // in browser_harness.mjs is reserved for "this machine has no browser", and even that is
  // a hard failure under CI.
  console.error(`❌ ${LABEL}: ${err && err.stack ? err.stack : err}`);
  process.exit(1);
}
