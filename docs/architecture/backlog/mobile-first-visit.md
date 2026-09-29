# The composer dock: talking to Moxie on a phone

**Status:** shipped — `#chat-dock` in [`sim/web/sim.html`](../../../sim/web/sim.html), styled by
[`sim/web/css/dock.css`](../../../sim/web/css/dock.css); tested by
[`sim/test_mobile_layout.mjs`](../../../sim/test_mobile_layout.mjs) (blocks 6–8),
[`sim/test_a11y.mjs`](../../../sim/test_a11y.mjs) (block 4), `sim/test_typed_turn.mjs` and
`sim/test_responsive.mjs`.

## The problem it fixed

The text box was the only way to talk to Moxie, and it lived inside the engineering rail
(`<aside id="panel">`), which starts as a closed drawer below 900 px. Measured on the live site at
390×844 in a fresh profile:

| state | `#speech-input` |
|---|---|
| on load | 0×0 |
| after tapping `CONTROLS` | 262×40 at y = 2 095, far below an 844 px viewport |
| after `scrollIntoView` | reachable, and the turn completed normally |

So the turn always *worked*; it was *unreachable*. None of the six visible controls (Hub, ALIVE,
GitHub, CONTROLS, Run it locally, ✕) said "talk to Moxie", and she speaks unprompted after about
7 s — a visitor heard her and had no visible way to answer.

## How it works

`#chat-dock` is the page's own bottom grid row at every viewport width. It holds:

- `#chat-cue` — a one-line cue that names the action ("Talk to Moxie").
- `#transcript` — the conversation log, moved out of the rail. It scrolls within a bounded
  `max-height`, has `role="log"`, and `tabindex="0"` so keyboard users can scroll it.
- `#chat-openers` — three one-tap conversation starters that send a real turn (see
  [`gamify-the-public-sim.md`](gamify-the-public-sim.md)).
- One row with `#speech-input`, `#mic-btn` and `#speech-btn`.

The dock claims only its own footprint; `#hud` is `pointer-events: none`, so the rest of the stage
stays orbit-draggable. `env(safe-area-inset-bottom)` keeps the row off an iPhone's home indicator.
`moxie.js` frames Moxie above the dock so she is never behind it.

## Decisions

- **Move the controls, never copy them.** These are the same nodes that lived in the rail. Every
  listener binds by id (`hud.js`, `mic.js`, `env.js`, `cloud-transport.js::adoptSpeechControl`, the
  `moxie.js` speech wiring), so re-parenting changed no behaviour. A second text box is the trap:
  visitors try the first box they see, and two boxes would disagree.
- **Do not open the drawer on load.** The drawer still starts closed at every phone width
  (`rail.js`, breakpoint `(max-width: 899px)`). The composer is reachable because it left the drawer,
  not because the drawer opened.
- **The rail is otherwise untouched.** At ≥ 900 px it is still a permanent column; the composer is
  also present on desktop, at the bottom of the stage column, on purpose — one composer, not a
  phone-only special case.
- **`section.sub` on the composer is load-bearing.** `cloud-transport.js::ensureStatus` puts
  `#chat-status` ("thinking…", refusals, the over-long warning) into
  `#speech-btn.closest("section.sub")`, and `sim/test_typed_turn.mjs` asserts the status lands there.

## Tests

- `sim/test_mobile_layout.mjs` block 6 — on a **cold** load (no tap, no scroll, drawer shut), the
  composer has a non-zero rect inside the initial viewport whose centre hit-tests to itself. Never
  `element.exists`: the defect was exactly the gap between existing and reachable.
- Block 7 — a whole typed turn with the rail never opened; the drawer stays closed throughout.
- Block 8 — the openers live in the dock and send a turn.
- `sim/test_a11y.mjs` block 4 — the composer is keyboard-reachable with the drawer shut, and a
  collapsed drawer leaves no tab stops behind it.
- `sim/test_responsive.mjs` — seven viewports from phone to ultrawide, canvas still full-bleed.

## Known gaps

The production numbers above are the *defect*, taken before the fix; the live site has not been
re-measured by hand since. Layout-over-time issues (the dock grows as the log fills) are covered in
[`turnstile-layout-collision.md`](turnstile-layout-collision.md).

---
📖 [Backlog index](README.md) · [Live Sim demo](live-sim-demo.md)
