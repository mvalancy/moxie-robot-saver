/* test_demo_proxy.mjs — the two spending routes (`/api/chat`, `/api/speech`) under bare node:
 * real handlers, a synthetic `Request`, a plain `env`, a stubbed `fetch`, no network.
 * Spec: docs/architecture/backlog/live-sim-demo.md §8.1 test 1 (+ §3.2, §4.1–4.6, §2.2).
 * Above every cap it proves (1) the key and gateway URL never appear in any response —
 * `assertClean()` sweeps body, headers and decoded audio of EVERY response — and (2) a
 * refusal makes zero upstream calls (`limits.js::noteUpstreamCall()` sits before each fetch).
 *
 *   node sim/test_demo_proxy.mjs
 */
import { runSections } from "./tests/edge/common.mjs";
import { fails, C } from "./tests/edge/demo_proxy/harness.mjs";

await runSections(new URL("./tests/edge/demo_proxy/", import.meta.url), [
  "01_contract.mjs",
  "02_safety_context.mjs",
  "03_speech.mjs",
  "04_deploy_only.mjs",
  "05_queue_and_keys.mjs",
  "06_cache_tier.mjs",
  "06b_unit_budget_tier.mjs",
  "07_turn_features.mjs",
  "08_tts_cache.mjs",
  "09_reroll_shape.mjs",
  "10_goodbye_close.mjs",
  "11_persona_v2.mjs",
  "12_spend_ops.mjs",
  "13_safety_floor.mjs",
]);

if (fails.length) {
  console.error(`✗ test_demo_proxy: ${fails.length} failure(s)`);
  for (const f of fails) console.error("  - " + f);
  process.exit(1);
}
console.log(`✓ test_demo_proxy: the two spending routes hold their contract (${C.asserts} assertions, ${C.sweeps} secret sweeps, 0 leaks)`);
