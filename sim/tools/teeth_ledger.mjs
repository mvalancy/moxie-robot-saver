/* teeth_ledger.mjs — per-CHECK instrumentation for the browser suites.
 *
 * NOT a test, and nothing in the repo imports it. It is loaded with
 * `node --import ./sim/tools/teeth_ledger.mjs sim/test_foo.mjs` by
 * `sim/tools/page_teeth_check.py`, and it exists to answer a question the suites
 * cannot answer about themselves: **which individual checks stayed GREEN while the
 * page underneath them was broken?**
 *
 * `browser_harness.mjs::finish` prints only the FAILURES plus a count. That is the
 * right output for a test run and the wrong one for an audit — "23 checks passed"
 * does not say which 23, so it cannot be diffed against a run where the page was
 * deliberately sabotaged. This hook rewrites two lines of `browser_harness.mjs`
 * **in memory, at load time** (an ESM `load` hook; the file on disk is never
 * touched) so that every `ok()` call records its message and its outcome.
 *
 * It also wraps `loadPuppeteer` so every page records the URLs it requested and the
 * status each came back with. That request log is the audit's RELEVANCE signal: a
 * suite is only in scope for "delete `qr.js`" if the suite's own healthy run
 * actually fetched `qr.js`. Measured, never assumed — the whole point of rule 30.
 *
 * Wrapping is deliberately PASSIVE. The recorder is a plain `page.on("request")`
 * listener, not `setRequestInterception`, because eleven of these suites intercept
 * requests themselves and a second interceptor would change what they are testing.
 *
 * Env:
 *   MOXIE_TEETH_LEDGER   path to write the JSON ledger to (required, else inert)
 *   MOXIE_TEETH_THROUGHPUT  bytes/s cap for the `stall` breakage (optional)
 */
import { register } from "node:module";
import { writeFileSync } from "node:fs";

const OUT = process.env.MOXIE_TEETH_LEDGER;
/* The `stall` breakage's other half. `page_teeth_check.py` pads ONE file to ~24 MB and
 * sets this cap, so that file takes ~24 s to arrive and every other resource on the page
 * still lands in milliseconds. A throttle rather than an interceptor on purpose: eleven
 * of these suites call `setRequestInterception` themselves and a second interceptor
 * would change what they are testing. */
const THROUGHPUT = Number(process.env.MOXIE_TEETH_THROUGHPUT || 0);

if (OUT) {
  const checks = [];
  const requests = [];
  const failed = [];
  const notes = [];
  let throttled = 0;
  const seen = new Map();

  /* A check's IDENTITY is its CALL SITE, not its message.
   *
   * Keying on the text was tried first and is wrong in a way that quietly destroys the
   * audit. `makeChecks().eq` formats its message as `${m} — got X, want Y`, and many
   * suites interpolate live values into the message they pass to `ok` — including
   * `test_docs_explorer.mjs`'s hero check, whose message carries the list of image
   * responses **in arrival order**. That order is not stable between two runs of the
   * SAME healthy tree, so a text key reported the check as having "vanished" and a
   * brand-new one as having appeared: the exact assertion the audit is about would have
   * been dropped from the comparison, silently, on the run that mattered.
   *
   * `file:line:col` of the frame that called `ok` is immutable across runs and immune to
   * anything the message interpolates. Frames inside `browser_harness.mjs` are skipped so
   * that `eq`, which calls `ok`, still resolves to the SUITE's own line. The occurrence
   * index keeps checks inside a loop distinct. */
  const site = () => {
    const lines = String(new Error().stack || "").split("\n").slice(1);
    for (const l of lines) {
      const m = l.match(/\/sim\/(test_[a-z0-9_]+\.mjs|check_[a-z0-9_]+\.mjs):(\d+):(\d+)/);
      if (m) return `${m[1]}:${m[2]}:${m[3]}`;
    }
    return "unknown";
  };

  globalThis.__teethRecord = (msg, passed) => {
    const k = site();
    const n = (seen.get(k) || 0) + 1;
    seen.set(k, n);
    checks.push({ i: checks.length, key: `${k}#${n}`, msg: String(msg), pass: !!passed });
  };

  globalThis.__teethWrapPuppeteer = (pptr) => {
    const instrument = async (page) => {
      try {
        page.on("request", (r) => requests.push(r.url()));
        page.on("response", (r) => requests.push(`${r.status()} ${r.url()}`));
        page.on("requestfailed", (r) =>
          failed.push(`${r.url()} ${((r.failure() || {}).errorText) || "?"}`));
      } catch (e) { notes.push("listener: " + e.message); }
      /* THE THROTTLE IS NOT ALLOWED TO FAIL QUIETLY.
       *
       * This was inside the same `catch {}` as the listeners for its first draft, and it
       * threw on every page — puppeteer 24 takes `{download, upload, latency}` and the CDP
       * names (`downloadThroughput`…) are rejected with "mandatory field missing". The
       * stall rows therefore ran against an UNTHROTTLED browser, the 24 MB file arrived in
       * 231 ms, every suite passed, and the audit would have reported "no findings" for
       * the entire not-loaded-yet family — the precise failure this tool exists to detect,
       * in the tool itself. A swallowed exception in an instrument is the instrument
       * lying. So it is recorded in the ledger and `page_teeth_check.py` refuses to read a
       * stall row whose ledger carries a note. */
      if (THROUGHPUT > 0) {
        try {
          await page.emulateNetworkConditions(
            { download: THROUGHPUT, upload: THROUGHPUT, latency: 0 });
          throttled++;
        } catch (e) {
          notes.push("emulateNetworkConditions FAILED: " + e.message);
          console.error("teeth_ledger: THROTTLE NOT APPLIED —", e.message);
        }
      }
      return page;
    };
    const wrapBrowser = (browser) => {
      const np = browser.newPage.bind(browser);
      browser.newPage = async (...a) => instrument(await np(...a));
      const ps = browser.pages.bind(browser);
      browser.pages = async (...a) => {
        const pages = await ps(...a);
        for (const p of pages) if (!p.__teethDone) { p.__teethDone = 1; await instrument(p); }
        return pages;
      };
      return browser;
    };
    return new Proxy(pptr, {
      get(t, k, r) {
        if (k !== "launch") return Reflect.get(t, k, r);
        return async (...a) => wrapBrowser(await t.launch(...a));
      },
    });
  };

  let written = false;
  const dump = () => {
    if (written) return;
    written = true;
    try {
      writeFileSync(OUT, JSON.stringify({ checks, requests, failed, notes, throttled }, null, 1));
    } catch {}
  };
  process.on("exit", dump);
  process.on("SIGTERM", () => { dump(); process.exit(143); });

  register(new URL("./teeth_hook.mjs", import.meta.url).href);
}
