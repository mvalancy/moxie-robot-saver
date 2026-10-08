# 🔌 test_cloud_transport sections

The sections of [`sim/test_cloud_transport.mjs`](../../../test_cloud_transport.mjs), in run order,
over one [`harness.mjs`](harness.mjs): the REAL `stub.js`, `bridge/`, `mode.js` and
`cloud-transport.js` loaded as source under a fake DOM/audio/mqtt/fetch on a virtual clock, plus
answer builders (`live`, `serve`, `said`, `ticket`, `voiced`, and `tickets`/`voicedChunk`/`chunked` for a reply with several voice chunks). By default `window.moxieAudio` is a
spy whose `isSpeaking()` is as narrow as the real one; `boot({ realVoice: true })` loads the REAL
`voice/` instead, over a fake Web Audio stack and speechSynthesis that record every sound that
starts and every one cut short (`autoplay: "policy"` adds the browser's activation rule).

- [`01_wrapper_turn.mjs`](01_wrapper_turn.mjs) — §1–2: a wrapper, not a replacement; one whole live turn.
- [`02_voice_order.mjs`](02_voice_order.mjs) — §3–4: slow speech starts no local voice and plays once; the double-voice hazard proven real, and the per-event expectation that removes it. §4b–4h: one ticket per sentence — chunks redeemed one at a time and routed in order behind chunk 0, a later chunk's failure ends the voice with no local stand-in, on the spy and on the real `voice/`. §4i–4k, on the real `voice/`: overlapping turns — a newer reply's voice starting (its chunk 0 routed, or a stub line) ends the older reply's pipeline, so nothing more of it is paid for and no sentence of it is heard after the newer reply.
- [`03_degraded.mjs`](03_degraded.mjs) — §5: every degraded path answers, and is spoken, even after a voiced turn.
- [`04_talk_scripted.mjs`](04_talk_scripted.mjs) — §6–6b: the Talk box; the consolation line is free.
- [`05_bot_control.mjs`](05_bot_control.mjs) — §7: one fresh Turnstile token per send, never a dead Send.
- [`06_voice_latch.mjs`](06_voice_latch.mjs) — §8–10, on the real `voice/`: one voice per reply (no line silent after a voiced one, no stand-in voice, a failed voice spoken locally once, a voice failure never degrades the page, the bus and replays keep their rule); the thinking filler; the audio unlock.
- [`07_turns_in_order.mjs`](07_turns_in_order.mjs) — §11, on the real `voice/`: one line at a time from the controls — two lines 300 ms apart through the Ask path are sent in turn, the second carrying the first reply's context, both heard whole in order, the third turn holding both exchanges; the mic's transcript takes the same queue; a waiting line is re-decided when its turn comes (a 429 before it: stub, never echoed twice); `sendUserTurn` still sends at once (§4i–4k's backstop); a turn whose pipeline never closes is settled by `TURN_MAX_MS` (§11e).

`sim/tools/turnstile_mutation_check.py` rows with runner `UX` match labels in `05_bot_control.mjs`.
The same one-voice rules are measured in real Chrome (phone, touch, its own autoplay policy) by
[`sim/test_one_voice.mjs`](../../../test_one_voice.mjs).

---
📖 [Edge modules](../README.md) · [Back to top](../../../../README.md)
