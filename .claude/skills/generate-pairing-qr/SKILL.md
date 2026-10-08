---
name: generate-pairing-qr
description: Generate a Moxie Wi-Fi pairing QR code (the "PA"+protobuf code you hold up to the robot's camera). Use when someone wants to pair a Moxie to Wi-Fi, either from the command line or the local server.
---

# Generate a Moxie pairing QR

Moxie scans a QR code to join Wi-Fi. There are two kinds: the **Wi-Fi-only** code (network name and
password, `StartPairingQR.wifi_only`), which is the first code for a robot coming to your own server,
and the original app's **pairing-key** code, which also carries a pairing key and sends a re-homed
robot looking for the dead cloud (`docs/debugging/live-hardware-debug.md`). Two ways to make one.

## Option A — CLI (no server needed)
```bash
python tools/pairing/moxie_pair.py \
    --ssid "<WIFI_NAME>" --password "<WIFI_PASSWORD>" \
    --band 24g --out qr.png
```
- **This makes a pairing-key code. For a re-home use the Wi-Fi-only code instead** (Option B, Moxie
  Direct, or the snippet below). `--hide-pair` drops the key but still writes field 8 (`iot_endpoint`),
  so its bytes are not the `encode_wifi_only` bytes a real robot has joined Wi-Fi with.
- `--band 24g` is recommended (Moxie prefers 2.4 GHz); `any` or `5g` if needed. `--hidden` for a hidden SSID.
- A random 32-byte Ed25519 seed is generated and printed; `--secret-key-hex <64 hex chars>` supplies your
  own (it must match what your server registered).
- `--mode json` emits the legacy JSON format instead of `"PA"`+protobuf.
- The QR is written to `qr.png` and printed to the terminal as ASCII (`--no-ascii` to suppress).

The Wi-Fi-only code from the command line (`moxie_qr.encode_wifi_only`, the bytes the console's Wi-Fi
tab and Moxie Direct serve):
```bash
python -c 'import sys; sys.path.insert(0, "tools/pairing"); import moxie_qr as q, segno
code = q.encode_wifi_only(q.WifiInfo("<WIFI_NAME>", "<WIFI_PASSWORD>", band=q.Band.ONLY_24G))
segno.make(code, error="l").save("wifi-only.png", scale=10, border=4); print(code)'
```

## Option B — local server + phone (recommended for owners)
```bash
python server/run.py          # or `docker compose up` for the full stack; then open http://<ip>:8080 on a phone
```
In the web app's **📶 Wi-Fi** tab: enter Wi-Fi → **Make the Wi-Fi code**. By default that is the
**Wi-Fi-only** code (no pairing key), the right first code for a robot coming to your server: a pairing
key sends the robot looking for the dead cloud (`docs/debugging/live-hardware-debug.md`). Ticking *Put a
pairing key in the code* makes the original app's pairing-key code instead; the server registers the
pairing and shows a recovery phrase to save.

## Then
Hold the QR to Moxie's camera while it is on its setup/QR screen. It acknowledges the scan and joins Wi-Fi.
Find it with the `find-moxie-on-lan` skill. On firmware 801+, a second QR re-homes it to your broker:
`python tools/pairing/moxie_endpoint_qr.py <broker-host>`. Once it reaches the broker, add it in the
console: **🤖 Moxie → Add to my account** (`docs/guides/bench-runbook.md`).

## Reference
- Wire format: `docs/reverse-engineering/phone/qr-format.md`
- Codec self-test: `python tools/pairing/moxie_qr.py`
