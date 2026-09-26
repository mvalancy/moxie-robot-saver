# 🎛️ Supervisor

The robot-cloud runtime. Speaks the robot's MQTT protocol directly and turns it into clean
[`MoxieApp`](../moxie_sdk/app.py) calls, so SDK apps never deal with the raw wire format.

- [`moxie_runtime/`](moxie_runtime/) — the supervisor package. `MoxieRuntime` (in
  [`__init__.py`](moxie_runtime/__init__.py)) is composed from one mixin per concern; import it as
  `moxie_runtime.MoxieRuntime` or `supervisor.moxie_runtime.MoxieRuntime`.
- [`markup.py`](markup.py) — Moxie's speech/behavior markup helpers (the tags that drive face + motion).

| Module | Concern |
|--------|---------|
| `constants.py` | tunables and privacy-policy defaults (`KEEPALIVE_S`, `MEMORY_POLICY`, …), re-exported by the package |
| `connection.py` | paho client, (re)connect + SUBACK readiness, `_publish`, connect detection, durable roster |
| `lifecycle.py` | `run()`, `status_snapshot()`, clean shutdown on SIGTERM/SIGINT |
| `status_http.py` | the loopback-only HTTP status/control API the console proxies |
| `fleet.py` | fleet/per-robot config, device permit list, config push, wake, rehearsal preview |
| `brain.py` | the per-robot brain picker (`app_for`) |
| `turns.py` | turn routing, latency budget + fillers, streaming, `_publish_chat` |
| `presence.py` | vision events → presence, greetings, event subscriptions |
| `memory.py` | conversation transcript + long-term memory and their privacy gate |
| `safety.py` | input safety gate + the parent review journal |
| `telemetry.py` | telemetry / mentor-behavior ingest and erasure |
| `voice.py` | server TTS, the voice picker, the STT extension point |
| `content.py` | content packs + authoring |
| `schedule.py` | the day plan and its explanations |
| `telehealth.py` | "Be Moxie" puppet mode |

---
📖 [Back to top](../../README.md)
