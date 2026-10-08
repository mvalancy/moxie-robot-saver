# ⚙️ moxie_server

The FastAPI application: a clean-room reimplementation of the parent-app REST API
(`client-service-api.embodied.com`) plus local helpers — runs entirely on the LAN, no account, no cloud.

- [`main.py`](main.py) — builds the app: no-cache middleware, includes the routers, mounts the client.
- [`routes/`](routes/) — one router per surface (account, robots, pairing, console, content).
- [`supervisor.py`](supervisor.py) — the one place that calls the MQTT supervisor's status
  server (`MOXIE_SUPERVISOR_STATUS`); a down supervisor is a 503 in the card's shape, never a 500.
- [`fleet/`](fleet/) — pure, dependency-free card views over supervisor payloads.
- [`lifecycle.py`](lifecycle.py) — unpair and factory reset: the answer's wording and the
  `restore_factory` setup code, shared by the routes and the browser suite (dependency-free).
- [`auth.py`](auth.py) — the bearer-token dependency, token minting, JSON body reading.
- [`crypto.py`](crypto.py) — deterministic crypto (Argon2id zero-salt seed → Ed25519/X25519/secretbox);
  reproduces the account/pairing key system without any secrets on disk.
- [`db.py`](db.py) — persistence layer (accounts, children, devices, pairing state).
- [`serializers.py`](serializers.py) — JSON:API response shaping the parent app's DataManager expects.
- [`diceware.py`](diceware.py) — human-friendly passphrase generation (uses [`data/`](data/)).
- [`data/`](data/) — static reference data bundled with the server.

---
📖 [Back to top](../../README.md)
