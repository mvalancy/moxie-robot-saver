# 💸 test_demo_proxy sections

The sections of [`sim/test_demo_proxy.mjs`](../../../test_demo_proxy.mjs), in run order, over
one [`harness.mjs`](harness.mjs) (stubbed gateway, `fresh()`, `call()`, the `assertClean` sweep).

- [`01_contract.mjs`](01_contract.mjs) — §1–7: fail-safe default, origin pin, server-built body, input caps, wire fields, upstream failure, per-IP windows/budget/capacity.
- [`02_safety_context.mjs`](02_safety_context.mjs) — §8–9: the safety floor, invisible-character bypasses, the signed context blob.
- [`03_speech.mjs`](03_speech.mjs) — §10–11: `/api/speech`, one ticket per sentence (§10f: the 311-char reply yields tickets that join back to the whole reply, every one redeemable; the three-chunk cap; a word-bounded cut), the Tunnel/Access path, the closed envelope.
- [`04_deploy_only.mjs`](04_deploy_only.mjs) — §12: deploy-only failures converted into local ones, and the `/api/*` hardening headers on every response shape.
- [`05_queue_and_keys.mjs`](05_queue_and_keys.mjs) — §13–14: the admission queue, the rate-limit key, redirects.
- [`06_cache_tier.mjs`](06_cache_tier.mjs) — §15a–h: the Cache API tier's shared per-IP windows.
- [`06b_unit_budget_tier.mjs`](06b_unit_budget_tier.mjs) — §15i: the unit budget's shared hour.
- [`07_turn_features.mjs`](07_turn_features.mjs) — §15j–15n: expressive envelope, expired context, diagrams, doc lookup.
- [`08_tts_cache.mjs`](08_tts_cache.mjs) — §16: the synthesised-audio cache.
- [`09_reroll_shape.mjs`](09_reroll_shape.mjs) — §17–18: the re-roll and the per-turn shape cue.
- [`10_goodbye_close.mjs`](10_goodbye_close.mjs) — §19–22: the goodbye close (detector, cue, `end_turn`, the sign-off wave), the prompt layouts (`DEMO_PROMPT_LAYOUT`), the brace-proof envelope parser, the punctuation-folding echo.
- [`11_persona_v2.mjs`](11_persona_v2.mjs) — §23: the persona v2 (identity and mission first, the child as mentor, her idle habits, honest senses, ordered rules each stated once, rule 2 deferring to the safety block for a hurt child, the safety block last and verbatim against a frozen copy) and the system messages every layout emits around it.

---
📖 [Edge modules](../README.md) · [Back to top](../../../../README.md)
