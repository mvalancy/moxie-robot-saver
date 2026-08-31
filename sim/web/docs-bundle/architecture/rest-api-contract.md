# 🛂 Parent-app server — REST services contract

> **Spec version 1 · derived from parent app `com.embo.embodied.parent` v2.2.2 (versionCode 249).**
> The *implementation-facing* contract for **Channel 1** — the REST API the phone (and the original
> Android APK) talks to. This is the "control plane": account, children, pairing, robot settings. It
> is independent of the robot-cloud ([AI seam](ai-seam.md) / [MQTT](mqtt-and-conversation.md)), which
> is Channel 2. Reads standalone; cites the study for provenance only. Source of facts:
> [`rest-api.md`](../reverse-engineering/phone/rest-api.md), [`crypto-and-keys.md`](../reverse-engineering/phone/crypto-and-keys.md),
> [`pairing-and-robot.md`](../reverse-engineering/phone/pairing-and-robot.md).

## What this server is

The phone app is a **pure REST client** — no MQTT, no websockets, no realtime SDK. Everything it does
is HTTPS calls to `client-service-api.embodied.com`. Our server reimplements that surface locally so
the phone (our web UI, or the original APK in compatibility mode) can create an account, add a child,
and **issue the pairing QR** that hands a robot its Wi-Fi + key. It meets Channel 2 at exactly two
points: the **pairing key** (its SHA-256 is recorded here; the robot later proves possession) and the
**account identity** (`iot-endpoint` selects which MQTT endpoint the robot dials).

## Transport & auth

- **Base URL:** every path below is relative to `<host>/api/`. One non-`/api/` path exists (the Privo
  consent webview) — irrelevant to a local server.
- **Headers:** `User-Agent: EmbodiedParentApp/v2.2.2 android/<rel>` on every request;
  `Authorization: <token_type> <access_token>` on authed calls.
- **Auth model:** OAuth-style bearer tokens. `login/start` (email → 6-digit code) → `login/finish`
  (code → `{access_token, refresh_token, token_type:"Bearer", expires_in, created_at}`) →
  `oauth/token` (grant_type=refresh_token) to refresh. Two hardcoded OAuth client credentials
  (`client_id`/`client_secret`) are sent on the login calls — a local server can accept any.
- **Account creation is implicit:** `login/start` with an unknown email **creates the account row**;
  there is no separate `POST users`. Sign-in and sign-up are the identical request.

## The zero-knowledge principle (must preserve)

All child PII is **end-to-end encrypted on the phone** before it reaches the server. The server stores
only opaque ciphertext (the `*-encrypted` child fields) and **escrowed copies of the symmetric key**
sealed to the account's public keys (`PUT secret-key-collection`). One 32-byte seed (derived from the
recovery phrase via Argon2id) is the key; the server never sees plaintext or the seed. A local server
**keeps this exactly** — it is zero-knowledge, the owner holds the keys. Details:
[`crypto-and-keys.md`](../reverse-engineering/phone/crypto-and-keys.md).

## Minimum viable server (the pairing-critical path)

The floor — implement these and a robot can pair. Everything else is enhancement.

| # | Call | Why it's required |
|---|---|---|
| 1 | `POST login/start` → `{redirect_uri}` (200) | begin auth; accept any email |
| 2 | `POST login/finish` → non-null `access_token`+`refresh_token`, `token_type:"Bearer"` | issue a session |
| 3 | `GET users/me?include=…` → JSON:API doc with non-empty `first-name`/`last-name`, `iot-endpoint`, `relationships.child` populated | bootstrap; **empty name forces sign-up; missing child aborts pairing** |
| 4 | `PUT users/me` (profile + `public-key`) · `POST children` | complete profile; create the child id pairing needs |
| 5 | `PUT secret-key-collection` | recovery-key escrow (entry is gated on `checkRecoveryKey()`) |
| 6 | `POST pairing-info?id=<sha256hex>&restore=&user-id=&child-id=` | record the pairing-key hash |
| 7 | `GET robots/{id}?include=restore,robot-setting` | app polls this to detect the paired robot |
| 8 | `POST oauth/token` (refresh) · `POST mobile-devices` (non-fatal) | keep session alive; push reg |

A local single-user server can shortcut steps 1–2 (no real email code needed — return a fixed code /
auto-accept) and skip Privo/COPPA gating.

## Full endpoint surface (distilled)

