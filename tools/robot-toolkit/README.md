# 🧰 Moxie robot toolkit

Runnable tools for talking to a Moxie robot the way its own firmware does — starting with the
**QR channel**, the one input a stock robot will act on with **no disassembly and no account**: you
hold a code up to its camera. Built on the [recovered protobuf schemas](../../docs/reverse-engineering/recovered-proto/).

> **North star:** a non-technical owner holds their phone up to a stuck robot and it comes back to
> life — re-homed to a working server, network reset, or re-paired. Every generator here emits codes
> the robot's `bo-wifi` setup app actually parses (validated by schema round-trip **and** byte-parity
> with the independently reverse-engineered phone-side encoder).

## Install

```sh
pip install protobuf segno pyzmq    # segno=QR images, pyzmq=on-device bus client
```

## QR CLI

```sh
# Re-home a robot to a community / self-hosted server (the OPEN_MOXIE endpoint is built into 803 fw)
python -m moxie_toolkit.cli endpoint OPEN_MOXIE --png redirect.png

# Consumer/factory debug commands (same JSON the factory line's QR generator uses)
python -m moxie_toolkit.cli debug reset_network
python -m moxie_toolkit.cli debug restore_factory --png restore.png
python -m moxie_toolkit.cli list-commands

# Pairing QR (wifi + pairing secret + target cloud), byte-identical to the real phone app
python -m moxie_toolkit.cli pair --ssid HomeNet --password s3cret \
    --endpoint OPEN_MOXIE --secret-hex 00112233...  --png pair.png

# Wi-Fi-only, VPN push, and decode/inspect any code
python -m moxie_toolkit.cli wifi --ssid HomeNet --password s3cret
python -m moxie_toolkit.cli vpn VPN_ACTIVATE --url https://vpn/cfg --connect
python -m moxie_toolkit.cli decode 'PA0a07...'

# Look up any protocol message/enum from the 120 recovered protos
python -m moxie_toolkit.cli proto StartPairingQR
python -m moxie_toolkit.cli proto --grep mqtt

# Validate every encoder (round-trip + cross-check vs phone-side tool) — the test framework
python -m moxie_toolkit.cli validate
```

## What's here

| Module | What |
|---|---|
| `moxie_toolkit/qr_codec.py` | Encode/decode every QR `bo-wifi` accepts: `PA`+`StartPairingQR`, `VN`+`QRVPNConfig`, JSON `{wifi,pair,debug}`. Mirrors `QRData.ParseFromString`. |
| `moxie_toolkit/validate_qr.py` | Schema round-trip + byte-parity validation (27 checks). **This replaces acoustic QR brute-forcing.** |
| `moxie_toolkit/validate_protos.py` | Cross-validate the 120 recovered protos vs OpenMoxie's independent set (17 msgs/99 fields/19 enum values — perfect match). |
| `moxie_toolkit/cli.py` | `moxie-qr` CLI (gen / decode / validate / PNG-SVG). |
| `moxie_toolkit/markup.py` | Build `<mark name="cmd:…">` behavior markup (gestures, mood, audio) to weave into TTS. See [behavior-markup.md](../../docs/reverse-engineering/behavior-markup.md). |
| `secrets/emulate_secrets.py` | Recover factory secrets from `libsecrets.so` via Unicorn emulation (algorithm = XOR with package name). See [factory-provisioning.md](../../docs/reverse-engineering/factory-provisioning.md). |
| `moxie_toolkit/cloud.py` | MQTT transport helpers: topic builders, the `/commands/zmq` `name:bytes` framing, event/command envelope parse/build, the full **telehealth remote-puppet** session (`telehealth_session()`, `telehealth_play_output()`, `telehealth_command()`, `telehealth_topic()`, `parse_telehealth_event()` — see [telehealth.md](../../docs/reverse-engineering/telehealth.md)), and **`service_configuration()`** (repoint a robot: host/port/disable_verify), plus the **config/telemetry data-model** helpers `build_robot_cloud_config()`/`parse_robot_cloud_config()` (the `/config` document), `parse_robot_status()`, `parse_telemetry_packet()`, `parse_cloud_status()` (see [device-config-and-telemetry.md](../../docs/reverse-engineering/device-config-and-telemetry.md)), and the **RemoteChat** brain-reply helpers `remote_chat_reply()`/`remote_chat_action()`/`parse_remote_chat_request()`/`parse_remote_chat_response()` (see [remote-chat-protocol.md](../../docs/reverse-engineering/remote-chat-protocol.md)), plus the **offline brain-state** parsers `parse_fallback_info()`/`parse_cs_data()`/`parse_user_recommendation_data()` (see [offline-and-brain-state.md](../../docs/reverse-engineering/offline-and-brain-state.md)). See [cloud-protocol.md](../../docs/reverse-engineering/cloud-protocol.md). |
| `moxie_toolkit/bus.py` | **MoxieBus** — the on-device ZMQ bus client (drive face/motors/LEDs, read sensors); `fused_people_classes()` for the [perception-fusion](../../docs/reverse-engineering/perception-fusion.md) world-model of people, `user_alarm()`/`time_zone_info()` for [wake alarms + timezone](../../docs/reverse-engineering/power-and-system-events.md), and `attention_classes()` for the [published attention state](../../docs/reverse-engineering/gaze-and-attention.md). `pip install pyzmq`; tunnel via `adb forward`. |
| `moxie_toolkit/protoref.py` | Query the protocol: `python -m moxie_toolkit.cli proto QRCommand` / `proto --grep endpoint` / `proto --list` (382 msgs, 84 enums). |
| `moxie_toolkit/embodied/…` | Generated Python bindings for all 120 recovered protos. |
| `proto/embodied/…` | The `.proto` sources (copy of `docs/reverse-engineering/recovered-proto/`). |

Regenerate bindings after editing protos:

```sh
python -m grpc_tools.protoc --proto_path=proto --python_out=moxie_toolkit $(find proto -name '*.proto')
```

## The QR grammar (summary)

| Form | Prefix | Payload | Use |
|---|---|---|---|
| Pairing | `PA` | Base64(`StartPairingQR`) | wifi + secret + `endpoint` (which cloud to home to) |
| VPN | `VN` | Base64(`QRVPNConfig`) | push/activate a VPN profile |
| JSON | — | `{"wifi":…}` / `{"pair":…}` / `{"debug":{command,param}}` | wifi creds / legacy pair / **debug/factory command** |

`debug` commands handled by `bo-wifi`: `serial_number_display`, `restore_factory`, `reset_network`,
`bluetooth_pair`; anything else (e.g. `endpoint_update`) is forwarded to the brain over ZMQ. Full map:
[`../../docs/reverse-engineering/qr-commands.md`](../../docs/reverse-engineering/qr-commands.md).

> ⚠️ **Honest scope.** Whether a *stuck pre-801* robot will scan and act on these depends on it
> entering the Wifi App's QR mode on boot (it should, when it can't reach its dead cloud) and on the
> target endpoint being reachable. Re-homing/​reset/​re-pair via QR is the plausible no-open fix;
> flashing *new firmware* onto old units may still require opening the shell (see
> [`../../docs/reverse-engineering/ota-and-recovery.md`](../../docs/reverse-engineering/ota-and-recovery.md)).
> These generators are validated against the firmware's parser; end-to-end hardware validation is tracked separately.

---
📖 [Reverse-engineering](../../docs/reverse-engineering/README.md) · [QR commands](../../docs/reverse-engineering/qr-commands.md)
