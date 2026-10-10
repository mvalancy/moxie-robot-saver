# QR command rig: findings (closed 2026-08-27)

For anyone wondering whether a hidden factory QR command exists: no. This page is the record of what
the [camera rig](../../tools/qr-rig/README.md) measured on one pre-801 robot in August 2026, and why
the search closed. The raw tables are in
[`tools/qr-rig/findings-2026-08-27.md`](../../tools/qr-rig/findings-2026-08-27.md).

> ✅ **CONCLUDED — the QR grammar is closed, read directly from the binary.** `QRData.ParseFromString`
> in `bo-wifi` has exactly three branches (`PA` pairing / `VN` VPN / JSON `{wifi,pair,debug}`), and
> `debug.command` matches exactly four literals (`serial_number_display`, `restore_factory`,
> `reset_network`, `bluetooth_pair`) with a literal `else → QRDiagnostic` for everything else. **There is
> no undocumented factory command the setup app acts on** — the set is provably these four.
>
> **The "reactors" below are dwell timers, not hidden handlers.** The gaps this rig measured map onto the
> app's own state waits: unknown command → 2 s diagnostic screen (`QR_DIAGNOSTIC_WAIT_DURATION=2f`); a
> Wi-Fi/`reset_network` path → 20 s connect wait (`CONNECTION_WAIT_DURATION=20f`); serial display → 30 s.
> The uniform ~20 s "consistent reactors" are the *known* Wi-Fi-connect state, not new commands. The rig
> has served its purpose and is **retired**; the only remaining open edge is the *native* consumer of the
> string-keyed `QRCommand` bus message (the managed brain has zero references to it). See the source of
> truth: [`../reverse-engineering/protocol/qr-commands.md`](../reverse-engineering/protocol/qr-commands.md).

## Method

Each candidate was a QR the robot scanned; a microphone heard its scan beep and the rig measured the
**scan→resume gap**. The normal re-scan cadence is ~2s; a consistently **longer gap meant the robot
did something** with that command. One long gap is noise; only commands that reacted across **many**
randomized repeats counted.

- **Commands scanned at least 3×:** 16  ·  distinct scanned: 107
- **Reaction threshold:** gap ≥ 4.5s  ·  **consistent =** ≥3 scans and ≥50% reaction rate
- The robot was **pre-801** (Google-IoT firmware), so QR re-homing was already ruled out
  ([`live-hardware-debug.md`](live-hardware-debug.md)). This was a search for anything else the firmware
  still answered to.

## What the timings mean

The measured gaps fall into four classes.

| Gap | What it is |
|---|---|
| ~2 s | The unknown-command diagnostic screen (`QR_DIAGNOSTIC_WAIT_DURATION=2f`): `attestation` and `kiosk` as `dbg.code`, the `nest[…]` sub-command shapes, top-level `ca` (1.41 s mean). |
| ~20 s | The Wi-Fi connect wait (`CONNECTION_WAIT_DURATION=20f`); the robot tried to join a network. `nest[mfg.setserial]` (20.35 s), `seq[mfg_mode>setserial]` (20.22 s), `audio_test` as a top-level command (20.01 s mean, n=3), and every other "consistent reactor". |
| 34–44 s | The raw strings `rockchip` (44.38 s mean, n=2), `mfg_mode` (41.12 s, n=1) and `eng` (34.58 s, n=2). The three known waits (2 s, 20 s, 30 s) do not account for them. An open residue, not evidence of a handler; it belongs on the [exploration map](../reverse-engineering/EXPLORATION-MAP.md#open-items-need-a-bench-unit-or-an-external-artifact). |
| <1 s, once | `seq[eng>factory_reset]` at 0.77 s (n=1): below the re-scan cadence, unexplained. |

The full record (119 rows: every command, its JSON shape, shows, reacts, rate, mean and max gap) is the
raw snapshot [`findings-2026-08-27.md`](../../tools/qr-rig/findings-2026-08-27.md) in the rig's folder.
The rig now writes `tools/qr-rig/findings.md` when it runs; this page stays the record.

---
📖 [Bench notes](README.md) · [QR commands](../reverse-engineering/protocol/qr-commands.md) · [QR rig](../../tools/qr-rig/README.md) · [Docs index](../README.md)
