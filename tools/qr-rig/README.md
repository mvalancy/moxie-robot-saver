# 🟢 `tools/qr-rig` — camera QR validation rig

Hardware-in-the-loop rig: it shows QR codes on a monitor to a Moxie sitting in front of it, and uses
Moxie's scan beeps (heard over a mic) to pace itself and detect reactions. Its job now is end-to-end
**validation** — display codes we know the firmware parses (from [`../robot-toolkit`](../robot-toolkit),
proven by schema round-trip + byte-parity) and watch the real reaction. The original blind
brute-force sweep is deprecated but kept, because the closed-loop camera-plus-beep-timing approach is
reusable.

**Scope:** pre-801 units can't be relocated by QR (endpoint pinned to `mqtt.googleapis.com` with
hostname-checked TLS); the real fix there is a Rockchip reflash to 803. QR grammar:
[`protocol/qr-commands.md`](../../docs/reverse-engineering/protocol/qr-commands.md). Origin:
[`live-hardware-debug.md`](../../docs/debugging/live-hardware-debug.md).

## Files
- [`qr_rig.py`](qr_rig.py) — the whole rig: HTTP server, fullscreen `/display`, phone `/`, mic listener, fuzzer. Self-contained.
- [`validated_codes.py`](validated_codes.py) — render the validated toolkit codes to PNGs for display (`--out ./deck`).
- [`overnight.py`](overnight.py) — keeps `qr_rig.py` alive headless and snapshots stats into `docs/debugging/qr-command-findings.md`.

## Run
```bash
pip install segno numpy                                   # + a working mic and chrome/chromium
DISPLAY=:1 python3 qr_rig.py --autofuzz --moxie-ip <robot-ip>
```
Open the control UI at `http://<host>:8091/`, position Moxie so its camera locks the bottom-right QR,
let it sweep, and check `/stats` (ranks commands by reaction rate; needs >=3 scans and >=50%).

## Modes and operational notes
- `--autofuzz` (brute): sweep the full candidate list, reshuffled each pass. `--maybes-file f.json` (retest): hammer only flagged candidates. `--focus`: re-confirm overnight PAUSE-producers, including two-frame primer->config and nested single-frame sub-commands. Switch live via the UI or `POST {"mode":"focus"}`.
- Beep pacing: a mic listener (`arecord` + numpy RMS) detects the ~480 Hz scan beep. The QR is never swapped while Moxie is silent — each frame waits for the first scan beep, the final frame holds through any post-scan pause until the steady rapid-scan cadence returns (45s cap). This pins a pause to the code that caused it and kills false positives.
- Runtime artifacts (config, logs) are written outside the repo; nothing here is committed at runtime.

---
📖 [tools](../README.md) · [Back to top](../../README.md)
