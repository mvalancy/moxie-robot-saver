# 🎫 QR command grammar — what the robot actually scans

The **complete, closed QR command space** of the robot, read from the `v24.10.803` binaries. The setup
app `bo-wifi` parses three forms — `PA` pairing, `VN` VPN, and JSON `{wifi?, pair?, debug?}` — and acts
on **exactly four** debug commands itself; every `debug` command is also forwarded to the bus as a
`QRCommand`, where the native cloud module `RightPoint` handles **exactly three** codes (`report`,
`endpoint_update`, `om`). `om` is the real re-home payload. The grammar is provably bounded — there is
nothing further to discover. Separately, the running brain has a content-QR scanner for play.
Phone-side encoding of the pairing QR: [`qr-format.md`](../phone/qr-format.md).

## The dispatcher

`bo-wifi` (`WifiApp.dll` → `QRData.ParseFromString(string)` + the `embodied.wifiapp` protos) branches on a
**2-char prefix**, else falls back to JSON:

| Form | Prefix | Payload | Meaning |
|---|---|---|---|
| **Pairing** | `PA` | `PA` + Base64(`StartPairingQR` protobuf) | Wi-Fi + pairing secret + endpoint (the normal setup QR) |
| **VPN config** | `VN` | `VN` + Base64(`QRVPNConfig` protobuf) | install/activate/revert a VPN profile |
| **JSON** | *(none)* | raw JSON `{wifi?, pair?, debug?}` | Wi-Fi creds, legacy pairing, and/or a debug/factory command |

A `debug` block becomes a `QRCommand{code, param}` protobuf **published on the ZMQ bus** via `QRDebug()`.

## JSON debug/factory commands

```json
{ "debug": { "command": "<code>", "param": "<value>" } }
```

Codes handled directly in `bo-wifi` (`WifiMain`):

| `command` | Effect (Wifi App) |
|---|---|
| `serial_number_display` | Switch to the **serial-number display** screen (`State.QRSerialNumber`). |
| `restore_factory` | Enter **factory restore** flow (`State.UserRestoreRequest`). |
| `reset_network` | **Forget all Wi-Fi** and reconnect (`DisconnectAll()`). |
| `bluetooth_pair` | Fire an Android intent to **Bluetooth-pair** the device in `param`. |
| *(any other code)* | Show `State.QRDiagnostic` and forward `QRCommand{code,param}` to `bo-android`. |

**The scan grammar is closed — this is the whole set.** `QRData.ParseFromString` has exactly three
branches (`PA`, `VN`, else JSON). `debug.command` is matched against **exactly four** literals; everything
else hits a literal `else → SetAppState(State.QRDiagnostic)`. There is no fifth app-side handler anywhere in
`bo-wifi`, so the setup app's command set is provably these four plus the pairing/VPN/Wi-Fi forms.