Grouped; "auth" = bearer required. Full request/response shapes in
[`rest-api.md §3`](../reverse-engineering/phone/rest-api.md#3-full-endpoint-inventory).

**Auth / session:** `POST login/start`, `POST login/finish`, `POST login/register` (Pro/clinician),
`POST oauth/token` (refresh).

**User:** `GET users/me?include=…`, `PUT users/me`, `DELETE users/me`, `POST users/me/change-email-request`,
`POST users/me/change-email`, `GET user-options`, `PUT secret-key-collection` (E2E key escrow).

**Children:** `POST children`, `PUT children/{id}`, `DELETE children/{id}`, `GET children/{id}/pending-info`
(COPPA/Privo), `POST children/{id}/resend-email`, `GET children/{id}/rewards`,
`GET|POST children/{id}/sensitive-conversations/{list,schedule,unschedule}`, `GET child-family-members`,
`GET content-preferences`. Child records carry many `*-encrypted` fields (client-side E2E blobs).

**Robot / pairing:** `POST pairing-info` (query params; `id`=SHA-256 of the pairing signing key),
`GET robots/{id}?include=restore,robot-setting`, `PUT robots/{id}` (robot **or** robot-setting overloads),
`DELETE robots/{id}` (unpair), `DELETE robots/{id}?rfs=1` (unpair + factory reset),
`POST robots/{id}/restores`, `GET robots/{id}/ota_status`, `POST robots/{id}/{reboot,wakeup}`,
`POST robots/{id}/set-language`, `POST grl/code`, `POST grl/revoke-all` (Guest/Remote Login).

**Mobile devices:** `POST mobile-devices`, `PUT mobile-devices/{id}` (FCM/APNS push tokens).

**Analytics / insights:** `GET analytics/pages/{id}`, `.../details`, `.../insights` (windowed, per-child).

**Notifications / content / help:** `GET notifications[/{id}]`, `POST notifications/{id}/{archive|unarchive}`,
`GET calendar-holidays`, `GET help[/{path}]`, `POST help/pronounce` (streaming audio),
`POST help/share-auid`, `GET|POST network-tests` (speed test hits absolute URLs from the response).

**Teletherapy:** `PUT teletherapy/patient-status`, `POST teletherapy/therapists-list`,
`POST teletherapy/request-access-moxie`.

## The `users/me` document shape (JSON:API)

```
{ "data": { "id", "type", "attributes": UserAttributes, "relationships": {…} },
  "included": [ { "id", "type", "attributes", "relationships" } … ] }
```
`relationships` keys: `child`, `children`, `robots`, `mobile-devices`, `identity-verification`.
`included[].type` is dispatched on exactly: `children`, `mobile-devices`, `robots`, `robot-setting`,
`restores`, `identity-verification`. Key `UserAttributes` (kebab-case): `active-child-id`, `email`,
`first-name`, `last-name`, `public-key`, `iot-endpoint`, `timezone-id`, `user-type`, `has-backups`,
`max-children`, `coppa-consent-status`, `grl-code-status`, plus notification/share flags. Full list:
[`rest-api.md §3.9`](../reverse-engineering/phone/rest-api.md#39-usersme-response-shape-jsonapi-style).

> **`iot-endpoint`** (integer) is the bridge to Channel 2 — it is embedded as the last byte of the
> pairing QR and selects the robot's MQTT/IoT endpoint. Our server sets this to point the robot at our
> broker. See [`mqtt-and-conversation.md`](mqtt-and-conversation.md).

## What a clean-room server can simplify

The original served millions of accounts across five regions with COPPA/Privo consent, teletherapy,
and marketing opt-ins. A **local, single-family** server can legitimately drop or stub:

- **Email-code login** → auto-accept a fixed code (or skip the code entirely) for the local owner.
- **Privo/COPPA** (`pending-info`, `resend-email`, `coppa-consent-status`) → stub "granted".
- **Teletherapy, analytics/insights, notifications, marketing flags** → optional; not on the pairing path.
- **Multi-region base URLs, Pro/clinician** (`login/register`) → single host, single user-type.

It **must not** drop: the token/refresh flow (the app hard-requires non-null tokens), the JSON:API
`users/me` shape (empty name → forced sign-up; missing `child` relationship → pairing NPE), the
`secret-key-collection` escrow, and `pairing-info` + `robots/{id}` polling.

## Conformance checklist

- [ ] Issues a working token pair from `login/start`+`login/finish`; refreshes via `oauth/token`.
- [ ] Returns a `users/me` JSON:API doc with non-empty name, an `iot-endpoint`, and a populated `child` relationship.
- [ ] Accepts `PUT secret-key-collection` and stores the escrowed keys verbatim (zero-knowledge).
- [ ] Records `POST pairing-info` (key hash) and serves `GET robots/{id}` so the app detects the paired robot.
- [ ] Supports unpair (`DELETE robots/{id}`) and factory-reset unpair (`?rfs=1`).

Where it lives in this repo: [`../server/`](../../server/) (the parent-app REST half). The robot-cloud
half is [`../mqtt/`](../../mqtt/); the two meet at `iot-endpoint` + the pairing key.

---
📖 [Docs index](../README.md) · [Architecture: overview](overview.md) · [MQTT & conversation](mqtt-and-conversation.md) · [AI seam](ai-seam.md)
