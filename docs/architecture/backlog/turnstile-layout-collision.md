# 🧩 The Turnstile challenge and `#rail-toggle` share a band on a small phone

> **Status: 🟠 latent — invisible today, blocks nothing, and gates one owner decision.**
> Found 2026-09-06 while building the chat-dock openers (PR #192), *not* caused by them: it
> reproduces on `dev` without that change. Filed rather than fixed, because fixing it means moving a
> control on a page another session is actively editing, and because it cannot bite until Turnstile
> enforcement is switched on.

## What happens

The Turnstile challenge renders **vertically centred in the viewport**. On a 375×667 phone that puts
it at roughly **y = 301..366**.

`#rail-toggle` sits above the chat dock, and **the dock grows upward as the transcript fills** — the
ambient self-talk loop writes to the log continuously, so the dock climbs from **187 px to its 315 px
cap** over the first ~30 seconds of a visit. That carries `#rail-toggle` from about **y = 423 up to
y = 323** — inside the challenge band.

Measured concretely while sizing the openers: a two-row pill layout cost ~100 px and reproduced the
collision immediately, reddening `sim/test_mobile_layout.mjs` block 4. The shipped layout (three equal
wrapped-label columns, 50 px) does **not** collide — but it also does not *fix* the underlying drift,
which predates it.

## Why no test sees it

**Both suites sample within about one second of load.** `test_mobile_layout.mjs` and
`test_typed_turn.mjs` measure geometry immediately, while the transcript is still nearly empty and the
dock is at its 187 px minimum. The collision needs ~30 seconds of ambient mutters to develop.

This is the same defect class this repo spent 2026-09-06 mapping — **a check that measures at the
wrong moment** — and it is the fourth instance. The others were a baseline sampled before the state
change it measured, an image read before decode, and a fixed sleep standing in for a condition. Here
nothing is *wrong* with the assertions; they simply cannot observe a state that arrives later.

## Why it is latent

`/api/health` reports `turnstile: ""` — enforcement is deliberately **off** in production, so the
challenge does not render and the band does not exist. **The collision cannot occur until Turnstile is
armed.**

## Why that matters now

Arming Turnstile is a one-API-call owner decision that has been open all day. **This should be
resolved before that switch, not after** — otherwise the first thing arming it does is put a control
underneath a challenge on a phone.

## What a fix would need

Not decided here; three shapes, cheapest first:

1. **Cap the dock's growth on short viewports** so `#rail-toggle` never enters the band. Smallest
   change, but it trades transcript height for clearance.
2. **Move or pin `#rail-toggle`** out of the vertical middle on small screens. Touches a control on a
   page another session is editing — coordinate first.
3. **Render the challenge somewhere bounded** rather than viewport-centred, if Turnstile's widget
   allows it. Needs checking against Cloudflare's documented options.

## What a fix must also do — and this is the load-bearing part

**Add a test that can actually see it.** Any fix guarded only by the existing suites is guarded by
checks that sample too early to observe the failure, which is how this arrived. The guard must let the
transcript fill (or drive it) and *then* measure, with the challenge band present.

## Honest limits of this filing

The numbers above come from one agent's local harness with the challenge rendered. **They have not
been reproduced against production**, because production cannot render the challenge while enforcement
is off — so the measurement is sound but single-sourced. A fix should re-derive them rather than trust
this page.
