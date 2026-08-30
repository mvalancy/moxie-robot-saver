---
name: using-the-moxie-toolkit
description: Use the moxie-robot toolkit programmatically — generate/decode the QR codes a stock robot scans, query the 120 recovered protobuf schemas, drive the on-device ZeroMQ bus, build cloud MQTT/REST messages, and emit behavior markup. Use when scripting against a Moxie robot or a revival server, or reusing the recovered protocol from Python.
---

# Using the Moxie toolkit

`tools/robot-toolkit/moxie_toolkit/` is the Python toolkit built on the recovered protobuf schemas — the
executable form of the reverse-engineering. `pip install protobuf segno pyzmq`.

## QR channel — the one input a stock robot acts on with no disassembly
The `bo-wifi` setup app scans QR codes; these emit exactly what it parses (schema round-trip + byte-parity
with the phone-side encoder):
```bash
python -m moxie_toolkit.cli endpoint OPEN_MOXIE --png redirect.png     # re-home to a community/self-host server
python -m moxie_toolkit.cli pair --ssid Net --password pw --endpoint EMBODIED_LOCAL --secret-hex 00.. --png pair.png
python -m moxie_toolkit.cli wifi --ssid Net --password pw             # Wi-Fi only
python -m moxie_toolkit.cli debug reset_network                       # the 4 factory debug commands
python -m moxie_toolkit.cli vpn VPN_ACTIVATE --url https://vpn/cfg --connect
python -m moxie_toolkit.cli decode 'PA0a07…'                          # inspect any scanned code
```
From Python: `from moxie_toolkit import qr_codec as qc` → `qc.encode_pairing/encode_endpoint_update/encode_debug/decode(...)`.
(Which endpoints/commands are valid + what they do: see `docs/reverse-engineering/protocol/qr-commands.md`.)

## Query the recovered protocol (382 messages / 84 enums)
```bash
python -m moxie_toolkit.cli proto StartPairingQR     # show a message's fields
python -m moxie_toolkit.cli proto --grep mqtt        # search messages/enums
python -m moxie_toolkit.cli proto --list             # list everything
```

## Drive the on-device ZeroMQ bus (`bus.py` → `MoxieBus`)
Two-frame framing (`descriptor FullName` + serialized protobuf) over the robot's internal broker. Helpers:
`bus.led(...)`, `bus.motor(...)`, `bus.power(...)` build `embodied.lizzerface` messages; `MoxieBus.send/subscribe/recv`
move them. Reach a device via `adb forward tcp:5678`/`6789`. (Contract: `protocol/robot-ipc-protocol.md`.)

## Build cloud messages (`cloud.py`)
The MQTT topic map + `/commands/zmq` injection framing a revival server needs:
`cloud.events_topic/state_topic/config_topic/command_topic/zmq_command_topic(device_id)`,
`cloud.encode_zmq_command(msg)` (the `"{full_name}:"+bytes` frame), `cloud.command_json(...)`. Pairs with
the server in `server/` + `mqtt/`. (Contract: `protocol/cloud-protocol.md`.)

## Emit behavior markup (`markup.py`)
Inline `<mark cmd:…>` tags into TTS so the robot moves/emotes while speaking:
`markup.behaviour_tree(...)`, `playback_mood(...)`, `icons(...)`, `playaudio(channel=markup.CHANNEL_FX,…)`,
`vocal_gesture("laugh")` (52 in `markup.VOCAL_GESTURES`), `say_as("1/2","fraction")`, plus SSML `usel/brk`.
(Catalog: `runtime/behavior-markup.md`.)

## Secrets extractor
`tools/robot-toolkit/secrets/` — the Unicorn-based extractor that recovers `libsecrets.so`'s XOR-obfuscated
factory creds (see `firmware/factory-provisioning.md`).

All of it is round-trip tested: `python tools/robot-toolkit/run_tests.py` (12 tests).
