# 🧰 Moxie robot toolkit

Python tools that speak to a Moxie the way its own firmware does, built on the
[recovered protobuf schemas](../../docs/reverse-engineering/protocol/recovered-proto/). The headline
is the **QR channel**: the one input a stock robot acts on with no disassembly and no account. Every
generator emits codes the robot's `bo-wifi` setup app parses, checked by schema round-trip and
byte-parity with the independently recovered phone-side encoder.

## Install and run

```sh
pip install protobuf segno pyzmq        # segno = QR images, pyzmq = on-device bus client
cd tools/robot-toolkit

python -m moxie_toolkit.cli endpoint OPEN_MOXIE --png redirect.png     # re-home to a community server
python -m moxie_toolkit.cli debug reset_network                        # factory debug command
python -m moxie_toolkit.cli pair --ssid HomeNet --password s3cret \
    --endpoint OPEN_MOXIE --secret-hex 00112233... --png pair.png        # pairing QR (phone-identical)
python -m moxie_toolkit.cli wifi --ssid HomeNet --password s3cret
python -m moxie_toolkit.cli vpn VPN_ACTIVATE --url https://vpn/cfg --connect
python -m moxie_toolkit.cli decode 'PA0a07...'                         # inspect any code
python -m moxie_toolkit.cli list-commands
python -m moxie_toolkit.cli proto StartPairingQR                       # look up a message/enum
python -m moxie_toolkit.cli validate                                   # 27 QR encoder checks
python3 run_tests.py                                                   # every test_*.py (CI runs this)
```

## Files

| Path | What |
|---|---|
| [`moxie_toolkit/`](moxie_toolkit/README.md) | The importable package: QR codec, validators, proto lookup, MQTT/ZMQ helpers, CLI. |
| [`proto/`](proto/README.md) | The `.proto` sources (a copy of the recovered protos, 120 files). |
| [`secrets/`](secrets/README.md) | `libsecrets.so` factory-secret extractor (Unicorn emulation). |
| [`run_tests.py`](run_tests.py) | Runs every `test_*.py` here; each skips cleanly when `protobuf` is missing. |
| [`gen_catalog.py`](gen_catalog.py) | Regenerates [`proto-catalog.md`](../../docs/reverse-engineering/protocol/proto-catalog.md) from `proto/` (needs `protoc`). |
| `test_*.py` | Wire tests of our builders, parsers and recovered enum/field tables, one per protocol area: `attention`, `config_telemetry`, `fusion`, `mainapp`, `mpu_handling`, `offline_state`, `remote_chat`, `runtime_control`, `sel_taxonomy`, `telehealth`, `time_alarms`, `wifiapp_status`. Each cites its RE doc in its header. |
| [`_harness.py`](_harness.py) | Shared `ok`/`rt`/`report` helpers for the `test_*.py` scripts. |

Regenerate the Python bindings after editing protos:

```sh
python -m grpc_tools.protoc --proto_path=proto --python_out=moxie_toolkit $(find proto -name '*.proto')
```

## The QR grammar (summary)

| Form | Prefix | Payload | Use |
|---|---|---|---|
| Pairing | `PA` | Base64(`StartPairingQR`) | Wi-Fi + secret + `endpoint` (which cloud to home to) |
| VPN | `VN` | Base64(`QRVPNConfig`) | Push or activate a VPN profile |
| JSON | none | `{"wifi":…}` / `{"pair":…}` / `{"debug":{command,param}}` | Wi-Fi creds, legacy pair, debug/factory command |

`bo-wifi` handles four `debug` commands: `serial_number_display`, `restore_factory`, `reset_network`,
`bluetooth_pair`. Anything else (for example `endpoint_update`) is forwarded to the brain over ZMQ.
Full map: [`protocol/qr-commands.md`](../../docs/reverse-engineering/protocol/qr-commands.md).

**Scope.** Whether a stuck pre-801 robot acts on these depends on it entering QR mode on boot (it
should when it can't reach its dead cloud) and on the target endpoint being reachable. Flashing new
firmware onto old units may still need the shell opened
([`firmware/ota-and-recovery.md`](../../docs/reverse-engineering/firmware/ota-and-recovery.md)). The
generators are validated against the firmware's parser, not yet end-to-end on hardware.

---
📖 [tools](../README.md) · [Reverse-engineering](../../docs/reverse-engineering/README.md) · [Back to top](../../README.md)
