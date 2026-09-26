# 🛣️ `routes/`

The FastAPI routers `main.py` includes, one per surface. Auth is the `current_user`
dependency from [`../auth.py`](../auth.py); every `/local/*` card proxy goes through
[`../supervisor.py`](../supervisor.py) and normalizes with a view from [`../fleet/`](../fleet/).

- [`account.py`](account.py) — auth (`login/*`, `oauth/token`), `users/me`, children, and the
  table of read/ack stubs the original app expects (notifications, help, analytics, …).
- [`robots.py`](robots.py) — `pairing-info`, `robots/{id}` CRUD and the honest device actions
  (`wakeup` publishes; `reboot` is a 501; `ota_status` reports only what the robot said).
- [`pairing.py`](pairing.py) — `/local/*` setup helpers: quick login, `pairing/prepare`, the QR
  PNGs, Moxie Direct, and `simulate-robot-scan` (completes a pairing once, with no hardware).
- [`console.py`](console.py) — the supervisor-backed console cards: fleet, config, permits,
  preview, telemetry, connection, safety, Be Moxie, today's plan, voice, brain, memory.
- [`content.py`](content.py) — 📦 content packs and ✍️ authoring (validation stays in the supervisor).

---
📖 [moxie_server](../README.md) · [Back to top](../../../README.md)
