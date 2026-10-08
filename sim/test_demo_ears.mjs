/* test_demo_ears.mjs — the ears, both halves, under bare node (live-sim-demo.md §3.2, §4.1–4.5,
 * §6, §10 assumptions 15–16).
 *
 * Part A calls the real `functions/api/transcribe.js` with a stubbed `fetch`; Part B runs the
 * real `sim/web/mic.js` on a virtual clock with a FAKE recorder. Proven: the key and gateway
 * URL never appear in a response, a refusal makes zero upstream calls, the page never goes
 * dead, and a tap with nothing said sends nothing. Sections live in `sim/tests/edge/ears/`.
 *
 *   node sim/test_demo_ears.mjs
 */
import { runSections } from "./tests/edge/common.mjs";
import { fails, C } from "./tests/edge/ears/harness.mjs";

await runSections(new URL("./tests/edge/ears/", import.meta.url), [
  "01_route_gates.mjs",
  "02_route_upstream.mjs",
  "03_route_duration.mjs",
  "04_mic_capture.mjs",
  "05_mic_degraded.mjs",
  "06_no_speech.mjs",
]);

if (fails.length) {
  console.error(`✗ test_demo_ears: ${fails.length} failure(s) of ${C.asserts}`);
  for (const f of fails) console.error("  - " + f);
  process.exit(1);
}
console.log(`✓ test_demo_ears: the ears hold their contract (${C.asserts} assertions, ${C.sweeps} secret sweeps, 0 leaks)`);
