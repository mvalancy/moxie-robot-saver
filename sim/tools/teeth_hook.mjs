/* teeth_hook.mjs — the ESM `load` hook half of `teeth_ledger.mjs`.
 *
 * Runs on Node's loader thread, so it must NOT reach for anything on the main thread:
 * all it does is hand back a rewritten SOURCE for a file. Two files get rewritten:
 *
 *   · `sim/browser_harness.mjs` — every `ok()` records its message and outcome, and
 *     `loadPuppeteer` is wrapped so pages log the URLs they requested.
 *   · any `sim/test_*.mjs` that still carries its OWN copy of `loadPuppeteer`
 *     (`test_responsive.mjs` and `test_env_hosted.mjs` do, and browser_harness.mjs's
 *     own header says a second hand-rolled copy is how these drift). Without this they
 *     would be invisible to the audit — no request log means no measured exposure,
 *     which would read as "not affected by anything" rather than "not instrumented".
 *     The wrapper is appended, not spliced: a `function` declaration hoists over the
 *     whole module, so it is in place before the top-level `await loadPuppeteer()`.
 *
 * Every rewrite REFUSES SILENTLY-NOTHING — a missing or non-unique anchor throws, so
 * the audit can never degrade to "no checks recorded" and report that as "no findings".
 */
const OK_ANCHOR = "  const ok = (c, m) => { n++; if (!c) fails.push(m); };";
const OK_PATCH =
  "  const ok = (c, m) => { n++; if (!c) fails.push(m);" +
  " try { globalThis.__teethRecord && globalThis.__teethRecord(m, !!c); } catch {} };";

const LP_EXPORTED = "export async function loadPuppeteer() {";
const LP_LOCAL = "async function loadPuppeteer() {";
const LP_RENAMED = "async function __teethLoadPuppeteerOrig() {";
const LP_TAIL = `
async function loadPuppeteer(...a) {
  const p = await __teethLoadPuppeteerOrig(...a);
  try { return (p && globalThis.__teethWrapPuppeteer) ? globalThis.__teethWrapPuppeteer(p) : p; }
  catch { return p; }
}
`;

function once(src, anchor, where) {
  const n = src.split(anchor).length - 1;
  if (n !== 1) throw new Error(`teeth_hook: anchor matched ${n} times in ${where}: ${anchor}`);
}

export async function load(url, context, nextLoad) {
  const res = await nextLoad(url, context);
  const harness = url.endsWith("/sim/browser_harness.mjs");
  const suite = /\/sim\/test_[a-z0-9_]+\.mjs$/.test(url);
  if (!harness && !suite) return res;
  let src = String(res.source);

  if (harness) {
    once(src, OK_ANCHOR, "browser_harness.mjs");
    once(src, LP_EXPORTED, "browser_harness.mjs");
    return {
      ...res,
      source: src.replace(OK_ANCHOR, OK_PATCH).replace(LP_EXPORTED, LP_RENAMED)
        + LP_TAIL.replace("async function loadPuppeteer(", "export async function loadPuppeteer("),
    };
  }

  // A suite with its own copy of the discovery helper. Suites that import the shared one
  // are already covered above and must be left byte-identical.
  if (!src.includes(LP_LOCAL)) return res;
  once(src, LP_LOCAL, url);
  return { ...res, source: src.replace(LP_LOCAL, LP_RENAMED) + LP_TAIL };
}
