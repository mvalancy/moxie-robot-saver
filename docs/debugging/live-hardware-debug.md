# Live hardware debugging log — reviving a real Moxie

For an owner whose robot reads the server code, beeps, and never connects. These are the bench notes
from one attempt to revive a real Moxie in August 2026, kept so nobody has to re-derive them. The
robot turned out to be older than firmware 24.10.801, and that gate decides everything below.
**Bold = load-bearing fact.**

> **Bench session, 2026-08: what it established.** Our Wi-Fi code and our server code work on real
> hardware, and the first code must be Wi-Fi-only. A pre-801 robot reads the server code and opens no
> connection: it talks only to Google Cloud IoT Core and validates that server's certificate, so no
> network trick re-homes it. Each thread now has a home. The owner path is
> [Revive your Moxie](../guides/revive-your-moxie.md) (Path C for a pre-801 unit); the QR grammar is
> [QR commands](../reverse-engineering/protocol/qr-commands.md); the TLS reasoning is
> [network trust](../reverse-engineering/protocol/network-trust.md#why-pre-801-is-stuck-precisely); the
> flash route is the [flashing runbook](../reverse-engineering/firmware/flashing-runbook.md).

## Setup

- One real Moxie, owner-operated. The server was a Linux box on the home LAN, with a USB Wi-Fi adapter
  added later to host a "Moxie Direct" hotspot.
- The stack: parent-app server (`:8080`), mosquitto broker in Docker (TLS `:8883`), the MQTT supervisor
  and Moxie SDK, with a local OpenAI-compatible gateway (LiteLLM) as the brain.

## Confirmed facts

Each of these cost real time; do not re-derive them.

- **The robot's Wi-Fi/Bluetooth MAC is an AMPAK `d4:12:43:xx:xx:xx` address.** Confirmed as Moxie by a
  power-off test: its lease went down the instant the robot was powered off. Use the AMPAK prefix to
  pick it out on any network ([find Moxie on the LAN](../guides/find-moxie-on-lan.md)).
- **Our clean-room Wi-Fi QR works on real hardware**: the robot scanned it and joined Wi-Fi.
  **The first-stage QR must be wifi-only** (`StartPairingQR.wifi_only=true`, NO secret_key). A
  pairing-key QR sends the robot chasing the dead Embodied cloud. Our `encode_wifi_only()` is
  byte-identical to OpenMoxie's `get_wifi_qr_data()` (verified).
- **Our endpoint QR (`{"debug":{"command":"om","param":...}}`) is byte-identical to OpenMoxie's**
  `get_endpoint_qr_data()` (verified against a clone). So QR *format* is never the problem.
- **"Moxie Direct" works.** Host an access point on the server box (nmcli hotspot, 2.4GHz): the robot
  joins and takes a DHCP lease on the hotspot subnet, and the broker is reachable at the hotspot's own
  address on `:8883`. This eliminates every router, subnet and AP-isolation variable; strongly
  recommended for debugging.

## The wall: the robot reads the `om` code and never connects

- Shown the endpoint (`om`) QR, the robot **beeps (reads it) then returns to the QR screen asking for
  another code. It NEVER opens a socket to the broker**: zero SYN/TLS at the broker, confirmed by
  tcpdump on both the LAN and the hotspot.
- **Per OpenMoxie's author: zero packets = the robot did not accept the `om` command = firmware older
  than 24.10.801.** An 801/803 robot gets *past* the QR screen and at least attempts a TCP/TLS
  connection, which shows in a capture.
- Firmware thresholds: **801** = supports `om` relocation but needs a **CA-signed** broker cert;
  **803** = also accepts **self-signed** (`disable_verify`).
- Recovery for a robot older than 801: the community 801→803 OTA is **closed**; the maintainer will not
  distribute the image (OpenMoxie issue #57, 2026-08-29). The remaining routes are the self-flash
  ([Revive your Moxie, Path C](../guides/revive-your-moxie.md#path-c-flash-an-older-robot-first),
  [flashing runbook](../reverse-engineering/firmware/flashing-runbook.md#reviving-a-stranded-robot-by-flashing))
  or a paid reflash service (r/MoxieRobot).

## Triage by what the robot shows

| What you see | What it means |
|---|---|
| A text badge **"EmbodiedProduction"** or **"OpenMoxie"** UNDER the QR box on Moxie's face | Firmware 801/803, relocatable (jbeghtol, OpenMoxie issue #43). The badge may only show after the robot joins a known Wi-Fi. |
| No badge, just a Wi-Fi/robot icon | Pre-801, too old for the `om` QR. This is what this unit showed. |
| The word **OpenMoxie** on the QR-scan screen | 801 or 803 (the maintainer, issue #57); its absence means pre-801. Not seen on this bench. |

## Old firmware talks to Google Cloud IoT Core

- Stuck on the QR screen and on our hotspot, the robot **repeatedly connected to `172.217.116.4:443`
  with TLS SNI `mqtt.googleapis.com`** (every ~7s). That is **Google Cloud IoT Core**, which Google
  **shut down in Aug 2023**. So this firmware predates Embodied's migration off Google IoT *and* the
  801 relocation feature: independent confirmation that the firmware is old.
- **No DNS query was seen for it**: the robot uses a cached/hardcoded Google IP.
- Google IoT Core uses the same `/devices/{id}/config|events|state` topics our supervisor already
  speaks, so a robot that reached our broker would be understood.

## The fake-Google-IoT-Core test and its result

The idea: since we host the robot's network, intercept `mqtt.googleapis.com` and point it at our
broker. If the robot did not strictly validate the server TLS cert (relying only on its device JWT), a
self-signed cert with `CN=mqtt.googleapis.com` could let a **pre-801** robot connect to us, reviving
robots OpenMoxie can't. The question under test: does the robot validate the Google server cert?

Method: a mosquitto listener with a `CN=mqtt.googleapis.com` cert, an iptables DNAT of the robot's
:443 to the broker, and a DNS spoof of `mqtt.googleapis.com`; then watch the broker for a TLS
ClientHello from the robot.

Result: the robot **DOES reach our broker** (DNAT works) but **rejects the cert: `tlsv1 alert unknown
ca`**. So this firmware **validates the server cert against its bundled Google roots**; a self-signed
cert can't pass. Faking Google IoT Core needs a cert chaining to a root the robot trusts (can't forge
Google's), OR getting onto the robot to change its trust/endpoint. The precise reasoning is in
[network trust](../reverse-engineering/protocol/network-trust.md#why-pre-801-is-stuck-precisely).

## Undoing the fake-Google experiment

The experiment leaves three things on the server box. To return to normal Moxie-Direct/803 use:

1. regenerate the broker cert for the broker's own address (`broker/gen-certs.sh <ip>`);
2. remove the DNAT rule (`iptables -t nat -D PREROUTING ...`);
3. remove the dnsmasq spoof file (`/etc/NetworkManager/dnsmasq-shared.d/moxie-spoof.conf`);
4. restart the broker.

## ADB and on-device access

- A port scan of the robot on the hotspot found **no open ports** (5555 adb, 22, etc. all closed).
  ADB-over-network is OFF and nothing listens, consistent with the locked-down Android client
  ([hardware access](../reverse-engineering/hardware/hardware-access.md#adb-usb-when-booted-normally)).
- ADB-over-USB is a separate channel (untested here); worth trying on a locked unit but low odds.

## Conclusion for a pre-801 robot

Software-only revival is blocked by TLS: the robot validates `mqtt.googleapis.com`'s cert against
bundled Google roots, we can't forge that, and there's no network way onto the device to change its
trust or endpoint. **Definitive check = the on-device badge** (the triage table above). A pre-801 unit
needs new firmware: the flash path, or a paid reflash service. Once on 803, our stack (broker +
supervisor + SDK + local LLM) is proven and ready. This is purely a firmware-gap problem on this
specific unit, not a problem with the server side.

## The QR command surface

The hunt for hidden QR commands that followed this session is closed. The grammar was read from the
firmware: the setup app acts on exactly four debug commands, the native cloud module
(`RightPoint::on_QRCommand`) on three codes (`report`, `endpoint_update`, `om`), plus the `PA`, `VN` and
JSON forms ([QR commands](../reverse-engineering/protocol/qr-commands.md); the rig's measurements are in
[QR rig findings](qr-command-findings.md)). The QR message set the firmware understands (`QRCommand`
with its `code` and `param` fields, `QRResponse`, `QRDiagnosticData`, `StartPairingQR`,
`WifiNetworkUpdate`, `QRMultiDecoder`, `QRVPNConfig`) is on that page and in the recovered
[`wifiapp` schemas](../reverse-engineering/protocol/recovered-proto/embodied/wifiapp/README.md); the
command strings live in the robot firmware (`bo-wifi.apk`), not the parent app. One fact stated nowhere
else: **a VPN QR routes traffic but does NOT by itself defeat the mqtt.googleapis.com cert check.**

---
📖 [Bench notes](README.md) · [Revive your Moxie](../guides/revive-your-moxie.md) · [Bench runbook](../guides/bench-runbook.md) · [QR commands](../reverse-engineering/protocol/qr-commands.md) · [Network trust](../reverse-engineering/protocol/network-trust.md) · [Docs index](../README.md)
