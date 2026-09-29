# 🔑 `tools/pairing` — pairing-QR codec + CLI

Clean-room reconstruction of the Moxie parent app's pairing-QR format (`"PA"`+protobuf and the legacy
JSON mode), plus the endpoint-config QR that repoints a firmware-801/803 robot to your own broker.
Round-trip tested and proven against real hardware.

## Files
- [`moxie_qr.py`](moxie_qr.py) — the codec: `encode_proto` / `decode_proto` / `encode_json`, plus a round-trip self-test.
- [`moxie_pair.py`](moxie_pair.py) — CLI that builds a pairing QR and renders it to a PNG and the terminal.
- [`moxie_endpoint_qr.py`](moxie_endpoint_qr.py) — the endpoint-config "QR #2" a fw-801/803 robot waits for after Wi-Fi, repointing it to your MQTT broker (JSON, verified against OpenMoxie's `ServiceConfiguration2`).

## Run
```bash
python moxie_pair.py --ssid HomeWiFi --password 's3cr3t' --band 24g --out qr.png   # needs segno
python moxie_qr.py                                                                 # round-trip self-test
```
`moxie_pair.py` options: `--mode proto|json`, `--band any|5g|24g`, `--hidden`, `--iot-endpoint N`,
`--secret-key-hex <32-byte hex>` (else a random Ed25519 seed is generated and printed),
`--user-token <tok>` (json mode).

Full wire spec: [`phone/qr-format.md`](../../docs/reverse-engineering/phone/qr-format.md).

---
📖 [tools](../README.md) · [Back to top](../../README.md)