Every debug command — recognized or not — is also published as `QRCommand`. **The managed brain does not
consume it**: `Assembly-CSharp` (the 7 MB `bo-android` decompile) has **zero** references to
`QRCommand`/`endpoint_update`/`QRDebug`. The consumers are native ([native-boundary](../runtime/native-boundary.md#resolved-who-consumes-qrcommand-the-setup-qr-brain-bridge)):
**`libbo-logger`** (`embodied::logging::cloud::RightPoint`, the cloud/MQTT module) and
**`libbo-system-monitor`** (`SystemStatusService`, system-level codes), with **`libwatchdog`** (the
launcher) relaying the proto.

### `QRCommand` protobuf (`embodied.unity`)

```proto
message QRCommand {
  uint64 timestamp = 1;
  string code      = 2;   // the "command"
  string param     = 3;   // the "param"
  embodied.logging.IOTEndpoint endpoint = 4;  // for endpoint_update
  string command   = 5;
  string software_version = 100;
  string module_name      = 101;
}
message QRMultiDecoder { embodied.unity.QRCommand debug = 1; bytes encoded_proto = 2; }
```

`QRResponse{response_code, response}` and `QRDiagnosticData{robot_uuid, rsa_pub, cloud_connected,
user_state, cloud_project}` come back the other way (shown on the diagnostic screen). `QRMultiDecoder`
lets one QR carry either a debug command or an **arbitrary encoded protobuf** (`encoded_proto`) — a
general-purpose protobuf-injection container.

## The effective command set — native dispatch (`RightPoint::on_QRCommand`)

Ghidra (PyGhidra) decompilation of `on_QRCommand` in `libbo-logger.so` (which carries DWARF; see
[methodology](../PLAYBOOK.md#using-ghidra-via-pyghidra)) shows an **exact string match** on
`QRCommand.code` against **exactly three** commands:

| `QRCommand.code` | Handler in `embodied::logging::cloud::RightPoint` | Effect (from the decompiled body) |
|---|---|---|
| **`report`** | `on_DiagnosticDataRequest` | Build `QRDiagnosticData{robot_uuid, rsa_pub, cloud_connected, cloud_project}` (UUID from `core::UUID::GetDevice()`, RSA pubkey via `Client::LoadFile`), serialize (`MessageToJsonString`, wrapped as `{"encoded_proto":"…"}`), and **post it** on a worker thread (`RPTokenURL::post_diagnostics`). The diagnostic trigger is named `report`, not "diag". |
| **`endpoint_update`** | `on_EndpointUpdate(json_t*, string)` | Look the `endpoint` profile up in an **`EndpointMap`** (`ForName`); if valid, build `GoogleIOTOpts`/`ConnectionOpts`, **write `cloud.json`**, and *"Exit… to restart logger"*. Invalid name → *"Received endpoint_update to invalid endpoint"*. |
| **`om`** | `on_EndpointUpdate` (OpenMoxie path) | Base64-decode `param` → `ServiceConfiguration2`; write `cloud.json` as `{"endpoint":"openmoxie"}` and *"Updating to OPEN_MOXIE and exiting to restart"*. Bad base64 → *"Invalid open_moxie configuration QR - base64 decode failed."* |
| *(anything else)* | — | *"Received unsupported QR Diagnostic Command"* / *"Unknown QR Diagnostic Command"*. |

Applying `endpoint_update`/`om` **restarts the logger process** to reconnect ([`cloud.json`](cloud-protocol.md#cloudjson-the-persisted-active-config)).
Because the handlers take a `json_t*`, the same actions are reachable over MQTT too — QR is one transport.
`ClearResetUserFlag` and `GetServiceConfig`/`InitServiceConfig` are supporting members, not QR codes.

**Where `endpoint_update` comes from.** The Wifi App emits it *internally* (`RehomeNeeded()` →
`RequestEndpoint()`) when a **pairing QR's** `endpoint` field differs from the current cloud, carrying an
`IOTEndpoint` enum in `QRCommand.endpoint`. `bo-wifi` has no handler of its own for the string
`endpoint_update`; a JSON `debug` QR with that code is forwarded like any unrecognized code. This repo's
encoders emit that form (profile name in `param`); whether `EndpointMap::ForName` accepts those names is
unverified on hardware. The robust way to move a robot between clouds is the pairing QR's `endpoint`
field or `om`.

### The `om` relocation — the real re-home payload

OpenMoxie's dashboard "Migration QR" (`moxie_server.py:get_endpoint_qr_data`) emits:

```json
{ "debug": { "command": "om", "param": "<base64(ServiceConfiguration2)>" } }
```

`ServiceConfiguration2` (`embodied/logging/Cloud2.proto`) is field-for-field the
[`ServiceConfiguration`](cloud-protocol.md#service-configuration-how-the-robot-is-repointed) message (fields 1–12).
`mqtt_host` / `endpoint` / `override_port` point the robot at your broker; **`disable_verify` (field 12)**
relaxes cloud cert verification, which is why an OpenMoxie/self-signed host is accepted
([network-trust](network-trust.md#the-disable_verify-escape-hatch)). The `OPEN_MOXIE`/relocation handler is
**absent below firmware 24.10.801** — the pre-801 wall.

## Pairing QR — `PA` + `StartPairingQR`

```proto
message StartPairingQR {
  string ssid = 1;
  string password = 2;
  bool   is_staging = 3;
  bytes  secret_key = 4;      // the pairing secret (Ed25519 material)
  bool   wifi_only = 5;       // set wifi without pairing
  bool   is_hidden = 6;       // hidden SSID
  enum WifiBandSelect { ANY = 0; ONLY_50G = 1; ONLY_24G = 2; }
  WifiBandSelect band_select = 7;
  embodied.logging.IOTEndpoint endpoint = 8;   // which cloud to home to
}
```

The robot re-homes only if `endpoint` ([values](cloud-protocol.md#the-built-in-endpoint-hosts-baked-into-libbo-logger))
differs from the current one. Phone-side encoder: [`qr-format.md`](../phone/qr-format.md),
[`tools/pairing/moxie_qr.py`](../../../tools/pairing/moxie_qr.py).

## VPN QR — `VN` + `QRVPNConfig`

```proto
message QRVPNConfig {
  uint64 timestamp = 1;
  enum VPNCommand { UNKNOWN_VPN_COMMAND=0; VPN_DOWNLOAD=1; VPN_REVERT=2; VPN_CREDENTIALS=3; VPN_ACTIVATE=4; VPN_DEACTIVATE=5; }
  VPNCommand command = 2;
  string vpn_id = 3;
  string url = 4;         // where to fetch the profile
  string username = 5;
  string password = 6;
  bool   connect = 7;
}
```

Logged as `Read VPN Config Code: Command: <n>` and published to the brain over ZMQ — a plausible lever
for routing a stock robot's traffic through infrastructure you control.

## Wi-Fi provisioning support (what networks work)

The QR Wi-Fi path (`bo-wifi` `AndroidWiFi.Connect(ssid, psk, isHidden)`) builds a legacy Android-9
`android.net.wifi.WifiConfiguration` and calls `addNetwork`/`enableNetwork`:

| Network type | Supported? |
|---|---|
| **Open** (no password) | yes (empty `psk` → `KeyMgmt.NONE`) |
| **WPA / WPA2-Personal (PSK)** | yes (`preSharedKey` → `WPA_PSK`) |
| **Hidden SSID** | yes (`hiddenSSID`; `StartPairingQR.is_hidden`) |
| Band hint (any / 5 GHz / 2.4 GHz) | yes, via `band_select` |
| **WPA3-only (SAE)** | no — no SAE key-mgmt (legacy API + BCM4339) |
| **WPA2-Enterprise / 802.1X / EAP** | no enterprise config |
| **Captive portal** | no — needs a browser |

A home WPA2-PSK (or open, or hidden) router works; switch WPA3-only routers to WPA2/WPA3-mixed, and use a
phone hotspot instead of enterprise/campus or captive-portal networks.

**Post-pairing Wi-Fi push** — a server can add/change Wi-Fi over MQTT with `embodied.wifiapp.WifiNetworkUpdate`:

```proto
message WifiNetworkUpdate {
  embodied.unity.StartPairingQR wifi_info = 2;  // reuses ssid/password/is_hidden/band_select
  bool add_only = 3;                             // true = add alongside; false = switch/replace
}
```

Same support matrix. `bo-wifi`'s `UI_Connect()` hard-codes the **factory** network `"Embodied Guest"` /
`"Embodied<3robots!"`, matching the `EmbodiedPSK` recovered from `libsecrets`
([factory-provisioning](../firmware/factory-provisioning.md)).

## Manufacturing QR codes

The factory apps (`me.embodied.productiontesting.*`) generate QRs with `androidmads`' `QRGEncoder`
(`qr/QR.java`) from an enum in `qr/Codes.java`; the shipped entry is

```java
DisplaySerialNumber("Display Device Serial Number", "{\"debug\":{\"command\":\"serial_number_display\"}}")
```

— the same `{"debug":{"command":…}}` channel, so any generator emitting this JSON produces a
factory-format QR. The serial/part barcode grammar the factory scanners *read* is in
[factory-provisioning](../firmware/factory-provisioning.md).

## The setup app's runtime status — `WifiAppStatus` / `WifiAppBricked`

`bo-wifi` publishes its own state on the ZMQ bus (`embodied.unity.WifiAppStatus` et al., `wifiapp` file
group), so `bo-android` or a [bus observer](robot-ipc-protocol.md) can tell whether a stranded robot is
ready to scan (the `STATE_CONFIG` surface, [boot-and-launcher](../firmware/boot-and-launcher.md#states-launcherstate)).

**`WifiAppStatus { uint32 code }`** (`WifiAppStatusCodes`):

| Code | Name | Meaning |
|--:|---|---|
| 1 | `WifiAndUserGood` | Wi-Fi **and** a paired user are valid — nothing to set up |
| 100 | `WifiAppReady` | up and **ready to scan a pairing/Wi-Fi/debug QR** — the moment to show a re-home QR |
| 101 | `WantsToDisplaySomething` | needs the screen (prompt/diagnostic) |
| 1977 | `Alive` | heartbeat |
| 1978 | `Unquiet` | heartbeat variant (active/needs attention) |

`WifiAppSilentBoot` / `WifiAppShutdown` mark booting without UI / shutting down (the `STATE_SILENT_REBOOT`
path, [power-and-system-events](power-and-system-events.md)). **`WifiAppBricked { uint32 error_code }`** —
the setup app failed to come up; `error_code` is `EBErrorCode` (`Assembly-CSharp`):

| # | `EBErrorCode` | Meaning |
|--:|---|---|
| 0 | `UNKNOWN` | unclassified |
| 5000 | `UNHANDLED_EXCEPTION` | a C# exception crashed the app |
| 5001 | `STREAMING_ASSETBUNDLES` | failed loading a **streamed** asset bundle |
| 5002 | `LOCAL_ASSETBUNDLES` | failed loading a **local** asset bundle |
| 5003 | `REMOTE_ASSETBUNDLES` | failed loading a **downloaded** asset bundle |
| 5004 | `ASSETBUNDLE_GENERAL` | other asset-bundle load failure |
| 5005 | `ASSERTION` | a code assertion tripped |
| 5006 | `ZMQ` | the [ZMQ bus](robot-ipc-protocol.md) failed to come up |
| 5007 | `ASSETBUNDLE_INVALID` | corrupt/incompatible asset bundle |

5001–5004/5007 are asset problems (re-flash / content re-sync), 5000/5005 a code crash, 5006 the bus.
Hardware faults come instead from the Lizard MCU as `LizardErrorEvent` (`BATTERY_OVER_TEMP`,
`BATTERY_LOST`, `MOTOR_FAIL_BOOT`, `BODYTOUCH_ERR`; [hardware-map](../hardware/hardware-map.md)). A bricked
setup app cannot scan, so that unit needs the physical [recovery path](../firmware/ota-and-recovery.md), not a QR.

## Runtime content QR — the second scanner (`bo-android`, in play)

Once paired, the brain has a separate QR path for content, not configuration:

- **`embodied.robotbrain.EnableQRCode{ bool run }`** — turns camera QR-reading on/off for a moment of content.
- **`embodied.perception.vision.QRPB{ string qrcode }`** — the [vision module `libbo-analytics`](../runtime/native-boundary.md#the-full-module-roster-what-each-remaining-bo-so-actually-is)
  decodes (OpenCV `wechat_qrcode` + ZBar) and publishes the string.

A server can drive it by toggling `EnableQRCode` and reacting to `QRPB`
([perception-pipeline](../runtime/perception-pipeline.md#vision-embodiedperceptionvision), [content-and-conversation](../runtime/content-and-conversation.md)).

## Toolkit — generate & validate these codes

[`tools/robot-toolkit/`](../../../tools/robot-toolkit/) (`moxie_toolkit/qr_codec.py`):

```sh
python -m moxie_toolkit.cli endpoint OPEN_MOXIE --png redirect.png   # re-home QR as a PNG
python -m moxie_toolkit.cli debug reset_network                      # factory debug command
python -m moxie_toolkit.cli validate                                 # 27 checks, incl. byte-parity
```

Generators are validated by schema round-trip and by byte-identical `PA` payloads against the
independently reverse-engineered phone-side encoder ([`tools/pairing/moxie_qr.py`](../../../tools/pairing/moxie_qr.py)).

In the browser, [`sim/web/qr.js`](../../../sim/web/qr.js) encodes the JSON forms (`endpoint_update`, `wifi`,
`debug` — no protobuf) client-side for the simulator's **Revive a robot** panel. It deliberately matches
Python `json.dumps` spacing (`{"a": 1}`) rather than `JSON.stringify` (`{"a":1}`); the robot doesn't care,
but `node sim/test_qr.mjs` asserts all seven payload shapes are byte-identical to `moxie_toolkit.qr_codec`.
Protobuf-bearing codes (`PA`, `QRMultiDecoder.encoded_proto`) stay in the Python toolkit.

The earlier acoustic brute-force log ([`qr-command-findings.md`](../../debugging/qr-command-findings.md)) is
superseded by this decompiled grammar and kept only as a historical record.

---
📖 [Reverse-engineering index](../README.md) · [Phone-side QR format](../phone/qr-format.md) · [Docs index](../../README.md) · [Back to top](../../../README.md)
