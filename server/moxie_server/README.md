# ⚙️ moxie_server

The FastAPI application: a clean-room reimplementation of the parent-app REST API
(`client-service-api.embodied.com`) plus local helpers — runs entirely on the LAN, no account, no cloud.

- [`main.py`](main.py) — the FastAPI app and route wiring.
- [`crypto.py`](crypto.py) — deterministic crypto (Argon2id zero-salt seed → Ed25519/X25519/secretbox);
  reproduces the account/pairing key system without any secrets on disk.
- [`db.py`](db.py) — persistence layer (accounts, children, devices, pairing state).
- [`serializers.py`](serializers.py) — JSON:API response shaping the parent app's DataManager expects.
- [`extra_api.py`](extra_api.py) — `/local/*` helpers beyond the original API (e.g. pairing-QR, robot-scan simulation).
- [`diceware.py`](diceware.py) — human-friendly passphrase generation (uses [`data/`](data/)).
- [`data/`](data/) — static reference data bundled with the server.

---
📖 [Back to top](../../README.md)
