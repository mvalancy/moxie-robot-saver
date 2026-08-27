# 🟢 `tools/qr-rig` — camera QR brute-force rig

A hardware-in-the-loop rig for probing a Moxie that sits in front of a monitor. It cycles candidate
**QR command codes** on a fullscreen display, Moxie's camera scans them, and the rig **listens to
Moxie's beeps over a microphone** to pace itself and to detect reactions — all logged statistically.

> Built during a live attempt to revive a **pre-801** Moxie (see
> [`../../docs/debugging/live-hardware-debug.md`](../../docs/debugging/live-hardware-debug.md)).
> **Honest scope:** the research confirmed pre-801 units can't be relocated by QR (endpoint is pinned
> to `mqtt.googleapis.com` with hostname-checked TLS), so this is an *exhaustive long-shot* to find any
> hidden/factory QR command the firmware acts on. The real fix for pre-801 is a Rockchip reflash to
> 803. This rig is kept because the approach — closed-loop camera + beep timing — is reusable.

## How it works
- **Display** (`/display`, shown fullscreen via `chrome --kiosk` on the monitor): a large QR
  (dark-on-light so Moxie scans it) framed bottom-right, a big **SPACE-to-FLAG** bar, and the command
  under test in large text. Press **space** (or click) when Moxie reacts to hand-flag a candidate.
- **Control** (`/`, open on your phone): sliders for QR size/position (persisted to a config file),
  Start/Stop the sweep, **retest maybes**, and the live maybe/stats list.
- **Beep pacing:** a mic listener (`arecord` + numpy RMS) detects Moxie's ~480 Hz scan beep. The rig
  advances the moment Moxie is ready (fast) and measures the **scan→resume gap**; an abnormally long
  gap = a reaction.
- **Statistics:** order is randomized and every scanned show is recorded per-command. `/stats` ranks
  commands by **reaction rate** (needs ≥3 scans, ≥50% rate) so a single accidental gap never counts.

## Run
```bash
pip install segno numpy            # + a working mic and chrome/chromium
DISPLAY=:1 python3 qr_rig.py --autofuzz --moxie-ip <robot-ip>
```
Open the control UI at `http://<host>:8091/`. Position Moxie in front of the monitor; nudge the QR to
where Moxie scans it (bottom-right, sized so it locks focus). Let it sweep; check `/stats`.

## Files
- `qr_rig.py` — the whole rig (HTTP server + display + control + audio listener + fuzzer). Self-contained.

Runtime artifacts (config, logs) are written outside the repo; nothing here is generated at runtime.
