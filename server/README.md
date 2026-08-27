# `server/` — parent-app server (Phase 1)

A clean-room, account-free reimplementation of the Moxie parent-app backend
(`client-service-api.embodied.com`) **plus** the mobile web client, in one FastAPI process.

## Run
```bash
pip install -r requirements.txt
python run.py                 # HOST/PORT env vars override (default 0.0.0.0:8080)
```
Open `http://<ip>:8080` from a phone on the same LAN/Tailscale.

## Layout
| File | Role |
|------|------|
| `moxie_server/main.py` | FastAPI app: the REST API, `/local/*` helpers, serves the web client |
| `moxie_server/crypto.py` | Deterministic seed/keys (Argon2id → Ed25519/X25519/secretbox) |
| `moxie_server/diceware.py` | Recovery-phrase generation (EFF short wordlist) |
| `moxie_server/db.py` | SQLite persistence (zero-knowledge: opaque blobs) |
| `moxie_server/serializers.py` | JSON:API shaping the app expects |
| `static/` | The mobile web client (vanilla JS, no build step) |

## Endpoints
- **Faithful REST API** (see [`../docs/reverse-engineering/rest-api.md`](../docs/reverse-engineering/rest-api.md)):
  `login/start`, `login/finish`, `oauth/token`, `users/me`, `children`, `robots/{id}`,
  `pairing-info`, `secret-key-collection`, …
- **`/local/*` conveniences** (not in the original API): `quicklogin`, `pairing/prepare`,
  `pairing/qr.png`, and **`simulate-robot-scan`** — completes pairing with no physical robot, for testing.

## Notes
- The DB (`moxie.db`) is gitignored. Delete it to reset all state.
- Production OAuth client credentials from the original app are accepted, so a *repointed* original
  APK also works — but the primary client is the bundled web app.
