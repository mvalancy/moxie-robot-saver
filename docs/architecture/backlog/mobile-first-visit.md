# 📱 On a phone, the Talk box is 2 095 px below the fold

> **Filed 2026-09-05 from measurements against production · ✅ BUILT 2026-09-05 on
> `feat/composer` · effort S/M as filed.**
> Not broken — **buried**. The distinction is the whole point of this document.
>
> **What shipped, and what this page is now for.** `sim/web/sim.html` grew `#chat-dock`:
> the page's own bottom grid row, holding a one-line cue that names the action, the
> `#transcript` moved out of the rail, and one row with `#speech-input`, `#mic-btn` and
> `#speech-btn` in it. The four controls were **moved, not copied** — this document's own
> "Suggested shape" below asked for exactly that and warned that a second control is the
> trap. The rail keeps everything else and is otherwise untouched: `rail.js` is
> byte-unchanged, the drawer still starts closed below 900 px, the desktop column is still
> a permanent column. **The acceptance list below was met as written**, and the checks that
> met it are in `sim/test_mobile_layout.mjs` (66 → 222). **Left for the reader:** the
> measurements in this page are still the production ones taken before the fix. Nobody has
> re-measured `moxie.mattvalancy.com/sim` after a deploy, so treat every number below as
> the *defect*, not as the current state.

## What a first-time phone visitor actually gets

Measured against `https://moxie.mattvalancy.com/sim` in a fresh incognito profile, cache disabled,
390 × 844 (iPhone-class), real iOS user agent:

| | desktop 1440 | iPhone 390 |
|---|---|---|
| DOM ready | 2 202 ms | 2 003 ms |
| `window.moxie` ready | 3 118 ms | 2 659 ms |
| mode | `live` | `live` |
| CSP violations | 0 | 0 |
| console errors | 0 | 0 |
| horizontal scroll | none | none |
| **visible tappable controls** | — | **6** |
| **Talk box in viewport** | yes | **no — 0 × 0** |

The six things a phone visitor can see are: `Hub`, `ALIVE`, `GITHUB ↗`, `CONTROLS`, `Run it locally →`
and a `✕`. **None of them says "talk to Moxie".**

## The sequence, measured

1. Land. The Talk box exists in the DOM inside an `<aside>` with a **0 × 0** rect.
2. Moxie speaks unprompted after ~7 s — 237 400 frames @ 48 kHz, 4.95 s of ambient. **So a visitor
   hears her and cannot answer.** That is the worst ordering of those two facts.
3. Tap `CONTROLS` (`#rail-toggle`). The drawer opens: the box becomes **262 × 40 at y = 2 095**, still
   outside an 844 px viewport. Waiting longer does not help — measured again at +2 s, unchanged.
4. `scrollIntoView` puts it at y = 663, and **the turn then completes**: `sent=true`,
   transcript grew.

**It works.** A visitor who taps a button labelled `CONTROLS` and then scrolls roughly 2 000 px inside
the drawer can talk to Moxie, and she answers. Almost nobody will do that.

## Why this is filed as the top live-page item

The owner's stated goal is the live public page. A link to a demo is opened on a phone more often than
on a desktop, and the primary interaction is currently two non-obvious steps and a very long scroll
below the fold — **after** the page has already spoken to the visitor.

## What NOT to do

- **Do not "fix" it by opening the drawer on load.** `sim/test_mobile_layout.mjs` deliberately asserts
  the closed-drawer start below 900 px; that decision was made for a reason and there is a test that
  will tell you so. If it should change, change the test *and* argue it.
  *(Obeyed. The drawer still starts closed at every phone width, and the shipped fix asserts
  that it is closed on a cold load and stays closed through a whole typed turn — the
  composer is reachable **because it left the drawer**, not because the drawer opened.)*
- **Do not move the whole rail.** The controls are a legitimate side panel on desktop; this is about
  the *one* control a first-time visitor needs.

## Suggested shape, to argue with rather than follow

Put a single, obvious "Talk to Moxie" affordance **above the fold** on narrow viewports that focuses
the existing input — reusing `#speech-input` and `#speech-btn` rather than adding a second control
with its own state. The drawer keeps everything else.

## Acceptance

- On 390 × 844, from a cold incognito load, a visitor can send a turn with **no scrolling** and at most
  one tap, and the transcript grows.
- Assert on the **rect in the viewport**, not on the element existing — this whole finding is the
  difference between those two.
- 360 px and 414 px behave the same; desktop ≥ 900 px is **unchanged**, pinned by a test.
  *(Met with one honest qualification: the rail on desktop is byte-for-byte the column it
  was — same width, same open-by-default, `rail.js` untouched — and `sim/test_responsive.mjs`
  is green across all seven viewports with the canvas still full-bleed. What did change on
  desktop is that the composer is there too, at the bottom of the stage column, centred and
  capped at 760 px. That is not "unchanged"; it is the same fix applied at a width that did
  not need it, and it is deliberate — one composer, not a phone-only special case.)*
- Whatever the new affordance is, it must not fire before `window.moxie` exists, or the first tap is
  swallowed.
- `sim/test_mobile_layout.mjs` (48 checks when this was filed, 66 by the time it was built)
  stays green or its change is argued in the PR. *(It is green at **222** checks. Two of its
  teeth blocks were re-aimed and the change is argued in place, in the file: the hosted
  banner's collision teeth and the Turnstile holder's both hit-tested `#rail-toggle`, which
  stopped being the lowest control on the page the moment the composer took the bottom row,
  so both would have reported "no collision" and gone green while measuring nothing.)*

## Honest note

Nothing here is a regression: the responsive work in `test_mobile_layout.mjs` does what it says, and
the drawer-closed default is deliberate. This is a **first-visit discoverability** gap that the
existing tests were never asked to catch, found by driving the page as a stranger with a phone rather
than as a developer who knows where the controls live.
