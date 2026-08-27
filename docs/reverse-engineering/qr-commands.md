# 🎫 QR command grammar — what the robot actually scans

> **What this is.** The **complete** set of QR codes Moxie's setup app (`bo-wifi`, the "Wifi App")
> recognizes, recovered from its managed code (`WifiApp.dll` → `QRData.ParseFromString`) and the
> `embodied.wifiapp` protobufs. This is the *robot-side* truth that complements the *phone-side*
> [`qr-format.md`](qr-format.md) (pairing QR as the parent app emits it).
>
> ⚠️ **Supersedes guesswork.** The acoustic brute-force log in
> [`../debugging/qr-command-findings.md`](../debugging/qr-command-findings.md) was speculating at the
> grammar from the outside. This document is the grammar, read directly from the binary — prefer it.

## The dispatcher

`bo-wifi` scans a code, then `QRData.ParseFromString(string)` branches on a **2-char prefix**, else
falls back to JSON:

| Form | Prefix | Payload | Meaning |
|---|---|---|---|
| **Pairing** | `PA` | `PA` + Base64(`StartPairingQR` protobuf) | Wi-Fi + pairing secret + endpoint (the normal setup QR) |
| **VPN config** | `VN` | `VN` + Base64(`QRVPNConfig` protobuf) | Install/activate/revert a VPN profile on the robot |
| **JSON** | *(none)* | raw JSON `{wifi?, pair?, debug?}` | Wi-Fi creds, legacy pairing, and/or a **debug/factory command** |

Anything parsed as a `debug` block is turned into a `QRCommand{code, param}` protobuf and
**published on the ZMQ bus to `bo-android`** via `QRDebug()` — so the robot brain, not just the setup
app, acts on it. Four codes are additionally special-cased by the Wifi App's own UI state machine;
everything else just shows the "QR diagnostic" screen and forwards the command.

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

Because unrecognized codes are forwarded verbatim to the brain over ZMQ, the **effective** command
set is whatever `bo-android`/`libbo-dispatch` handles for `embodied.unity.QRCommand` — a broader,
extensible surface. Confirmed forwarded control codes include **`endpoint_update`** (with an
`IOTEndpoint`), used to move the robot between clouds.

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
```

`QRResponse{response_code, response}` and `QRDiagnosticData{robot_uuid, rsa_pub, cloud_connected,
user_state, cloud_project}` come back the other way — the diagnostic screen displays them.

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

The robot infers/honors the target cloud from `endpoint` (see enum below) and only "re-homes" if it
differs from the current endpoint (`RehomeNeeded()` / `RequestEndpoint()` → `QRCommand code=endpoint_update`).
For the phone-side encoder that produces this, see [`qr-format.md`](qr-format.md) and
[`../../tools/pairing/moxie_qr.py`](../../tools/pairing/moxie_qr.py).

## VPN QR — `VN` + `QRVPNConfig`

A QR can push a whole VPN profile onto the robot:

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

Read → logged as `Read VPN Config Code: Command: <n>` → published to the brain over ZMQ. This is a
plausible lever for routing a stock robot's cloud traffic through infrastructure you control.

## `IOTEndpoint` — the cloud selector (`embodied.logging`)

Real enum value names (from the recovered descriptor):

```
IOT_DEFAULT=0  GOOGLE_DEVELOP=1  GOOGLE_STAGING=2  GOOGLE_PRODUCTION=3
EMBODIED_DEVELOP=4  EMBODIED_STAGING=5  EMBODIED_PRODUCTION=6  EMBODIED_HIPAA=7
EMBODIED_LOCAL=8  EMBODIED_CHINA=9  EMBODIED_HK=10  OPEN_MOXIE=11
```

Two values matter for revival, and **both are baked into the shipped 803 firmware**:

- **`OPEN_MOXIE=11`** — a first-class endpoint for the community server. Confirmed by
  `[OriginalName("OPEN_MOXIE")]` in *both* `WifiApp.Protos.dll` and `Embodied.Protos.dll`. The
  firmware natively knows how to home to an OpenMoxie-style server.
- **`EMBODIED_LOCAL=8`** — a local-server endpoint.

Combined with `endpoint_update` via a `debug` QR, this is the path to point a stock robot at a server
you control (this repo's [`server/`](../../server/) + [`mqtt/`](../../mqtt/), or OpenMoxie). Note this
only **redirects** the robot's cloud — running *custom software on the robot itself* is a separate
effort (see [`firmware-image.md`](firmware-image.md)).

## Manufacturing QR codes

The factory line's own apps (`me.embodied.productiontesting.*`) **generate** QR codes with
`androidmads`' `QRGEncoder` (`qr/QR.java`) and drive them from an enum, `qr/Codes.java`. The shipped
entry is:

```java
DisplaySerialNumber("Display Device Serial Number", "{\"debug\":{\"command\":\"serial_number_display\"}}")
```

i.e. **manufacturing QR codes ride the exact same `{"debug":{"command":…}}` JSON channel** documented
above — the factory just pre-bakes specific codes. So any generator that emits this JSON produces a
"factory-format" QR the robot treats identically. The serial/part grammar the factory scanners *read*
(barcodes, not command QRs) is in [`factory-provisioning.md`](factory-provisioning.md).

## Toolkit — generate & validate these codes

A runnable encoder/validator lives at [`../../tools/robot-toolkit/`](../../tools/robot-toolkit/):

```sh
python -m moxie_toolkit.cli endpoint OPEN_MOXIE --png redirect.png   # re-home QR as a PNG
python -m moxie_toolkit.cli debug reset_network                      # factory debug command
python -m moxie_toolkit.cli validate                                 # 27 checks, incl. byte-parity
```

Every generator is validated by schema round-trip **and** by producing byte-identical `PA` payloads
to the independently reverse-engineered phone-side encoder ([`../../tools/pairing/moxie_qr.py`](../../tools/pairing/moxie_qr.py)) — strong evidence the recovered grammar is exactly right.

## Also carried: `QRMultiDecoder`

```proto
message QRMultiDecoder { embodied.unity.QRCommand debug = 1; bytes encoded_proto = 2; }
```

A container letting one QR carry either a debug command or an **arbitrary encoded protobuf**
(`encoded_proto`) — i.e. the QR channel is a general-purpose way to inject a protobuf into the robot.

---
📖 [Reverse-engineering index](README.md) · [Phone-side QR format](qr-format.md) · [Docs index](../README.md) · [Back to top](../../README.md)
