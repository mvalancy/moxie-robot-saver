# 🧠 Moxie SDK

The developer-facing SDK: drive Moxie as an avatar from any app or AI, without touching the raw MQTT wire
protocol. The [supervisor](../supervisor/) translates the robot's MQTT traffic into these clean calls.

- [`app.py`](app.py) — the `MoxieApp` interface, the heart of the SDK (what an app implements to control Moxie).
- [`types.py`](types.py) — shared data types passed to/from apps.
- [`actions.py`](actions.py) — parses robot-control tags (`<exit>`, `<launch:MOD:CID>`, …) out of a brain's own text into real `Reply.actions`, and the prompt paragraph that teaches a model to use them.
- [`apps/`](apps/) — ready-made `MoxieApp` implementations (echo, LLM brain, webhook).

---
📖 [Back to top](../../README.md)
