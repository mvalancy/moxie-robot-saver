/* test_mode.mjs — the mode machine and the honest indicator, end to end, under bare node
 * (live-sim-demo.md §3.2, §4.2, §4.5, §5, §6.3, §7): `_lib/env.js`, `_lib/envelope.js`,
 * `health.js` called with a plain `context.env`, and `sim/web/mode.js` + `env.js` as source
 * under stubbed globals on injected time. Sections live in `sim/tests/edge/mode/`.
 *
 *   node sim/test_mode.mjs
 */
import { runSections } from "./tests/edge/common.mjs";
import { fails, C } from "./tests/edge/mode/harness.mjs";

await runSections(new URL("./tests/edge/mode/", import.meta.url), [
  "01_config_envelope.mjs",
  "02_health.mjs",
  "03_mode_machine.mjs",
  "04_indicator_lint.mjs",
  "05_grounding_budget.mjs",
  "06_outage_honesty.mjs",
  "07_ears_apart.mjs",
]);

if (fails.length) {
  console.log("❌ mode tests FAILED:");
  for (const f of fails) console.log("   -", f);
  process.exit(1);
}
console.log(`✅ mode tests OK (${C.asserts} assertions)`);
