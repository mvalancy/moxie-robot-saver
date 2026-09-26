# `sim/web/css/` — the SIM's stylesheet, split by region

Linked from [`../sim.html`](../sim.html) in this order, which **is** the cascade:

1. **`hud.css`** — tokens, atmosphere, the HUD grid, topbar, notice, stage overlays, speech bubble.
2. **`dock.css`** — the composer dock: cue line, conversation log, text box, mic, send.
3. **`rail.css`** — the engineering rail: panel groups, motors, buttons, inputs, comms log.
4. [`../style.css`](../style.css) — responsive layout and late additions, last so it wins ties.

Covered by the `/css/*` `no-cache` rule in [`../_headers`](../_headers).

📖 [sim/web](../README.md) · [Back to top](../../../README.md)
