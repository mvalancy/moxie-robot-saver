# 🧾 `fleet/`

The console's pure card views: a supervisor payload in, the render-ready shape a card reads
out. No fastapi, no network, nothing from `mqtt/` — so every view unit-tests in the hermetic
tier — and none of them raises on a missing, partial or mistyped payload.

- [`__init__.py`](__init__.py) — re-exports every view, so `from moxie_server.fleet import …` works.
- [`_coerce.py`](_coerce.py) — the shared coercion helpers and `card_view`, the "a card is
  never a 500" wrapper.
- [`robots.py`](robots.py) — the fleet view, the face catalog, and which action buttons are
  real (`UNSUPPORTED_ACTIONS`, `ota_status_view`, `resolve_device_id`).
- [`activity.py`](activity.py) — 📈 telemetry, 🔌 the broker connection, 🛡️ the safety queue.
- [`memory.py`](memory.py) — 🧠 what Moxie remembers, as dated per-activity rows.
- [`cards.py`](cards.py) — 🎭 Be Moxie, 📅 today's plan, 🎚️ voice, 🧠 brain.
- [`content.py`](content.py) — 📦 content inventory, review table and results.

---
📖 [moxie_server](../README.md) · [Back to top](../../../README.md)
