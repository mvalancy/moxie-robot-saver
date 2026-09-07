/* diagram.js — Moxie draws. Renders a mermaid diagram she wrote, in the comms log.
 *
 * The source arrives on the response envelope as `diagram` (`functions/api/_lib/envelope.js`),
 * already separated from the words she SPEAKS by `chat.js::splitDiagram` — so nothing here
 * has to worry about syntax being read aloud, and nothing here should ever put source text
 * where a person expects prose.
 *
 * ============================================================================
 * WHY MERMAID IS LOADED LAZILY, AND ONLY FROM OUR OWN ORIGIN.
 *
 * `vendor/mermaid.min.js` is 3.3 MB — roughly the whole rest of this page put together.
 * Loading it on every visit to pay for a feature most turns never use would be a
 * straightforward regression in the thing the SIM is judged on (time to a robot on
 * screen), so it is fetched the first time she actually draws something and never before.
 *
 * AND IT COMES FROM `vendor/`, NOT A CDN. `sim/web/_headers` pins
 * `script-src 'self' 'sha256-…' https://static.cloudflareinsights.com
 * https://challenges.cloudflare.com`. A lazy load from anywhere else is refused by the
 * policy at fetch time — silently, from this file's point of view — and the feature simply
 * would not work in production while working perfectly on a local server with no headers.
 * `sim/test_csp.mjs` is the guard that catches that class of mistake; the same-origin
 * vendored copy is the path that does not need catching.
 *
 * `securityLevel: "strict"` — DELIBERATELY STRICTER THAN THE DOCS EXPLORER, which uses
 * "loose". `docs.js` renders diagrams WE wrote and committed; this renders text a language
 * model produced in response to whatever a stranger typed. Strict is what stops a diagram
 * carrying click handlers or raw HTML into the page. The rendered SVG is inserted as the
 * result of `mermaid.render`, under a CSP with no `unsafe-eval` and `object-src 'none'`.
 * ============================================================================
 */
(function () {
  "use strict";
  var loading = null, ready = false, seq = 0;

  /** Load the vendored bundle once. Resolves `false` if it cannot be had, so a caller can
   *  degrade rather than wait for something that is not coming. */
  function load() {
    if (ready) return Promise.resolve(true);
    if (loading) return loading;
    loading = new Promise(function (resolve) {
      var s = document.createElement("script");
      s.src = "vendor/mermaid.min.js";          // same origin: `script-src 'self'`
      s.async = true;
      s.onload = function () {
        try {
          window.mermaid.initialize({
            startOnLoad: false,
            theme: "dark",
            securityLevel: "strict",            // stricter than docs.js — see the header
            fontFamily: "inherit",
          });
          ready = true;
          resolve(true);
        } catch (e) { resolve(false); }
      };
      s.onerror = function () {
        // A CSP refusal lands here too, which is why this resolves rather than rejects:
        // "she could not draw" must never become an unhandled rejection on the page.
        loading = null;
        resolve(false);
      };
      document.head.appendChild(s);
    });
    return loading;
  }

  /** Is this source something mermaid will accept? Asked BEFORE rendering so a broken
   *  diagram never reaches the DOM half-drawn, and so the caller can ask for a repair. */
  function valid(src) {
    try {
      // `parse` throws on bad syntax. v10 returns a promise; v9 returns a boolean.
      var out = window.mermaid.parse(src);
      return out && typeof out.then === "function"
        ? out.then(function () { return true; }, function () { return false; })
        : Promise.resolve(!!out);
    } catch (e) { return Promise.resolve(false); }
  }

  var api = {
    /** Recorded facts, for the tests (playbook rule 11). */
    stats: { rendered: 0, invalid: 0, loadFailed: 0 },

    /**
     * Draw `src` into the comms log. Resolves `true` when a diagram really landed.
     *
     * Every failure path resolves FALSE and draws nothing: an unavailable bundle, a CSP
     * refusal, syntax mermaid rejects, a render that throws. She said her words either
     * way — the picture is the bonus, and a broken picture is worse than none.
     */
    render: function (src) {
      var source = String(src || "").trim();
      if (!source) return Promise.resolve(false);
      return load().then(function (ok) {
        if (!ok) { api.stats.loadFailed++; return false; }
        return valid(source).then(function (good) {
          if (!good) { api.stats.invalid++; return false; }
          var id = "moxie-diagram-" + (++seq);
          return Promise.resolve(window.mermaid.render(id, source)).then(function (out) {
            var svg = out && typeof out === "object" ? out.svg : out;
            if (!svg) { api.stats.invalid++; return false; }
            var log = document.getElementById("transcript");
            if (!log) return false;
            var row = document.createElement("div");
            row.className = "diagram";
            // NOT a `.turn`: `bridge.js::addTranscript` appends streamed reply chunks into
            // the last `.turn.moxie`, and a diagram carrying that class would have half a
            // sentence welded into it. Same reasoning as ambient's `.mutter` rows.
            row.setAttribute("role", "img");
            row.setAttribute("aria-label", "A diagram Moxie drew");
            row.innerHTML = svg;                 // mermaid's own output, strict mode
            log.appendChild(row);
            var atBottom = (log.scrollHeight - log.scrollTop - log.clientHeight) < 40;
            if (atBottom) log.scrollTop = log.scrollHeight;
            api.stats.rendered++;
            return true;
          }, function () { api.stats.invalid++; return false; });
        });
      });
    },
  };

  window.moxieDiagram = api;
})();
