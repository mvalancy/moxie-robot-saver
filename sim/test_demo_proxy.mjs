/* test_demo_proxy.mjs — the two spending routes (`/api/chat`, `/api/speech`) under bare
 * node, with no Cloudflare account and no network.
 *
 * Spec: docs/architecture/backlog/live-sim-demo.md §8.1 test 1, plus §3.2 (both route
 * contracts), §4.1 (every cap), §4.2 (what the browser may know), §4.3 (the origin pin),
 * §4.5 (the status table), §2.2 (the wire field set).
 *
 * The handlers are imported and called with a synthetic `Request` and a plain object as
 * `context.env`; `fetch` is stubbed. The sections live in `sim/tests/edge/demo_proxy/`
 * and run in order against one shared harness (`harness.mjs` there). Above all the caps,
 * the suite proves two things:
 *
 *   1. THE KEY AND THE GATEWAY URL NEVER APPEAR IN A RESPONSE. `assertClean()` sweeps the
 *      body, every header and any decoded audio of EVERY response produced, including
 *      hostile upstream bodies that name a model and a key prefix.
 *   2. A REFUSAL MAKES ZERO UPSTREAM CALLS. `_lib/limits.js::noteUpstreamCall()` sits
 *      immediately before the one `fetch()` in each route, so `upstreamCalls` is a
 *      recorded fact rather than an inference from a stub.
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
  "07_turn_features.mjs",
  "08_tts_cache.mjs",
  "09_reroll_shape.mjs",
]);

if (fails.length) {
  console.error(`✗ test_demo_proxy: ${fails.length} failure(s)`);
  for (const f of fails) console.error("  - " + f);
  process.exit(1);
}
console.log(`✓ test_demo_proxy: the two spending routes hold their contract (${C.sweeps} secret sweeps, 0 leaks)`);
