/* diagram.js — Moxie draws. Renders a mermaid diagram she wrote, in the comms log.
 *
 * The source arrives on the response envelope as `diagram`, already split from the words
 * she speaks by `chat.js::splitDiagram`.
 *
 * Mermaid (3.3 MB) is loaded LAZILY, on her first drawing, and only from `vendor/`: the
 * CSP's script-src would silently refuse any other origin in production.
 * `securityLevel: "strict"` — stricter than the docs explorer's "loose" — because this
 * renders model output prompted by a stranger, not diagrams we committed.
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

    /** Draw `src` into the comms log; resolves `true` only when a diagram really landed.
     *  Every failure (bundle, CSP, syntax, render) resolves false and draws nothing — her
     *  words stand either way. */
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
            // NOT a `.turn`: `bridge/index.js::addTranscript` appends streamed chunks into
            // the last `.turn.moxie` (same reasoning as ambient's `.mutter` rows).
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
