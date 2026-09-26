/* helpers_shared_ceilings.mjs — the per-IP HOUR/DAY windows and the unit budget's DAY on
 * the shared Cache API tier of `functions/api/_lib/limits.js` (live-sim-demo.md §4.6.1–3).
 * Run by `sim/tests/test_shared_ceilings.py`, which asserts each lettered section's count;
 * `sim/tools/unit_budget_mutation_check.py` matches its failing labels. Sections A–K live in
 * `sim/tests/edge/ceilings/`. No wall clock: every admission gets an explicit `nowS`.
 *
 *   node sim/tests/helpers_shared_ceilings.mjs [--json]
 */
import { runSections } from "./edge/common.mjs";
import { fails, sections, S } from "./edge/ceilings/harness.mjs";

await runSections(new URL("./edge/ceilings/", import.meta.url), [
  "01_bind.mjs",
  "02_fail_open.mjs",
  "03_keys_cost.mjs",
]);

if (process.argv.includes("--json")) {
  console.log(JSON.stringify({ checks: S.checks, failures: fails, sections }));
} else if (fails.length) {
  console.log(`✗ shared_ceilings: ${fails.length} failure(s)`);
  for (const f of fails) console.log("  - " + f);
} else {
  console.log(`✓ shared_ceilings: ${S.checks} checks, the hour/day windows and the day budget are shared`);
}
process.exit(fails.length ? 1 : 0);
