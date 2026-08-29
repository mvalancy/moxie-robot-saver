# Guide: find your Moxie on the LAN

After Moxie joins your Wi-Fi (via the pairing QR), here's how to find its IP address.

## Quick method — ARP scan by vendor
Moxie's Wi-Fi module is made by **AMPAK**, so it stands out in an ARP scan:

```bash
sudo arp-scan --interface=<iface> --localnet
```

Look for a host whose vendor is **AMPAK Technology** (MAC prefix like `d4:12:43:…`). That's almost
certainly Moxie.

## Confirming it's Moxie
Moxie is a locked-down Android **client**, so it has a recognizable fingerprint:
- **Vendor:** AMPAK (Wi-Fi/BT module).
- **TTL 64** (Android/Linux) in a ping reply.
- **No listening TCP ports** — it makes outbound connections only; it doesn't run servers.
- Appears right after you pair it; disappears if you power it off.

```bash
ping -c1 <ip>                 # TTL=64
# a quick port check should show nothing listening
```

Still unsure? Check your router's attached-devices list (often `http://192.168.1.1`) for the AMPAK
device, or power-cycle Moxie and watch which host drops and returns.

## Worked example
On one setup, `arp-scan` showed `192.168.1.48  d4:12:43:22:31:d8  AMPAK Technology` — TTL 64, zero
open ports, freshly present after pairing. That was Moxie.
