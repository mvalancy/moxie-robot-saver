# 🛣️ `routes/`

The FastAPI routers `main.py` includes, one per surface. Auth is the `current_user`
dependency from [`../auth.py`](../auth.py); every `/local/*` card proxy goes through
[`../supervisor.py`](../supervisor.py) and normalizes with a view from [`../fleet/`](../fleet/).

- [`account.py`](account.py) — auth (`login/*`, `oauth/token`), `users/me`, children, and the
  table of read/ack stubs the original app expects (notifications, help, analytics, …).
- [`robots.py`](robots.py) — `pairing-info`, `robots/{id}` CRUD and the honest device actions
  (`wakeup` publishes; `reboot` is a 501; `ota_status` reports only what the robot said). Unpair
  and factory reset are `DELETE robots/{id}[?rfs=1]`: off the account, permit revoked, unused
  pairing codes voided, the child kept; a reset also returns the `restore_factory` code.
- [`pairing.py`](pairing.py) — `/local/*` setup helpers: quick login, `wifi/payload` (the Wi-Fi
  tab's Wi-Fi-only code), `pairing/prepare` (the pairing-key code), the QR PNGs (including the
  `factory-reset` code), Moxie Direct, `simulate-robot-scan` (completes a pairing once, with no
  hardware; a code voided by an unpair is a 410), and `robots/{id}/claim` (Add to my account:
  a robot the supervisor lists joins the parent's account; one robot per account, fails closed).
- [`console.py`](console.py) — the supervisor-backed console cards: fleet, config, permits,
  preview, telemetry, connection, safety, Be Moxie, today's plan, voice, brain, memory.
- [`content.py`](content.py) — 📦 content packs and ✍️ authoring (validation stays in the supervisor).

---
📖 [moxie_server](../README.md) · [Back to top](../../../README.md)
