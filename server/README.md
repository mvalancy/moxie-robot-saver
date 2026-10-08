# server — the parent-app backend

A clean-room, account-free reimplementation of the backend the Moxie phone app used
(`client-service-api.embodied.com`), plus a phone web app, in one FastAPI process. It also hosts the
parent console, which reads the robot side ([`../mqtt/`](../mqtt/README.md)) through the
supervisor's status API.

## Run it

```bash
pip install -r requirements.txt
python run.py                 # HOST and PORT override the default 0.0.0.0:8080
```

Open `http://<this-computer's-ip>:8080` on a phone on the same network (or over Tailscale). To run
it together with the robot side, use the repo-root `docker compose up`
([guide](../docs/guides/one-command-stack.md)). Tests: `sim/tests/test_parent_api.py`,
`test_fleet.py`, `test_robot_lifecycle.py`, `test_robot_claim.py`, `test_wifi_first_qr.py` and the
`test_console_*.py` files (`test_sil_robot_claim.py` runs the claim against the real supervisor); the browser suites `sim/test_console_insights.mjs`,
`sim/test_robot_lifecycle.mjs` and `sim/test_robot_claim.mjs`.

## Layout
| Path | Role |
|------|------|
| `moxie_server/main.py` | The FastAPI app: no-cache middleware, the routers, the static client at `/` |
| `moxie_server/routes/` | One router per surface — [index](moxie_server/routes/README.md) |
| `moxie_server/supervisor.py` | Server-side calls to the MQTT supervisor (every `/local/*` card proxy) |
| `moxie_server/fleet/` | Pure card views over supervisor payloads — [index](moxie_server/fleet/README.md) |
| `moxie_server/lifecycle.py` | Unpair and factory reset: the answer's wording and the `restore_factory` code (dependency-free) |
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
  `DELETE robots/{id}` unpairs and `?rfs=1` factory-resets: the robot leaves the account, its permit
  is revoked, unused pairing codes are voided and the child is kept
  ([what is built](../docs/features/robot-lifecycle.md#built-here-unpair-and-factory-reset)).
- **`/local/*` conveniences** (not in the original API): `quicklogin`, `wifi/payload` (the Wi-Fi
  tab's code: Wi-Fi only, no pairing key, the first code a re-homed robot scans), `pairing/prepare`
  (the original app's pairing-key code, behind an option), `pairing/qr.png`,
  `factory-reset/payload` and `factory-reset/qr.png` (the `restore_factory` setup
  code and its instructions), and **`simulate-robot-scan`** — completes pairing with no physical robot, for testing
  (pass `device_id` and it also permits that robot on the supervisor, so pairing needs no second click;
  a robot another account already has is a 409, in the claim's words).
- **Add to my account**: `POST /local/robots/{id}/claim` (bearer) puts a robot the supervisor lists
  on the parent's account and permits it, so a robot that paired by scanning the codes gets its
  robot card; `GET /local/state` lists the connected robots on no account as `unclaimed`
  (`unclaimed_known: false` when the supervisor could not be asked)
  ([bench runbook](../docs/guides/bench-runbook.md)).
- **`/local/*` fleet + access** (proxied to the MQTT supervisor): `fleet`, `broker/status`,
  `robots/{id}/config`, `fleet/config`, `robots/{id}/telemetry`, `robots/{id}/safety`, and the
  **device allowlist** — `permits`, `robots/{id}/permit`, `fleet/permits`
  ([guide](../docs/guides/permitting-a-robot.md)).

## Notes

- State lives in SQLite at `moxie.db` (git-ignored; `MOXIE_DB` moves it). Delete it to reset.
- There is no password: anyone on the network can log in as any email. That is intended for a
  single household; do not expose this server to the internet.
- OAuth client credentials are not checked, so a repointed original app would also work, but the
  bundled web app is the supported client.
