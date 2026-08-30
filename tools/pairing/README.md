# 🔑 `tools/pairing` — pairing-QR codec + CLI

Clean-room reconstruction of the Moxie parent app's pairing-QR format (`"PA"`+protobuf and the legacy
JSON mode). Round-trip tested; this is what proved correct against **real hardware**.

## Files
- `moxie_qr.py` — the codec: `encode_proto` / `decode_proto` / `encode_json`, plus a self-test.
- `moxie_pair.py` — CLI that builds a QR and renders it to a PNG **and** the terminal.

## Generate a pairing QR
```bash
python moxie_pair.py --ssid HomeWiFi --password 's3cr3t' --band 24g --out qr.png
```
Options: `--mode proto|json`, `--band any|5g|24g`, `--hidden`, `--iot-endpoint N`,
`--secret-key-hex <32-byte hex>` (else a random Ed25519 seed is generated and printed),
`--user-token <tok>` (json mode). Requires `segno` (`pip install segno`).

## Verify a QR
```bash
python moxie_qr.py            # runs the round-trip self-test
```

## Format
Full wire spec: [`../../docs/reverse-engineering/qr-format.md`](../../docs/reverse-engineering/phone/qr-format.md).

---
📖 [Back to top](../../README.md) · [QR format spec →](../../docs/reverse-engineering/phone/qr-format.md)
