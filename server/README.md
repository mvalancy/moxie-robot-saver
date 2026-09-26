# `server/` — server app: parent-app half (Phase 1)

> One of the **two halves of the server app** (③ in [`../STRUCTURE.md`](../STRUCTURE.md)): this folder
> is the **parent-app-facing** backend (what the phone hits). The **robot-facing** half — MQTT broker,
> supervisor, Moxie SDK — lives in [`../mqtt/`](../mqtt/).

A clean-room, account-free reimplementation of the Moxie parent-app backend
(`client-service-api.embodied.com`) **plus** the mobile web client, in one FastAPI process.

## Run
```bash
pip install -r requirements.txt
python run.py                 # HOST/PORT env vars override (default 0.0.0.0:8080)
```
Open `http://<ip>:8080` from a phone on the same LAN/Tailscale.

## Layout
| Path | Role |
|------|------|
| `moxie_server/main.py` | The FastAPI app: no-cache middleware, the routers, the static client at `/` |
| `moxie_server/routes/` | One router per surface — [index](moxie_server/routes/README.md) |
| `moxie_server/supervisor.py` | Server-side calls to the MQTT supervisor (every `/local/*` card proxy) |
| `moxie_server/fleet/` | Pure card views over supervisor payloads — [index](moxie_server/fleet/README.md) |
| `moxie_server/auth.py` | Bearer-token dependency + token minting |
| `moxie_server/crypto.py` | Deterministic seed/keys (Argon2id → Ed25519/X25519/secretbox) |
| `moxie_server/diceware.py` | Recovery-phrase generation (EFF short wordlist) |
| `moxie_server/db.py` | SQLite persistence (zero-knowledge: opaque blobs) |
| `moxie_server/serializers.py` | JSON:API shaping the app expects |
| `static/` | The mobile web client (vanilla JS, no build step) |

## Endpoints
- **Faithful REST API** — the implementation contract is [`../docs/architecture/rest-api-contract.md`](../docs/architecture/rest-api-contract.md)
  (what to build + the minimum-viable-server path), distilled from the study
  [`rest-api.md`](../docs/reverse-engineering/phone/rest-api.md): `login/start`, `login/finish`,
  `oauth/token`, `users/me`, `children`, `robots/{id}`, `pairing-info`, `secret-key-collection`, …
- **`/local/*` conveniences** (not in the original API): `quicklogin`, `pairing/prepare`,
  `pairing/qr.png`, and **`simulate-robot-scan`** — completes pairing with no physical robot, for testing
  (pass `device_id` and it also permits that robot on the supervisor, so pairing needs no second click).
- **`/local/*` fleet + access** (proxied to the MQTT supervisor): `fleet`, `broker/status`,
  `robots/{id}/config`, `fleet/config`, `robots/{id}/telemetry`, `robots/{id}/safety`, and the
  **device allowlist** — `permits`, `robots/{id}/permit`, `fleet/permits`
  ([guide](../docs/guides/permitting-a-robot.md)).

## Notes
- The DB (`moxie.db`) is gitignored. Delete it to reset all state.
- OAuth client credentials are not checked, so a *repointed* original APK also works — but the
  primary client is the bundled web app. There is no password: anyone on the LAN can log in as
  any email (by design for a single-household appliance; keep it off the open internet).
