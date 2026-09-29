# 🧩 `moxie_runtime/` — the supervisor package

`MoxieRuntime` (in [`__init__.py`](__init__.py)) is one runtime serving every robot on the broker,
composed from one mixin per concern. Import it as `moxie_runtime.MoxieRuntime` or
`supervisor.moxie_runtime.MoxieRuntime`.

| Module | Concern |
|--------|---------|
| [`__init__.py`](__init__.py) | assembles `MoxieRuntime` from the mixins |
| [`constants.py`](constants.py) | tunables and privacy-policy defaults (`KEEPALIVE_S`, `MEMORY_POLICY`, …), re-exported by the package |
| [`connection.py`](connection.py) | paho client, (re)connect + SUBACK readiness, `_publish`, connect detection, durable roster |
| [`lifecycle.py`](lifecycle.py) | `run()`, `status_snapshot()`, clean shutdown on SIGTERM/SIGINT |
| [`status_http.py`](status_http.py) | the loopback-only HTTP status/control API the console proxies |
| [`fleet.py`](fleet.py) | fleet/per-robot config, device permit list, config push, wake, rehearsal preview |
| [`brain.py`](brain.py) | the per-robot brain picker (`app_for`), hot-swappable per child |
| [`turns.py`](turns.py) | turn routing, latency budget + fillers, streaming, `_publish_chat` |
| [`presence.py`](presence.py) | vision events → presence, greetings, event subscriptions |
| [`memory.py`](memory.py) | conversation transcript + long-term memory and their privacy gate |
| [`safety.py`](safety.py) | input safety gate + the parent review journal |
| [`content.py`](content.py) | content packs (export/import/undo) and authoring + template rendering |
| [`schedule.py`](schedule.py) | the day plan: schedule building and its console explanation |
| [`telemetry.py`](telemetry.py) | telemetry + mentor-behavior ingest, the durable activity record, and its erasure |
| [`telehealth.py`](telehealth.py) | the telehealth remote-puppet session path |
| [`voice.py`](voice.py) | server voice (TTS), the voice picker, and the STT extension point |

---
📖 [supervisor](../README.md) · [Back to top](../../../README.md)
