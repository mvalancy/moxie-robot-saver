# 🎛️ Supervisor

The robot-cloud runtime. Speaks the robot's MQTT protocol directly and turns it into clean
[`MoxieApp`](../moxie_sdk/app.py) calls, so SDK apps never deal with the raw wire format.

- [`moxie_runtime.py`](moxie_runtime.py) — the supervisor: MQTT session handling, message routing, and the
  robot conversation loop, based on the reverse-engineered protocol.
- [`markup.py`](markup.py) — Moxie's speech/behavior markup helpers (the tags that drive face + motion).

---
📖 [Back to top](../../README.md)
