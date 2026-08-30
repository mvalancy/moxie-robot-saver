# 🔐 Network trust — TLS, CA validation, and what a server needs

> **What this is.** Exactly how the robot decides which servers to trust — the piece that determines
> whether you can point it at your own backend (goal #2) and why old robots are stuck (goal #3).
> Short version: **standard CA-store validation, no custom public-key pinning.** From the native
> `libbo-*` TLS stack (libcurl + BoringSSL, Paho MQTT) and the system CA store, cross-checked against
> OpenMoxie's working configuration.

## The stack

| Channel | Library | Auth | Server-cert trust |
|---|---|---|---|
| REST (`client-service`) | **libcurl + BoringSSL** | `Authorization: Bearer <token>` | system CA store |
| MQTT | **Eclipse Paho**, TLS on **:8883** | anonymous (801+) / per-robot on Google IoT | system CA store |
| STT | WebSocket (`org.java_websocket`) | `Authorization: bearer <token>` | system CA store |

## No public-key pinning

The libs contain libcurl's `CURLE_SSL_PINNEDPUBKEYNOTMATCH` **error string** (part of curl's built-in
table) but **no configured pin** — no `sha256//…` pin values, no `CURLOPT_PINNEDPUBLICKEY` setup.
Trust is ordinary **CA-chain validation + hostname check** against the on-device store:

- `/system/etc/security/cacerts` — **961** standard roots.
- `/system/etc/security/cacerts_google` — Google's roots (GeoTrust, DigiCert, Entrust, GlobalSign…).

**Consequence:** the robot will trust *any* server whose certificate chains to a public CA and matches
the hostname it connects to. There is no Embodied-specific pin to defeat.

## What this means for running your own server (goal #2)

You do **not** need Embodied's keys — you need a server the robot's CA store already trusts:

1. **Use a real domain with a valid TLS cert** (e.g. Let's Encrypt). The robot validates it against the
   961-root store like any browser. Self-signed / `.local` certs are **not** trusted unless you add a
   CA (needs `/system` write / root).
2. **Point the robot at it** by changing its endpoint (801+): an `endpoint_update` QR
   ([`qr-commands.md`](qr-commands.md)) to `OPEN_MOXIE`/a custom host, or DNS (`/etc/hosts` /
   local resolver mapping the endpoint hostname to your server).
3. **Run the broker + REST + STT** behind that domain ([`cloud-protocol.md`](cloud-protocol.md)).

### The proven recipe (OpenMoxie)

```conf
# /etc/mosquitto/conf.d/openmoxie.conf
listener 8883
cafile   /etc/letsencrypt/live/DOMAIN/chain.pem
keyfile  /etc/letsencrypt/live/DOMAIN/privkey.pem
certfile /etc/letsencrypt/live/DOMAIN/cert.pem
allow_anonymous true
```
The robot connects to `DOMAIN:8883`, validates the Let's Encrypt cert against its store, and (on 801+)
authenticates anonymously. That's the whole trust story — **a real domain + a real cert**, no pinning
bypass required. (`EMBODIED_LOCAL`'s `client-service-api.local` is the exception: `.local` can't get a
public cert, so it needs a CA you install on the device.)

## The `disable_verify` escape hatch (and its catch)

`embodied.logging.ServiceConfiguration` carries a **`disable_verify`** flag that maps to
`CURLOPT_SSL_VERIFYPEER=0` — i.e. the robot **can** be configured to skip TLS peer verification, which
would let a **self-signed** cert work and remove the "real domain + Let's Encrypt" requirement.

**The catch is delivery.** `ServiceConfiguration` (host/port/`disable_verify`) is applied natively via
the `SettingSchema` store, pushed over the **already-connected** bus/provisioning — *not* settable
from the setup QR. The setup QR (`StartPairingQR`) only carries an **`IOTEndpoint` enum** (a *fixed*
host like `EMBODIED_LOCAL`→`client-service-api.local`), not an arbitrary `mqtt_host` or
`disable_verify`. So bootstrapping is two-stage:

1. Get the robot to connect *once* to a host it already trusts (a real cert, via the endpoint QR /
   DNS), then
2. push a `ServiceConfiguration` over MQTT to move it to your real host and/or set `disable_verify`.

For a first-contact **self-signed** setup you'd still need to plant a `ServiceConfiguration` (or a CA)
on the device — which needs on-device access. So `disable_verify` helps *operationally* (a running
fleet can be moved to self-signed infra) but doesn't remove the first-connection trust requirement,
and doesn't rescue pre-801 (which never connects to you in the first place).

## Why pre-801 is still stuck (goal #3) — precisely

It is **not** cert pinning. Two things block a pre-801 unit, both about *reachability*, not crypto:

1. **The endpoint hostname is hardcoded** to Google IoT (`mqtt.googleapis.com`) and can't be changed
   by QR on pre-801 — so you can't tell it to talk to your domain.
2. Even if you DNS-redirect that hostname to your server, TLS validation then requires a certificate
   **valid for `mqtt.googleapis.com`** signed by a CA the robot trusts — which nobody but Google can
   obtain. So the connection fails at the hostname/cert check.

The robot's willingness to connect isn't the problem (it drops to QR-reading when offline —
[`boot-and-launcher.md`](../firmware/boot-and-launcher.md)); the problem is you can't present a trusted cert for
the *fixed* hostname it insists on. Breaking that needs either the 801+ firmware (changeable endpoint)
or on-device access to add a CA / edit the endpoint — i.e. the upgrade this whole effort is chasing.

## Time sync (clock skew breaks auth) — and it's fine

Correct time matters here: **TLS cert validity** (not-before/not-after) and the **RS256 JWT** `iat`/`exp`
([device auth](cloud-protocol.md#robot-authentication-device-identity)) both fail if the robot's clock
is wrong. Good news for revival — the time source is **public and still alive**:

- `me.embodied.NTPService` runs SNTP against **`time.android.com, pool.ntp.org, time.nist.gov`**
  (default), refreshes periodically, and sets the system clock (`SntpClient` → `setTime`). It falls
  through the list and logs `BO#8013 Unable to reach any NTP servers` on total failure.
- **Overridable** via the prop **`sys.embodied.ntp_servers`** — point it at a local NTP if you run a
  fully-offline network.
- Timezone comes from the cloud/parent (`requestSetTimezone`; `RemoteChatRequest.timezone_id`).

**Caveat for revival:** a robot that boots **offline** (or after a long-dead battery) has a wrong
clock until it reaches NTP — the *first* TLS/JWT handshake can fail on skew, then self-correct once
online. If you serve a fully-offline setup, either run a local NTP (set `sys.embodied.ntp_servers`) or
allow for the initial skew. Otherwise the public NTP path just works.

## Corrections to earlier docs

Earlier notes said pre-801 endpoints are "TLS **hostname-pinned**." More precisely: **hostname is
hardcoded and the cert is CA-validated** (not public-key pinned). The practical block is identical, but
the mechanism matters — it means 801+ redirection works with an ordinary public cert, no pin bypass.

---
📖 [Reverse-engineering index](../README.md) · [Cloud protocol](cloud-protocol.md) · [OTA & recovery](../firmware/ota-and-recovery.md) · [Docs index](../../README.md)
