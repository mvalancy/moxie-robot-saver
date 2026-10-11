# 🧾 `sim/tests/fixtures/` — data the node suites read

Recorded inputs a suite replays, kept apart from the sections that read them so a corpus can
grow without a test file changing. Each subfolder says where its data came from and which
section pins it.

- [`safety-floor/`](safety-floor/README.md) — the false-positive corpus of the safety floor:
  every real Moxie reply on disk when the output floor shipped, the child lines the input floor
  must and must not block, and the hurt replays the referral floor was measured on. Read by
  [`edge/demo_proxy/13_safety_floor.mjs`](../edge/demo_proxy/13_safety_floor.mjs).

A fixture is data, never code: no `.mjs`, no `.py`, and nothing here is collected by pytest.

---
📖 [Tests](../README.md) · [Back to top](../../../README.md)
