# 🐛 Debugging

Running logs from real revival attempts on physical hardware — written so the next person's agent can
move fast and not re-derive facts that already cost real time. **Bold = load-bearing fact.**

- [`live-hardware-debug.md`](live-hardware-debug.md) — chronological log of an actual revival attempt:
  confirmed MACs, the wifi-only vs. pairing-key QR distinction, "Moxie Direct" AP setup, and **the wall**
  — a pre-801 robot reads the endpoint (`om`) QR but never opens a socket, because old firmware is pinned
  to Google Cloud IoT Core with hostname-checked TLS. Explains the 801/803 firmware gate.
- [`qr-command-findings.md`](qr-command-findings.md) — **auto-generated** discovery log from
  [`tools/qr-rig`](../../tools/qr-rig). Ranks candidate QR command codes by how consistently the robot
  *pauses* after scanning them (a pause = the firmware acted). The git history of this file is the record
  of what's been tried; only commands that react across many randomized repeats count.

## How the QR-command hunt works
The rig cycles candidate command QRs on a monitor, the robot's camera scans them, and a microphone
listens to the scan beep. Normal re-scan cadence is rapid (~2s between beeps); a **long gap before the
next beep = the robot did something**. Order is randomized and every scan is recorded per-command, so a
single accidental gap is noise — only consistent reactors are meaningful. See the rig's own
[README](../../tools/qr-rig/README.md) for the closed-loop camera+beep design and the multi-phase
(primer → payload) sequence support.

> ⚠️ Context: QR *relocation* is confirmed impossible on **pre-801** firmware (see the endpoint wall in
> `live-hardware-debug.md`). This hunt is pure discovery of any hidden/factory command the firmware still
> responds to; the real fix for pre-801 remains a Rockchip reflash to 803.

---
📖 [Docs index](../README.md) · [Back to top](../../README.md)
