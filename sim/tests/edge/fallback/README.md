# 🔇 test_fallback_coverage sections

The sections of [`sim/test_fallback_coverage.mjs`](../../../test_fallback_coverage.mjs), in run
order, over one [`harness.mjs`](harness.mjs) (the shipped manifest, `stub.js`, `ambient.js`,
`audio.js`, a `withGlobals` save/restore and a fake Web Audio stack). Later sections import the
lines earlier ones extracted.

- [`01_manifest_sessions.mjs`](01_manifest_sessions.mjs) — §1–2b: the manifest is whole; every session line, both speakers; the child has room to speak and to finish.
- [`02_inventory.mjs`](02_inventory.mjs) — §3–6: the fallback is wired; every utterable line has its clip; the ambient layer is server-free.
- [`03_ambient_probe.mjs`](03_ambient_probe.mjs) — §7–8: the degraded line through the real `ambient.js`; when the Piper probe may fire.
- [`04_child_voice.mjs`](04_child_voice.mjs) — §8b–9: the child's voice is clip-or-nothing; end to end on the real assets; the renderer keeps every group.

---
📖 [Edge modules](../README.md) · [Back to top](../../../../README.md)
