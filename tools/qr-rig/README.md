# 🟢 `tools/qr-rig` — camera QR validation rig

For anyone with a real Moxie on the bench who wants to watch it react to a code we generate. The rig
shows QR codes on a monitor to a robot sitting in front of it and listens for its scan beeps over a
mic, so every reaction is pinned to the code that caused it. Its job is end-to-end **validation** of
codes the firmware is known to parse; the blind brute-force sweep it began as is retired.

**Scope:** a pre-801 robot cannot be re-homed by any QR (its endpoint is pinned to
`mqtt.googleapis.com` with CA-validated TLS); it needs new firmware first
([Revive your Moxie, Path C](../../docs/guides/revive-your-moxie.md#path-c-flash-an-older-robot-first)).
The grammar every code here follows: [`protocol/qr-commands.md`](../../docs/reverse-engineering/protocol/qr-commands.md).
Origin: [`live-hardware-debug.md`](../../docs/debugging/live-hardware-debug.md).

## Validate the known codes

```bash
pip install segno numpy                                   # + a working mic and chrome/chromium
python3 validated_codes.py --out ./deck                   # one PNG per known-good code
DISPLAY=:1 python3 qr_rig.py --moxie-ip <robot-ip>        # display, control UI and mic listener
```

1. **Render the deck.** `validated_codes.py` writes one PNG per code from
   [`../robot-toolkit`](../robot-toolkit/README.md): the `OPEN_MOXIE` and `EMBODIED_LOCAL` endpoint codes,
   the four debug commands and a Wi-Fi example, each proven by schema round-trip and byte-parity.
2. **Show the deck.** Open the control UI at `http://<host>:8091/`, position Moxie so its camera locks
   the bottom-right QR, and post a payload, or an `om` code built from `MOXIE_BROKER_HOST`.
3. **Record the reaction.** The beep pacing below tells you which code the robot answered and how long
   it dwelt on it.

## Files

- [`qr_rig.py`](qr_rig.py) — the whole rig: HTTP server, fullscreen `/display`, phone `/`, mic listener, fuzzer. Self-contained.
- [`validated_codes.py`](validated_codes.py) — render the validated toolkit codes to PNGs for display (`--out ./deck`).
- [`overnight.py`](overnight.py) — headless runner for the retired sweep: keeps `qr_rig.py` alive and snapshots its statistics into `findings.md` beside it.
- [`findings-2026-08-27.md`](findings-2026-08-27.md) — the one snapshot the sweep produced, kept verbatim; what it means is in [`qr-command-findings.md`](../../docs/debugging/qr-command-findings.md).

## Beep pacing

A mic listener (`arecord` + numpy RMS) detects the ~480 Hz scan beep. The QR is never swapped while
Moxie is silent: each frame waits for the first scan beep, and the final frame holds through any
post-scan pause until the steady rapid-scan cadence returns (45s cap). This pins a pause to the code
that caused it and kills false positives.

## What is written where

- `qr_rig.py` writes its config and timeline outside the repo, and `rig.log` beside itself (git-ignored).
- `overnight.py` writes `tools/qr-rig/findings.md` beside itself (untracked). Only with `--commit` does
  it also commit that file and push the current branch, every `--commit-every` seconds. By default
  nothing here is committed at runtime.
- `validated_codes.py` writes PNGs to `--out` (default `./deck`).

## Retired: the brute-force sweep

Decompiling the scanner closed the grammar: four debug commands in the setup app, three native codes,
and nothing hidden ([QR commands](../../docs/reverse-engineering/protocol/qr-commands.md)). The sweep
modes stay in `qr_rig.py` because the closed-loop camera-plus-beep-timing method is reusable:

- `--autofuzz` (brute): sweep the full candidate list, reshuffled each pass.
- `--maybes-file f.json` (retest): hammer only flagged candidates.
- `--focus`: re-confirm overnight PAUSE-producers, including two-frame primer->config and nested
  single-frame sub-commands. Switch live via the UI or `POST {"mode":"focus"}`.
- `/stats` ranks commands by reaction rate; needs >=3 scans and >=50%.
- `MOXIE_BROKER_HOST=<broker-ip> python3 overnight.py --moxie-ip <robot-ip>` keeps the sweep running
  headless until `--until HH:MM` (default `07:00`).

---
📖 [tools](../README.md) · [Back to top](../../README.md)
