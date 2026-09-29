---
name: generate-pairing-qr
description: Generate a Moxie Wi-Fi pairing QR code (the "PA"+protobuf code you hold up to the robot's camera). Use when someone wants to pair a Moxie to Wi-Fi, either from the command line or the local server.
---

# Generate a Moxie pairing QR

Moxie scans a QR code to receive Wi-Fi credentials plus a pairing seed. Two ways to make one.

## Option A — CLI (no server needed)
```bash
python tools/pairing/moxie_pair.py \
    --ssid "<WIFI_NAME>" --password "<WIFI_PASSWORD>" \
    --band 24g --out qr.png
```
- `--band 24g` is recommended (Moxie prefers 2.4 GHz); `any` or `5g` if needed. `--hidden` for a hidden SSID.
- A random 32-byte Ed25519 seed is generated and printed; `--secret-key-hex <64 hex chars>` supplies your
  own (it must match what your server registered).
- `--mode json` emits the legacy JSON format instead of `"PA"`+protobuf.
- The QR is written to `qr.png` and printed to the terminal as ASCII (`--no-ascii` to suppress).

## Option B — local server + phone (recommended for owners)
```bash
python server/run.py          # or `docker compose up` for the full stack; then open http://<ip>:8080 on a phone
```
In the web app: enter Wi-Fi → **Generate pairing QR**. The server registers the pairing and shows a
recovery phrase to save.

## Then
Hold the QR to Moxie's camera while it is on its setup/QR screen. It acknowledges the scan and joins Wi-Fi.
Find it with the `find-moxie-on-lan` skill. On firmware 801+, a second QR re-homes it to your broker:
`python tools/pairing/moxie_endpoint_qr.py <broker-host>`.

## Reference
- Wire format: `docs/reverse-engineering/phone/qr-format.md`
- Codec self-test: `python tools/pairing/moxie_qr.py`
