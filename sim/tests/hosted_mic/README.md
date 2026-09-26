# 🎙️ Hosted-mic check modules

The two halves of [`sim/check_hosted_mic.mjs`](../../check_hosted_mic.mjs), which plays a WAV
into Chrome's fake microphone on a deployed (or hermetic `--selftest`) SIM page and scores what
the site heard, answered and said out loud. Not run on their own — the entry file imports them.

- [`score.mjs`](score.mjs) — pure maths, identical on every machine: the transcript overlap (a
  port of `sim/tests/helpers_audio.py::word_overlap`), the chunked log-RMS **identity vote**
  (played clip vs. an unrelated one), `scorerProof` over committed fixtures (the push gate),
  the degradation gauntlet (saturation, dropped `ScriptProcessor` blocks, quiet input — each
  also checked with the templates swapped), and WAV read/write.
- [`probe.mjs`](probe.mjs) — the browser half: fixtures decoded by Chrome from the shipped
  manifest, `probeTurn` (one recorded turn under a spend-ceiling interceptor), `assertHeard`
  (clauses 1-6), and the per-run report.

Why the identity vote asserts an ORDERING rather than a magnitude: a CI runner's microphone
saturates (peak 1.0000) and halves every score while leaving "the played clip wins" intact.
A browser capture's identity/fidelity is asserted only by `--dry-run` and the paid run; the
fast tier's `--selftest` gates on `scorerProof` and the gauntlet instead.

Shared page plumbing (`PHONE`, `SPENDING`, `measureBoxes`, `instrumentWebAudio`,
`liveFixture`, …) lives in [`sim/browser_harness.mjs`](../../browser_harness.mjs).
