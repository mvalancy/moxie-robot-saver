# Design language — the static site

For anyone styling a page under [`sim/web/`](../../sim/web/): the hub, the simulator, the setup page and
the docs explorer. **North star: the hub** ([`index.html`](../../sim/web/index.html)): a dark, engineered
**robot-telemetry / control-room** look, so the site's pages read as one product. The tokens live
in-repo ([`css/hud.css`](../../sim/web/css/hud.css)), so the guide stands on its own.

**Scope.** This language is the static site's. The parent console ([`server/static/`](../../server/static/))
keeps a deliberate exception: a light, rounded phone-app theme (`--bg: #f4f5fb`, `--card: #fff`,
`--radius: 16px` in [`style.css`](../../server/static/style.css)) with a dark variant from
`prefers-color-scheme`, because it is a parent's phone app, not a control room.

## Mood
Dark, precise, **engineering-grade**. Think a mission-control HUD for an autonomous machine: a near-black
"void", neon-cyan hairlines and glows, monospace telemetry readouts, generous negative space, restraint
over decoration. Motion is subtle and purposeful (glows, fades, scan-lines) — never bouncy.

## Color tokens

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

**Known drift.** The hub and the docs explorer set `--bg: #08080e`
([`index.html`](../../sim/web/index.html), [`docs.html`](../../sim/web/docs.html)), one step darker than
the `#0a0a0f` above that the simulator ([`css/hud.css`](../../sim/web/css/hud.css)) and the setup page
use. Pick one the next time either page is touched.

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
- **Simulator** ([`sim/web/sim.html`](../../sim/web/sim.html)) — opens as a toy ("Meet Moxie — talk with
  a little robot"): the 3D Moxie in the void, with the HUD rail behind it (Motors as telemetry gauges,
  Live-bus as a connection console, Transcript as a comms log, Session as record/replay controls). See
  the [SIL doc](../architecture/sil-and-cicd.md).
- **Hub, setup page, docs explorer** ([`sim/web/`](../../sim/web/)) — the same tokens and fonts.
- **Parent console** ([`server/`](../../server/)) — the exception above; it does not use this language.

## Provenance
The tokens were first sampled from an external personal site, [`valpatel.com`](https://valpatel.com), in
August 2026 and captured here so the guide stands even if that site changes; the hub is the reference now.

---
📖 [SIL simulator](../architecture/sil-and-cicd.md) · [Docs index](../README.md)
