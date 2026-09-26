/* test_fallback_coverage.mjs — the degraded page has a real voice for the lines it plays
 * (live-sim-demo.md §8.1 test 6, §6.1–6.3, §2.4).
 *
 * When the live brain is unreachable the page answers from `stub.js` and speaks from
 * `sim/web/audio/index.json`, keyed by the EXACT line text: re-punctuate a line and its clip
 * is silently orphaned. This builds one inventory of every line the degraded page can utter
 * and requires a clip for each, and drives the real `ambient.js`/`audio.js`/`bridge.js` for
 * the behaviour a grep cannot prove. Sections live in `sim/tests/edge/fallback/`.
 *
 *   node sim/test_fallback_coverage.mjs
 */
import { runSections } from "./tests/edge/common.mjs";
import { fails, C, notes } from "./tests/edge/fallback/harness.mjs";

await runSections(new URL("./tests/edge/fallback/", import.meta.url), [
  "01_manifest_sessions.mjs",
  "02_inventory.mjs",
  "03_ambient_probe.mjs",
  "04_child_voice.mjs",
]);

if (fails.length) {
  console.error(`✗ test_fallback_coverage: ${fails.length} failure(s)`);
  for (const f of fails) console.error("  - " + f);
  process.exit(1);
}
console.log(`✓ test_fallback_coverage: ${C.asserts} assertions`);
for (const n of notes) console.log("  " + n);
