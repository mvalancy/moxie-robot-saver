# 🐛 Debugging

Running logs from real revival attempts on physical hardware — written so the next person's agent can
move fast and not re-derive facts that already cost real time. **Bold = load-bearing fact.**

- [`live-hardware-debug.md`](live-hardware-debug.md) — chronological log of an actual revival attempt:
  confirmed MACs, the wifi-only vs. pairing-key QR distinction, "Moxie Direct" AP setup, and **the wall**
  — a pre-801 robot reads the endpoint (`om`) QR but never opens a socket, because old firmware is pinned
  to Google Cloud IoT Core with hostname-checked TLS. Explains the 801/803 firmware gate.
- [`qr-command-findings.md`](qr-command-findings.md) — **auto-generated** discovery log from
  [`tools/qr-rig`](../../tools/qr-rig), ranking candidate QR command codes by how consistently the robot
  *pauses* after scanning them (a pause = the firmware acted). **Superseded by decompilation** (see note
  below): kept as the historical empirical record; it is no longer a live search.

## How the QR-command hunt worked (historical — now closed)
> ⚠️ **The QR grammar is provably closed.** The black-box rig below predates our Ghidra decompilation of
> the scanner. [`qr-commands.md`](../reverse-engineering/protocol/qr-commands.md) now shows
> `QRData.ParseFromString` is a single function with a **closed set** — `PA`/`VN`/Wi-Fi forms plus
> **exactly four** `debug.command` literals (`report`, `endpoint_update`, `om`, clear-reset-user-flag).
> There are **no hidden/factory commands left to discover**, so the empirical hunt is retired; this
> section documents how it worked, and the log stays as the record of what confirmed the closed set.

The rig cycles candidate command QRs on a monitor, the robot's camera scans them, and a microphone
listens to the scan beep. Normal re-scan cadence is rapid (~2s between beeps); a **long gap before the
next beep = the robot did something**. Order is randomized and every scan is recorded per-command, so a
single accidental gap is noise — only consistent reactors are meaningful. See the rig's own
[README](../../tools/qr-rig/README.md) for the closed-loop camera+beep design and the multi-phase
(primer → payload) sequence support.

> ⚠️ Context: QR *relocation* is confirmed impossible on **pre-801** firmware (see the endpoint wall in
> `live-hardware-debug.md`); the real fix for pre-801 remains a Rockchip reflash to 803.

---
📖 [Docs index](../README.md) · [Back to top](../../README.md)
