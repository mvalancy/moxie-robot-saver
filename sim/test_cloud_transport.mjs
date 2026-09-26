/* test_cloud_transport.mjs — the live turn in the browser, on a virtual clock (live-sim-demo.md
 * §8.1 test 5, §3.4, §3.5, §6.3).
 *
 * The hazard: with no MQTT broker `bridge/speakLocally` speaks IMMEDIATELY, so a transport
 * that routed the chat message before the TTS message would play two voices at once. §4 drives
 * that naive order through the real bridge and proves the double voice happens; §2–3 prove the
 * shipped voice-first order makes it impossible. Sections live in `sim/tests/edge/transport/`.
 *
 *   node sim/test_cloud_transport.mjs
 */
import { runSections } from "./tests/edge/common.mjs";
import { fails, C } from "./tests/edge/transport/harness.mjs";

await runSections(new URL("./tests/edge/transport/", import.meta.url), [
  "01_wrapper_turn.mjs",
  "02_voice_order.mjs",
  "03_degraded.mjs",
  "04_talk_scripted.mjs",
  "05_bot_control.mjs",
]);

if (fails.length) {
  console.error(`✗ test_cloud_transport: ${fails.length} failure(s)`);
  for (const f of fails) console.error("  - " + f);
  process.exit(1);
}
console.log(`✓ test_cloud_transport: one voice, always — and every degraded path still answers (${C.asserts} assertions)`);
