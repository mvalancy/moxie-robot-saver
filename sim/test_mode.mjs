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
]);

if (fails.length) {
  console.log("❌ mode tests FAILED:");
  for (const f of fails) console.log("   -", f);
  process.exit(1);
}
console.log(`✅ mode tests OK (${C.asserts} assertions) — /api/health answers gateway_not_configured with no variables set `
  + "(one request, no poll storm, page byte-identical to today); the envelope is a fixed key allowlist "
  + "with no URL, key or model id in any body or header; boot→offline on an absent/malformed route; "
  + "live/degraded/busy/budget badges and copy per §7; 429 soft-degrades without leaving live; "
  + "the probe reads the REAL limits.js counters (busy at 3/4, budget_exhausted with its "
  + "Retry-After) and still makes zero upstream calls; "
  + "the paid grounding transport refuses redirects, counts retries, and times complete bodies; "
  + "3 strikes → degraded; 30 s→5 min backoff; never polls while hidden; env.js drives the badge, "
  + "pill, banner and needs-backend marks from the MODE, not the hostname");
