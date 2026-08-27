# 🟢 `tools/qr-rig` — camera QR validation rig

A hardware-in-the-loop rig for showing QR codes to a Moxie that sits in front of a monitor.

> **Pivoted (2026-08).** This rig started as a *brute-force* sweep — cycling unknown candidate codes
> and listening to Moxie's beeps to guess reactions. That approach is **deprecated**: we have since
> read the QR grammar directly from `bo-wifi` (see
> [`../../docs/reverse-engineering/qr-commands.md`](../../docs/reverse-engineering/qr-commands.md))
> and built validated encoders in [`../robot-toolkit`](../robot-toolkit). The rig's job now is
> **end-to-end validation**: display codes we *know* the firmware parses (proven by schema
> round-trip + byte-parity) and observe the real reaction. Generate the deck with
> `python3 validated_codes.py --out ./deck`. The brute-force sweep below is kept only for reference.

The legacy sweep cycles candidate **QR command codes** on a fullscreen display, Moxie's camera scans
them, and the rig **listens to Moxie's beeps over a microphone** to pace itself and detect reactions.

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

## Modes
- **brute** (`--autofuzz`): sweep the full candidate list once per pass, order reshuffled each pass.
- **retest** (`--maybes-file f.json`): hammer only flagged candidates, more shows each, for confirmation.
- **focus** (`--focus`): zero in on the overnight PAUSE-producers. Tests each (a) alone to re-confirm its
  pause under the fixed pacing, (b) as a **multi-phase primer → config sub-command** (two QR frames shown
  in sequence), and (c) as a nested single-frame sub-command (`{command:factory, sub:set_endpoint}`). This
  targets the hypothesis that a manufacturing/factory mode unlocks sub-commands that write important values
  (serial, MAC, UUID, keys, and the endpoint host / GCP project — where the robot phones home).
  Switch modes live from the control UI, or `POST {"mode":"focus"}`.

## Pacing (why the timing is careful)
The QR is **never swapped while Moxie is silent**. Each frame waits for the first scan beep (PHASE 1);
the final frame then *holds* through any post-scan pause until Moxie returns to its steady rapid-scan
cadence (PHASE 2), with a 45s safety cap. This keeps a pause pinned to the code that actually caused it —
without it, one robot pause gets mis-attributed to whatever code happens to show next, flooding the results
with false positives.

## Files
- `qr_rig.py` — the whole rig (HTTP server + display + control + audio listener + fuzzer). Self-contained.

Runtime artifacts (config, logs) are written outside the repo; nothing here is generated at runtime.
