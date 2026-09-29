# The Turnstile challenge and the bottom controls

**Status:** fixed — [`sim/web/turnstile.js`](../../../sim/web/turnstile.js) (`controlsTop()`, `place()`),
tested by [`sim/test_mobile_layout.mjs`](../../../sim/test_mobile_layout.mjs) blocks 4, 5 and 9.

## The defect

`#chat-dock` is the bottom row of the page's HUD grid, and the `#transcript` inside it grows as
Moxie's ambient self-talk writes lines to the log, up to its `min(26vh, 168px)` cap. Every pixel of
that growth comes out of the stage row above, so everything above the dock, including the drawer
handle `#rail-toggle`, rides up. A Turnstile challenge centred in the *viewport* does not move, so on
ordinary modern phones the handle climbed into it and the challenge covered part of a control.

With the dock at its cap, the handle and a viewport-centred 65 px challenge overlap only for
**683 < viewport height < 909** — measured at 390×844 (32 px of overlap) and 393×851 (29 px). A
375×667 phone is *below* that window: its dock pushes the handle past the challenge. So this was
never a short-viewport bug; it was the common-phone case.

## Why no test saw it

The layout suites sampled within about a second of load, while the log was empty and the dock at its
minimum height. The collision needs roughly 30 s of ambient lines to develop. A centre-point hit test
was not enough either: at some heights the challenge covered the top of the 48 px handle while
`elementFromPoint()` at its centre still answered `#rail-toggle`.

## How it works now

Cloudflare's `render()` API has no position option — the widget draws inside the container it is
given — so placement is this repo's job. `turnstile.js` renders the challenge in a bounded region
**above the page's bottom stack** instead of the whole viewport:

- `controlsTop()` takes the top of `#chat-dock` (plus `#panel` in drawer mode, below 900 px) and
  subtracts the transcript's **remaining** growth (`growth()`), so the challenge does not move as
  she keeps talking.
- `place()` sets the holder's `bottom` from that, with an 8 px gap (`PLACE_GAP`).
- With at least 300 px of room (`PLACE_CENTRE_MIN`, which clears Turnstile's tallest compact widget)
  the challenge is centred; below that it is pinned to the bottom edge (`flex-end`).
- Under 88 px of room (`PLACE_MIN`, e.g. a phone in landscape) it falls back to the whole viewport:
  a challenge in the way beats a clipped, unsolvable one.

Cost: zero pixels of transcript.

## Decisions

- **Rejected: cap the dock on short viewports.** It would cost up to 113 of 168 px of transcript and
  make two independently positioned boxes miss by arithmetic that the next composer change re-breaks.
- **Rejected: move or pin `#rail-toggle`.** Fixes one control only; the composer is in the same position.
- **Not reusing `env.js`'s `--eb-lift`.** `env.js::liftBanner` only counts boxes whose bottom is in
  the lower half of the viewport; once the dock is at its cap on a short phone, `#panel` can drop out
  of that sum. `turnstile.js` computes its own bottom-stack top for that reason.

## Known gap

The same driven state showed `#env-banner` landing on `#rail-toggle` at 375×667 with no Turnstile on
the page, because of the lower-half filter above. That filter is unchanged in `env.js`, and no guard
exercises the banner with the dock at its cap. Treat it as open until re-measured.

## Tests

`sim/test_mobile_layout.mjs` block 9 reaches the failing state **without sleeping**: it drives
`window.__ambient.say()` (ambient.js's test seam) until four appends in a row leave the dock height
unchanged, checks that the log is at its computed `max-height` and overflowing (`atCap`), and gates
every assertion on that. It asserts **rect intersection** as well as the hit test. Its teeth restore
the whole-viewport layer and require the collision to come back at 844 and 851, and **not** at 667.
The `turnstilejs-inert` mutation in [`sim/tools/page_teeth_check.py`](../../../sim/tools/page_teeth_check.py)
guts `turnstile.js` and must redden these blocks.

---
📖 [Backlog index](README.md) · [Live Sim demo](live-sim-demo.md)
