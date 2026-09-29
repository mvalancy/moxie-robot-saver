/* test_turnstile.mjs — the Cloudflare Turnstile bot control in front of the spending routes,
 * under bare node (live-sim-demo.md §4.1/§4.2/§4.5). The real handlers are called with a
 * synthetic Request; siteverify is stubbed with what Cloudflare documents its dummy keys
 * answering. Proves: the three mandatory checks each refuse alone and resist loosening; the
 * fail-open/fail-closed split; the slot and the shared budget come back on every refusal;
 * cheaper refusals never buy a siteverify call; chat and transcribe tokens do not cross; the
 * browser half mints a fresh token per send; nothing leaks. Assertion labels are selected by
 * `sim/tools/turnstile_mutation_check.py` — keep them stable.
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
/* §9 leaves turnstile.js's real 8 s deadline armed (it asserts a mint has NOT resolved). */
process.exit(0);
