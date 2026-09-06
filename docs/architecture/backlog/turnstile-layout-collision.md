# 🧩 The Turnstile challenge and `#rail-toggle` share a band on a small phone

> **Status: 🟢 fixed 2026-09-06 — and the numbers below are the SECOND set, measured against
> the first.** The challenge no longer centres in the viewport; it centres in the space above
> the page's bottom-anchored controls (`sim/web/turnstile.js`, `place()`). The guard that can
> actually see the failure is `sim/test_mobile_layout.mjs` block 9. **Arming Turnstile is no
> longer gated on this.**
>
> Found 2026-09-06 while building the chat-dock openers (PR #192), *not* caused by them: it
> reproduced on `dev` without that change.

## What happens

`#chat-dock` is the HUD grid's bottom row, and the `#transcript` inside it **grows** — her
ambient self-talk writes a `.mutter` to the log every 11–24 s and the log runs to its
`min(26vh, 168px)` cap. Every pixel of that growth is taken out of the `1fr` **stage** row, so
everything above the dock, `#rail-toggle` included, **rides up**. A challenge centred in the
*viewport* does not move, so the drawer handle climbs into it.

## The measurements — re-derived, and they disagree with the first filing

Headless Chromium, this repo's own `sim/web`, `/api/health` publishing one of Cloudflare's
documented always-passes test sitekeys, a 300×65 stand-in widget, and `#transcript` driven to
its cap through the page's own `window.__ambient.say()` seam.

| viewport | `#chat-dock` | `#rail-toggle` cold → full | challenge band | collides? |
|---|---|---|---|---|
| 375×667 | 237 → 365 px | y=373..421 → **245..293** | 301..366 | **no** |
| 390×844 | 237 → 365 px | y=550..598 → **422..470** | 390..455 | **yes, 32 px** |
| 393×851 | 237 → 365 px | y=557..605 → **429..477** | 393..458 | **yes, 29 px** |
| 360×640 | 237 → 363 px | y=346..394 → 220..268 | 288..353 | no |

**The first filing's 187 → 315 px dock and y = 423 → 323 handle could not be reproduced.**
187 → 315 is this dock *without* PR #192's openers (each figure is exactly 50 px short), and
423 is the pre-openers handle **top**; 323 does not fall out of any layout measured here. The
shape of the finding was right and the geometry was not, which is what the filing itself asked
the fix to check.

**And the window is not where the filing put it.** With the dock at its cap the handle sits at
`vh-422 .. vh-374` and a viewport-centred 65 px challenge at `(vh±65)/2`, so they overlap for

> **683 < vh < 909**

and nowhere else — swept at 17 heights from 568 to 1024, every one measured. **375×667 — the
device the filing measured — is *below* that window:** its dock eats so much that the handle
overshoots *above* the band. The devices *inside* it are 844, 851 and 896, i.e. the ordinary
modern phone. So this was never a short-viewport bug.

## Why no test saw it

**Both suites sample within about one second of load.** `test_mobile_layout.mjs` (block 4) and
`test_typed_turn.mjs` measure while the log is empty and the dock is at its 237 px minimum. The
collision needs ~30 s of ambient mutters to develop. Nothing was wrong with the assertions; they
could not observe a state that arrives later — the same defect class this repo spent 2026-09-06
mapping and closed nine instances of.

**A centre hit test would not have been enough either.** At 870 and 896 the challenge covers the
*top* of the 48 px handle while `elementFromPoint()` at its exact centre still answers
`#rail-toggle`. The guard asserts **rect intersection**, with the hit test alongside it.

## The fix, and the two that lost

Chosen: **render the challenge in a bounded region instead of the viewport** —
`sim/web/turnstile.js` sets the holder's `bottom` from the measured top of the page's bottom
stack, `align-items: center` above 300 px of room and `flex-end` below it, and falls back to the
whole viewport under 88 px (measured: a 667×375 phone in landscape, where the behaviour is then
byte-identical to before). Cost: **zero px of transcript.**

* **Cap the dock on short viewports** — the filing's preferred option. Rejected on the
  measurements above: it buys nothing at 667 (no collision there) and clearing the band inside
  the window costs `T ≤ (vh−573)/2` — **33 px of 168 at vh=844 and 113 px of 168 at vh=683**, to
  make two independently-positioned boxes miss by arithmetic that any change to the composer or
  the openers re-breaks.
* **Move or pin `#rail-toggle`** — touches a control in `sim/web/sim.html`, and fixes the handle
  only; the composer strip is in the same position to be landed on.

**Cloudflare has no say in where the challenge goes.** Its `render()` reference documents
`sitekey`, `action`, `cData`, the callbacks, `theme`, `size`, `tabindex`, `response-field*`,
`retry*`, `language`, `execution`, `appearance` and `refresh-expired` — and nothing about
position. The widget draws inside the container it is handed, so placement was always this
repo's decision.

## The guard

`sim/test_mobile_layout.mjs` **block 9**. It reaches the failing state with **no sleep**: it
drives `window.__ambient.say()` — `ambient.js`'s declared test seam, which calls the page's own
`logMutter()` — and stops on a *measurement*, four appends in a row that do not change
`#chat-dock`'s height. `atCap` then re-derives that the log is pinned at its computed
`max-height` and overflowing, and every assertion is gated on it. Teeth: restoring the
whole-viewport layer must bring the collision back at 844 and 851, and must **not** at 667.

Verified: **4 failures / 339 checks against `origin/dev`'s `turnstile.js`** (32 px and 29 px of
bleed, `elementFromPoint` → `div#fake-cf-widget`), **339/339 green with the fix, three runs.**

## What this did NOT fix — a second, live-today defect in `env.js`

Found by the same driven state, with **no Turnstile anywhere on the page** (`turnstile: ""`,
375×667, hosted origin):

    #env-banner  y=119..280      #rail-toggle  y=245..293
    document.elementFromPoint(centre of #rail-toggle) -> div#env-banner

`env.js::liftBanner` lifts `#env-banner` above the bottom stack, but skips any box whose
`bottom` is in the **upper half** of the viewport — which is exactly what `#panel` becomes once
the dock reaches its cap on a short phone. The panel drops out of the sum, the banner is lifted
to clear only the dock, and it lands on the handle. Reproduced at every vh ≤ 740 in the sweep.
**This is real in production today**, needs no Turnstile, and is why `place()` above computes
its own bottom-stack top rather than reusing `--eb-lift`. Left unfixed here because `env.js`
belongs to another live session — **owner call**.
