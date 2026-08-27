# Live hardware debugging log — reviving a real Moxie

Running notes from an actual revival attempt, so the next person's agent can move fast.
Chronological; newest findings at the bottom. **Bold = load-bearing fact.**

## Setup
- Real Moxie robot, owner-operated. Server = a Linux box (wired `enp5s0` on 192.168.1.9, plus
  Tailscale). Later added a USB Wi-Fi adapter (ALFA RTL8812AU, `rtl88XXau`) to host "Moxie Direct".
- Stack: parent-app server (:8080), mosquitto broker (Docker, TLS :8883), MQTT supervisor + Moxie SDK
  wired to a local LiteLLM gateway (model `qwen3.8-27b`).

## Confirmed facts (each cost real time — don't re-derive)
- **The robot's Wi-Fi/BT MAC is `d4:12:43:22:31:d8` (AMPAK).** Confirmed as Moxie by power-off test
  (its IP went DOWN the instant the robot was powered off). Use this MAC to identify it on any network.
- **Our clean-room Wi-Fi QR works on real hardware** — the robot scanned it and joined Wi-Fi.
  IMPORTANT: the first-stage QR must be **wifi-only** (`StartPairingQR.wifi_only=true`, NO secret_key).
  A pairing-key QR sends the robot chasing the dead Embodied cloud. Our `encode_wifi_only()` is
  byte-identical to OpenMoxie's `get_wifi_qr_data()` (verified).
- **Our endpoint QR (`{"debug":{"command":"om","param":...}}`) is byte-identical to OpenMoxie's**
  `get_endpoint_qr_data()` (verified against a clone). So QR *format* is never the problem.
- **"Moxie Direct" works**: hosting an AP on the box (nmcli hotspot, 2.4GHz) → the robot joins and
  gets a DHCP lease (e.g. 10.42.0.79). This eliminates all router/subnet/AP-isolation variables —
  strongly recommended for debugging. Broker reachable at the AP IP (10.42.0.1:8883).

## The wall we hit
- Shown the endpoint (`om`) QR, the robot **beeps (reads it) then returns to the QR screen asking for
  another code. It NEVER opens a socket to the broker** (zero SYN/TLS at the broker, confirmed by
  tcpdump on both the LAN and the AP).
- **Per OpenMoxie's author: zero packets = the robot did not accept the `om` command = firmware older
  than 24.10.801.** (An 801/803 bot gets *past* the QR screen and at least attempts a TCP/TLS
  connection, which would show in a capture.)
- **On-device firmware test (jbeghtol, issue #43): look UNDER the QR box on Moxie's face.** A text
  badge **"EmbodiedProduction" or "OpenMoxie"** = firmware 801/803 (relocatable). **No badge, just a
  Wi-Fi/robot icon = pre-801, too old for the `om` QR.** (May only show after it joins known Wi-Fi.)
- Firmware thresholds: **801** = supports `om` relocation but needs a **CA-signed** broker cert;
  **803** = also accepts **self-signed** (`disable_verify`). Recovery for <801: jbeghtol's 801→803 OTA
  (issue #57, needs the bot already on 801) or a paid reflash service (r/MoxieRobot).

## The smoking gun — old firmware uses Google Cloud IoT Core
- While stuck on the QR screen, the robot (on our AP) **repeatedly connects to `172.217.116.4:443`
  with TLS SNI `mqtt.googleapis.com`** (every ~7s). That is **Google Cloud IoT Core** — which Google
  **shut down in Aug 2023.** So this firmware predates Embodied's migration off Google IoT *and* the
  801 relocation feature. Independent confirmation the firmware is old.
- No DNS query was seen for it — the robot uses a cached/hardcoded Google IP.

## The "fake their server" idea (in progress)
Since we host the robot's network, we can intercept `mqtt.googleapis.com` and point it at our broker.
If the robot does NOT strictly validate the server TLS cert (relying only on its device JWT), a
self-signed cert (CN=mqtt.googleapis.com) could let a **pre-801** robot connect to us — reviving bots
OpenMoxie can't. Google IoT Core uses the same `/devices/{id}/config|events|state` topics our
supervisor already speaks. **Open question being tested: does the robot validate the Google server
cert?** If yes → blocked (need the reflash/OTA path). If no → we have a new revival path.
Method: mosquitto listener on :443 with a CN=mqtt.googleapis.com cert + iptables DNAT of the robot's
:443 → broker + DNS spoof of mqtt.googleapis.com. Watch the broker for a TLS ClientHello from the bot.

## Cert test result (fake Google IoT Core)
- DNAT (robot :443 → broker :8883) + a self-signed cert `CN=mqtt.googleapis.com` → the robot **DOES
  reach our broker** (DNAT works) but **rejects the cert: `tlsv1 alert unknown ca`**. So this firmware
  **validates the server cert against its bundled Google roots** — a self-signed cert can't pass.
- Implication: faking Google IoT Core needs a cert chaining to a root the robot trusts (can't forge
  Google's), OR getting onto the robot to change its trust/endpoint. Next probe: **ADB** (it's an
  Android device on our AP at 10.42.0.79).

## ADB / on-device access
- Port scan of the robot (10.42.0.79) — **no open ports** (5555 adb, 22, etc. all closed). ADB-over-
  network is OFF and nothing listens. Consistent with the locked-down Android client.
- ADB-over-USB is a separate channel (untested here) — worth trying on a locked unit but low odds.

## Honest conclusion for a pre-801 / Google-IoT robot
Software-only revival is blocked by TLS: the robot validates `mqtt.googleapis.com`'s cert against
bundled Google roots, we can't forge that, and there's no network way onto the device to change its
trust or endpoint. **Definitive check = the on-device badge** (§firmware). If pre-801, the path is a
firmware bump to 803 (community OTA needs the bot already on 801; else a reflash service). Once on
803, our stack (broker + supervisor + SDK + local LLM) is proven and ready — this is purely a
firmware-gap problem on this specific unit, not a problem with the server side.

## State of the running stack (for whoever continues)
- Broker cert is currently CN=mqtt.googleapis.com (SAN also 10.42.0.1) + an iptables DNAT of the
  robot's :443→broker and a dnsmasq spoof of mqtt.googleapis.com→10.42.0.1 are ACTIVE (the fake-Google
  experiment). To revert to normal Moxie-Direct/803 use: regenerate the broker cert for the broker IP
  (broker/gen-certs.sh <ip>), remove the DNAT (`iptables -t nat -D PREROUTING ...`) and the
  /etc/NetworkManager/dnsmasq-shared.d/moxie-spoof.conf file, and restart the broker.

## QR command surface (for easter-egg hunting)
The robot's Wifi App QR handler (`RightPoint::on_QRCommand`) dispatches on `QRCommand.command` (string;
"om" is the only known value) — there is also a `code` (field 2) and `param` (field 3) string field.
Full QR proto message set the firmware understands: QRCommand, QRResponse, QRDiagnosticData
{robot_uuid, rsa_pub, cloud_connected, cloud_project, software_version}, StartPairingQR,
WifiNetworkUpdate, QRMultiDecoder{debug:QRCommand, encoded_proto}, and **QRVPNConfig**
{command: VPN_DOWNLOAD/REVERT/CREDENTIALS/ACTIVATE/DEACTIVATE, vpn_id, url, username, password}.
The debug command STRINGS are in the robot firmware (`bo-wifi.apk`), not the parent app. Next: find
bo-wifi.apk / an OTA image to decompile the handler, and/or probe candidate command strings by QR.
Note: a VPN QR routes traffic but does NOT by itself defeat the mqtt.googleapis.com cert check.
