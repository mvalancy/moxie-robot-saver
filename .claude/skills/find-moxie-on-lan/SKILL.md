---
name: find-moxie-on-lan
description: Find a Moxie robot's IP address on the local network after it has joined Wi-Fi. Use when you need to confirm Moxie connected or locate it for the next setup step.
---

# Find Moxie on the LAN

Moxie's Wi-Fi module is an **AMPAK** AP6335 (Broadcom BCM4339 inside), and the robot is a locked-down
Android client, so it has a recognizable fingerprint.

## Scan
```bash
sudo arp-scan --interface=<iface> --localnet
```
Look for a host whose vendor is **AMPAK Technology** (MAC prefix often `d4:12:43:…`).

## Confirm it's Moxie
- Vendor: **AMPAK**.
- `ping -c1 <ip>` → **TTL 64** (Android/Linux).
- **No listening TCP ports** — it only makes outbound connections.
- Appears right after pairing; disappears when powered off.

If unsure, check the router's attached-devices list for the AMPAK device, or power-cycle Moxie and watch
which host drops and returns. Once it is re-homed to your broker, the console's fleet view shows it too.

## Reference
- `docs/guides/find-moxie-on-lan.md`
