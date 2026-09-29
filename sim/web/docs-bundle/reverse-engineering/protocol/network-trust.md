# 🔐 Network trust — TLS, CA validation, and what a server needs (`v3.6.4-Zephyr` / OTA `v24.10.803`)

How the robot decides which servers to trust. Short version: **standard CA-store validation plus hostname
check, no public-key pinning.** On 801+ firmware a server needs either a publicly trusted certificate for a
host the robot is pointed at, or an `om` re-home with `disable_verify` set. Pre-801 units are stuck on
*reachability* (a hard-coded Google hostname), not on crypto. From the native `libbo-*` TLS stack (libcurl +
BoringSSL, Paho MQTT) and the system CA store, cross-checked against OpenMoxie's working configuration.

| Channel | Library | Auth | Server-cert trust |
|---|---|---|---|
| REST (`client-service`) | **libcurl + BoringSSL** | `Authorization: Bearer <token>` | system CA store |
| MQTT | **Eclipse Paho**, TLS on **:8883** | anonymous (801+) / per-robot JWT on Google IoT ([device auth](cloud-protocol.md#robot-authentication-device-identity)) | system CA store |
| STT | WebSocket (`org.java_websocket`) | `Authorization: bearer <token>` | system CA store |

## No public-key pinning

The libs contain libcurl's `CURLE_SSL_PINNEDPUBKEYNOTMATCH` **error string** (curl's built-in table) but
**no configured pin** — no `sha256//…` values, no `CURLOPT_PINNEDPUBLICKEY` setup. Trust is CA-chain
validation + hostname check against:

- `/system/etc/security/cacerts` — **961** standard roots.
- `/system/etc/security/cacerts_google` — Google's roots (GeoTrust, DigiCert, Entrust, GlobalSign…).

The robot trusts *any* server whose certificate chains to a public CA and matches the hostname it connects to.

## Running your own server (801+)

1. **Point the robot at your host** — the pairing QR's `endpoint` field or `endpoint_update` selects a
   *fixed* `IOTEndpoint` profile; the **`om` debug QR** carries a full `ServiceConfiguration`
   (`mqtt_host`, `override_port`, `disable_verify`) for an arbitrary host ([qr-commands](qr-commands.md#the-om-relocation-the-real-re-home-payload)).
   DNS (a local resolver mapping the endpoint hostname) also works.
2. **Present a cert the robot accepts** — a real domain with a public cert (e.g. Let's Encrypt), or
   `disable_verify` (below). `.local` names (`EMBODIED_LOCAL`'s `client-service-api.local`) cannot get a
   public cert, so they need `disable_verify` or a CA installed on the device (`/system` write / root).
3. **Run the broker + REST + STT** behind it ([cloud-protocol](cloud-protocol.md)).

### The proven recipe (OpenMoxie)

```conf
# /etc/mosquitto/conf.d/openmoxie.conf
listener 8883
cafile   /etc/letsencrypt/live/DOMAIN/chain.pem
keyfile  /etc/letsencrypt/live/DOMAIN/privkey.pem
certfile /etc/letsencrypt/live/DOMAIN/cert.pem
allow_anonymous true
```

The robot connects to `DOMAIN:8883`, validates the cert against its store, and (801+) authenticates anonymously.

## The `disable_verify` escape hatch

`ServiceConfiguration.disable_verify` (field 12) maps to `CURLOPT_SSL_VERIFYPEER=0` — the robot skips TLS
peer verification, so a **self-signed** cert works. Ways it reaches the robot:

- the **`om` QR** (`{"debug":{"command":"om","param":"<base64 ServiceConfiguration2>"}}`), decoded natively
  by `RightPoint::on_QRCommand` and written to `cloud.json` — this is how OpenMoxie's unverified MQTTS works;
  the handler is **absent below 24.10.801**;
- a `ServiceConfiguration` pushed over an already-connected MQTT/bus link (moving a running fleet).

The `PA` pairing QR (`StartPairingQR`) carries only an `IOTEndpoint` enum, never a host or `disable_verify`.
None of these paths helps pre-801, which never connects to you in the first place.

## Why pre-801 is stuck — precisely

Not cert pinning. Two *reachability* blocks:

1. **The endpoint hostname is hard-coded** to Google IoT (`mqtt.googleapis.com`) and has no QR re-home handler.
2. DNS-redirecting that hostname fails the TLS check: it needs a cert **valid for `mqtt.googleapis.com`**
   from a CA the robot trusts, which only Google can obtain.

The robot is willing to connect (it drops to QR-reading when offline — [boot-and-launcher](../firmware/boot-and-launcher.md));
you just can't present a trusted cert for the fixed hostname. Breaking that needs 801+ firmware or
on-device access to add a CA / edit the endpoint. (Older notes called this "hostname-pinned"; precisely,
the hostname is hard-coded and the cert is CA-validated. The practical block is the same, but it is why 801+
redirection works with an ordinary public cert.) A stranded unit still talks from its
[offline fallback tree](offline-and-brain-state.md).

## Time sync (clock skew breaks auth)

TLS validity (not-before/not-after) and the RS256 JWT `iat`/`exp` both fail on a wrong clock. The time
source is public and alive:

- `me.embodied.NTPService` runs SNTP against **`time.android.com, pool.ntp.org, time.nist.gov`** (default),
  refreshes periodically, sets the clock (`SntpClient` → `setTime`), falls through the list, and logs
  `BO#8013 Unable to reach any NTP servers` on total failure.
- **Overridable** via prop **`sys.embodied.ntp_servers`** (point at a local NTP for fully-offline networks).
- Timezone comes from the cloud/parent (`requestSetTimezone`; `RemoteChatRequest.timezone_id`).

A robot that boots offline (or after a dead battery) can fail its first TLS/JWT handshake on skew, then
self-correct once NTP is reachable. Fully-offline setups should run local NTP.

---
📖 [Reverse-engineering index](../README.md) · [Cloud protocol](cloud-protocol.md) · [OTA & recovery](../firmware/ota-and-recovery.md) · [Docs index](../../README.md)
