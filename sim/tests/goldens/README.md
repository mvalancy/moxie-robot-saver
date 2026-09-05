# 📁 `goldens`

Recorded expectations the suites compare against, rather than recompute. Two families.

**JSON goldens** — `annotate.json` (the markup floor), `performance.json` (the behavior
planner's dialog acts), `cloud_to_robot_actions.json` and `robot_to_cloud_activity.json`
(wire shapes in both directions). Each is read by a pytest file *and* by a `node
sim/test_*.mjs` renderer, which is the point: one recorded truth, checked by both clients,
so the browser bridge and the python robot can never quietly disagree about it.

**`real_voice_22050_mono.wav`** — 0.75 s of the SIM's own prerendered Moxie speech, mono
PCM16 @ 22050 Hz, 33 118 B. It is the one *real voice* in the hermetic suite:
`../test_speech_guard.py` uses it to prove the tone/speech guard recognises actual speech,
not just the synthetic broadband control built in that file.

Committed as a **plain WAV on purpose**, and it is worth knowing why before anyone
"optimises" it back to the 26 KB mp3 in `../../web/audio/moxie/`. The first version of that
test decoded the mp3 by shelling out to `ffmpeg`, in a change whose whole subject was
declaring every dependency exactly once — and CI (run 33985062379) failed with
`FileNotFoundError: [Errno 2] No such file or directory: 'ffmpeg'`, because a runner has no
ffmpeg. A `shutil.which` skip would have turned that loud red into a silent pass, and
`apt-get install ffmpeg` would have put a system package on every run of the tier to decode
one fixture. So the dependency was removed instead: `wave` is in the standard library, and
now the assertion runs on any box, with no decoder and no numpy. `test_speech_guard.py`'s
`DECLARED_BINARIES` guard makes the general mistake impossible to repeat quietly.

The clip is the shortest span that keeps a comfortable margin on **both** flatness
implementations — 3.073e-02 (30 727× the 1e-6 speech floor) on the stdlib path and
3.200e-03 (3 200×) on the numpy path. Trimming it further is allowed; re-stating the new
margins is not optional, and a guard fails if the weaker one drops under 100×.

---
📖 [Docs index](../../../docs/README.md) · [Back to top](../../../README.md)
