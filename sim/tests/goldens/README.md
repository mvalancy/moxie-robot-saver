# 📁 `sim/tests/goldens/` — recorded expectations

Recorded truths the suites compare against rather than recompute.

- [`annotate.json`](annotate.json) — the markup floor's expected output.
- [`performance.json`](performance.json) — the behavior planner's dialog acts.
- [`cloud_to_robot_actions.json`](cloud_to_robot_actions.json) — action wire shapes, cloud to robot.
- [`robot_to_cloud_activity.json`](robot_to_cloud_activity.json) — activity wire shapes, robot to cloud.
- [`real_voice_22050_mono.wav`](real_voice_22050_mono.wav) — 0.75 s of the SIM's prerendered
  Moxie speech, mono PCM16 @ 22050 Hz, 33 118 B; the one real voice in the hermetic suite, used by
  [`../test_speech_guard.py`](../test_speech_guard.py).

Each JSON golden is read by a pytest file and by a `node sim/test_*.mjs` renderer, so the Python
robot and the browser bridge cannot drift apart.

The WAV is plain on purpose: the mp3 original needed `ffmpeg`, which CI runners lack; `wave` is
stdlib. Its flatness margins are 3.073e-02 (stdlib path) and 3.200e-03 (numpy path) against the
1e-6 speech floor; trimming it is allowed, but `test_speech_guard.py` fails if the weaker margin
drops under 100x.

---
📖 [Tests](../README.md) · [Back to top](../../../README.md)
