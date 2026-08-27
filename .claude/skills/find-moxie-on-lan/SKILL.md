---
name: find-moxie-on-lan
description: Find a Moxie robot's IP address on the local network after it has joined Wi-Fi. Use when you need to confirm Moxie connected or locate it for the next setup step.
---

# Find Moxie on the LAN

Moxie's Wi-Fi module is made by **AMPAK**, and it's a locked-down Android client, so it has a
recognizable fingerprint.

## Scan
```bash
sudo arp-scan --interface=<iface> --localnet
```
Look for a host whose vendor is **AMPAK Technology** (MAC prefix often `d4:12:43:…`).

## Confirm it's Moxie
- Vendor: **AMPAK** (Wi-Fi/BT module).
- `ping -c1 <ip>` → **TTL 64** (Android/Linux).
- **No listening TCP ports** — it only makes outbound connections.
- Appears right after pairing; disappears when powered off.

If unsure, check the router's attached-devices list (often `http://192.168.1.1`) for the AMPAK
device, or power-cycle Moxie and watch which host drops and returns.

## Reference
- `docs/guides/find-moxie-on-lan.md`
