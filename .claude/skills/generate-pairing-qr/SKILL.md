---
name: generate-pairing-qr
description: Generate a Moxie Wi-Fi pairing QR code (the "PA"+protobuf code you hold up to the robot's camera). Use when someone wants to pair a Moxie to Wi-Fi, either from the command line or the local server.
---

# Generate a Moxie pairing QR

The Moxie robot scans a QR code to receive Wi-Fi credentials + a pairing seed. Two ways to make one:

## Option A — CLI (no server needed)
```bash
python tools/pairing/moxie_pair.py \
    --ssid "<WIFI_NAME>" --password "<WIFI_PASSWORD>" \
    --band 24g --out qr.png
```
- `--band 24g` is recommended (Moxie prefers 2.4 GHz). Use `any` or `5g` if needed.
- Add `--hidden` for a hidden SSID.
- A random Ed25519 pairing seed is generated and printed; pass `--secret-key-hex <64 hex chars>` to
  supply your own (must match what your server registered).
- The QR is written to `qr.png` **and** printed to the terminal as ASCII.

## Option B — local server + phone (recommended for owners)
```bash
python server/run.py                      # then open http://<ip>:8080 on a phone
```
In the web app: enter Wi-Fi → **Generate pairing QR**. The server also registers the pairing and
shows a recovery phrase to save.

## Then
Put Moxie in pairing mode and hold the QR to its camera. It should acknowledge the scan and join
Wi-Fi. Find it afterward with the `find-moxie-on-lan` skill.

## Reference
- Wire format: `docs/reverse-engineering/qr-format.md`
- Codec self-test: `python tools/pairing/moxie_qr.py`
