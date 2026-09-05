# 📱 On a phone, the Talk box is 2 095 px below the fold

> **Filed 2026-09-05 from measurements against production · 🟢 build-ready · effort S/M.**
> Not broken — **buried**. The distinction is the whole point of this document.

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
- Whatever the new affordance is, it must not fire before `window.moxie` exists, or the first tap is
  swallowed.
- `sim/test_mobile_layout.mjs` (48 checks) stays green or its change is argued in the PR.

## Honest note

Nothing here is a regression: the responsive work in `test_mobile_layout.mjs` does what it says, and
the drawer-closed default is deliberate. This is a **first-visit discoverability** gap that the
existing tests were never asked to catch, found by driving the page as a stranger with a phone rather
than as a developer who knows where the controls live.
