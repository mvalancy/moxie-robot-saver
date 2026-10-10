# Debugging

Bench notes from reviving real robots, for the owner whose Moxie will not connect. The day-to-day
steps are in the [bench runbook](../guides/bench-runbook.md); the firmware gate and the paths through
it are in [Revive your Moxie](../guides/revive-your-moxie.md). These pages are the evidence behind them.

- [`live-hardware-debug.md`](live-hardware-debug.md) — one revival attempt on a real robot: the
  Wi-Fi-only-first rule, the "Moxie Direct" hotspot, and the wall a pre-801 robot hits (it reads the
  `om` code but talks only to Google Cloud IoT Core, whose certificate it validates).
- [`qr-command-findings.md`](qr-command-findings.md) — what the [`tools/qr-rig`](../../tools/qr-rig/README.md)
  camera rig measured while timing a robot's scan beeps; the search it served is closed, because the
  decompiled grammar has exactly four debug commands ([QR commands](../reverse-engineering/protocol/qr-commands.md)).
  The rig now writes `tools/qr-rig/findings.md`; this page is the record.

For a pre-801 robot the fix is new firmware, not a QR code:
[Revive your Moxie, Path C](../guides/revive-your-moxie.md#path-c-flash-an-older-robot-first).

---
📖 [Docs index](../README.md) · [Project README](../../README.md)
