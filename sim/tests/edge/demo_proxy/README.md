# 💸 test_demo_proxy sections

The sections of [`sim/test_demo_proxy.mjs`](../../../test_demo_proxy.mjs), in run order, over
one [`harness.mjs`](harness.mjs) (stubbed gateway, `fresh()`, `call()`, the `assertClean` sweep).

- [`01_contract.mjs`](01_contract.mjs) — §1–7: fail-safe default, origin pin, server-built body, input caps, wire fields, upstream failure, per-IP windows/budget/capacity.
- [`02_safety_context.mjs`](02_safety_context.mjs) — §8–9: the safety floor, invisible-character bypasses, the signed context blob.
- [`03_speech.mjs`](03_speech.mjs) — §10–11: `/api/speech`, the Tunnel/Access path, the closed envelope.
- [`04_deploy_only.mjs`](04_deploy_only.mjs) — §12: deploy-only failures converted into local ones.
- [`05_queue_and_keys.mjs`](05_queue_and_keys.mjs) — §13–14: the admission queue, the rate-limit key, redirects.
- [`06_cache_tier.mjs`](06_cache_tier.mjs) — §15a–h: the Cache API tier's shared per-IP windows.
- [`06b_unit_budget_tier.mjs`](06b_unit_budget_tier.mjs) — §15i: the unit budget's shared hour.
- [`07_turn_features.mjs`](07_turn_features.mjs) — §15j–15n: expressive envelope, expired context, diagrams, doc lookup.
- [`08_tts_cache.mjs`](08_tts_cache.mjs) — §16: the synthesised-audio cache.
- [`09_reroll_shape.mjs`](09_reroll_shape.mjs) — §17–18: the re-roll and the per-turn shape cue.

---
📖 [Edge modules](../README.md) · [Back to top](../../../../README.md)
