# 🎨 Design language — Moxie web apps

> **North star:** [`valpatel.com`](https://valpatel.com) — a dark, engineered **robot-telemetry / control-room**
> aesthetic. Every web app in this repo (the [SIL](../../sim/web/), the [server UI](../../server/)) uses
> this language so they read as one product. Tokens captured in-repo (self-sufficiency — the guide
> stands even if the reference site changes).

## Mood
Dark, precise, **engineering-grade**. Think a mission-control HUD for an autonomous machine: a near-black
"void", neon-cyan hairlines and glows, monospace telemetry readouts, generous negative space, restraint
over decoration. Motion is subtle and purposeful (glows, fades, scan-lines) — never bouncy.

## Color tokens (from valpatel.com)

```css
:root {
  /* backgrounds — deepest → elevated */
  --void:        #060609;   /* deepest ground */
  --bg:          #0a0a0f;   /* app background */
  --surface-1:   #0e0e14;   /* panels */
  --surface-2:   #12121a;   /* raised cards */
  --surface-3:   #1a1a2e;   /* hover / active */
  --hairline:    #2a303c;   /* borders (solid) */

  /* text — cool grey ramp */
  --text:        #e8edf5;   /* primary */
  --text-dim:    #c8d0dc;   /* secondary */
  --muted:       #8892a4;   /* labels / captions */
  --muted-2:     #6b7a8d;
  --dim:         #5a6577;   /* faint */
  --dimmer:      #4a5568;   /* faintest / disabled */

  /* accents */
  --cyan:        #00f0ff;   /* PRIMARY — links, focus, active, glow */
  --cyan-dim:    #0e7490;   /* dimmed cyan */
  --amber:       #fcee0a;   /* warning / highlight (a chartreuse-yellow) */
  --mint:        #05ffa1;   /* success / connected / go */
  --magenta:     #ff2a6d;   /* error / alert / stop */
  --purple:      #a855f7;   /* aux category */

  /* cyan at low alpha = ambient glow / hairlines on dark */
  --glow-06:     rgba(0,240,255,0.06);
  --glow-12:     rgba(0,240,255,0.12);
  --glow-30:     rgba(0,240,255,0.30);
}
```

**Usage rules**
- **Cyan is the signature.** Use it for the active/live state, focus rings, key borders, and glows —
  but sparingly and often at **low alpha** (`--glow-*`) for ambient hairlines, full-strength only for the
  one thing that matters (a live indicator, a hovered control).
- **State = color:** `--mint` connected/OK, `--magenta` error/disconnected, `--amber` recording/attention.
- Backgrounds step `--void → --bg → --surface-1/2/3`; borders are `--hairline` (solid) or `--glow-*`
  (glowing). Never pure `#000` or pure `#fff`.

## Typography

```css
/* headings + body */  font-family: 'Inter', system-ui, sans-serif;   /* 300 400 500 600 700 */
/* data / labels */    font-family: 'JetBrains Mono', 'Fira Code', monospace;  /* 400 500 */
```
- **Inter** for prose, headings, buttons. Headings: 600–700, **tight** letter-spacing (`-0.01em`…`-0.02em`).
- **JetBrains Mono** for all **telemetry**: numbers, IDs, topic names, status lines, section labels
  (`UPPERCASE`, `letter-spacing: 0.08em`, `--muted`). This mono-labels-on-dark move is the core of the look.
- Fonts are **vendored** at [`sim/web/vendor/fonts/`](../../sim/web/vendor/fonts/) (woff2 + `fonts.css`) so
  the apps render offline — no CDN.

## Layout & components
- **Void canvas + floating HUD panels.** The hero (the 3D Moxie) sits in the void; controls live in
  translucent dark panels (`--surface-1`, 1px `--hairline`/`--glow-12` border, generous padding).
- **Radius:** small and technical — `4–8px` (not pill-round). **Shadows:** minimal; prefer a faint cyan
  glow (`0 0 0 1px var(--glow-12)`, or `0 0 24px var(--glow-06)`) over drop shadows.
- **Section labels:** mono, uppercase, `--muted`, with a short cyan tick/underline.
- **Buttons:** dark surface, `--hairline` border, `--text`; hover → `--surface-3` + `--cyan` text/border
  + subtle glow. Primary/active → cyan border + `--glow-*` fill.
- **Inputs/sliders:** dark track, cyan fill/thumb, mono value readout.
- **Live indicators:** a small dot — `--mint` (live), `--magenta` (down), `--amber` (recording) — with a
  soft pulse.
- **Optional texture:** a faint cyan grid or scan-line at very low alpha (`--glow-06`) on the void.

## Motion
- Transitions `120–200ms ease`. Hover: border/glow fade-in. Live dot: slow 2s pulse. Avoid large
  transforms; the machine is precise, not springy.

## Applying it
- **SIL** ([`sim/web/`](../../sim/web/)) — the flagship: a Moxie **control room**. 3D Moxie in the void;
  right rail of HUD panels (Motors as telemetry gauges, Live-bus as a connection console, Transcript as a
  comms log, Session as record/replay controls). See the [SIL doc](../architecture/sil-and-cicd.md).
- **Server UI** ([`server/`](../../server/)) — the same tokens/fonts for its admin/status pages.

---
📖 [SIL simulator](../architecture/sil-and-cicd.md) · [Docs index](../README.md)
