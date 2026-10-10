# Find your Moxie on the LAN

For owners setting up a robot. After Moxie scans the Wi-Fi code and joins your network, here's how to
find its IP address.

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
- Appears right after it scans the Wi-Fi code; disappears if you power it off.

```bash
ping -c1 <ip>                 # TTL=64
# a quick port check should show nothing listening
```

Still unsure? Check your router's attached-devices list (its admin page; the address is usually on
a label on the router) for the AMPAK device, or power-cycle Moxie and watch which host drops and
returns.

## Worked example
On one setup, `arp-scan` listed a single AMPAK host with a `d4:12:43:xx:xx:xx` address. It answered
ping with TTL 64, had zero open ports, and appeared right after the robot scanned the Wi-Fi code.
That was Moxie.

---
📖 [Guides index](README.md) · [Revive your Moxie](revive-your-moxie.md) · [Back to top](../../README.md)
