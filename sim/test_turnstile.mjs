/* test_turnstile.mjs — the Cloudflare Turnstile bot control in front of the spending
 * routes, under bare node.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §4.1 (guard order; a refusal is free),
 * §4.2 (what the browser may know), §4.5 (the status table). Implementation:
 * `functions/api/_lib/turnstile.js`, the Turnstile step in `functions/api/chat.js` and
 * `transcribe.js`, `sim/web/turnstile.js`, `sim/web/mode.js`.
 *
 * The real handlers are imported and called with a synthetic `Request`; `fetch` is
 * stubbed, and the stubbed siteverify answers what Cloudflare DOCUMENTS each of its dummy
 * keys answering, so the suite is written against the published contract. No Cloudflare
 * account, Turnstile widget or gateway key is needed, and none may ever be. Sections live
 * in `sim/tests/edge/turnstile/` and run in order against one harness.
 *
 * What it proves:
 *   1. The three mandatory checks (`success`, `action`, `hostname`) each refuse on their
 *      own, and each resists LOOSENING (prefix/case-fold on action, suffix/empty on host).
 *   2. The designed fail-open/fail-closed split: a verdict of "no" refuses, a transport
 *      failure does not — except a WRONG SECRET, which arrives as HTTP 400 and must refuse.
 *   3. The concurrency slot comes back on the refusal path (a leaked slot fails CLOSED).
 *   4. A refusal costs nothing: zero gateway calls on a Turnstile refusal, zero siteverify
 *      calls on every cheaper refusal, and the shared unit budget refunded.
 *   5. `/api/chat` and `/api/transcribe` require different actions; tokens do not cross.
 *   6. The browser can get a token: `/api/health` publishes the sitekey, a failed script
 *      load is not memoised, and an on-screen challenge is never reset under the visitor.
 *   7. Nothing leaks: the secret and every Cloudflare `error-codes` string are absent from
 *      every body and header on every path.
 *   8. `sim/tools/turnstile_mutation_check.py` loosens each guard and requires THE CHECK
 *      THAT NAMES IT to redden, so keep the assertion labels stable.
 *
 *   node sim/test_turnstile.mjs
 */
import { runSections } from "./tests/edge/common.mjs";
import { fails, C } from "./tests/edge/turnstile/harness.mjs";

await runSections(new URL("./tests/edge/turnstile/", import.meta.url), [
  "01_config_and_checks.mjs",
  "02_fail_open.mjs",
  "03_slot_order_leaks.mjs",
  "04_browser.mjs",
  "05_contracts_ears_refund.mjs",
]);

if (fails.length) {
  console.error(`\n❌ Turnstile bot control: ${fails.length} FAILED of ${C.asserts} checks\n`);
  for (const f of fails) console.error("  FAIL: " + f);
  process.exit(1);
}
console.log(`✅ Turnstile bot control: ${C.asserts} checks passed (${C.sweeps} leak sweeps)`);
/* An explicit exit: §9 deliberately leaves `sim/web/turnstile.js`'s own 8 s deadline armed
 * (the assertion is that the promise has NOT resolved), and node would otherwise sit on
 * those timers after every assertion has finished — once per mutation-table row. */
process.exit(0);
