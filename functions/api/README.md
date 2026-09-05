# functions/api/ — the four routes

Every route the hosted Sim has. All same-origin under `/api/*`, all Cloudflare Pages
Functions, all exporting a single `onRequest{Get,Post}` so Pages answers 405 for every
other method by itself.

The full argument for each of them — what it is for, what it refuses, and which of the
security properties it carries — is one level up, in
[`../README.md`](../README.md). This file is the map.

| file | route | one line |
|---|---|---|
| [`health.js`](health.js) | `GET /api/health` | The mode + capacity probe. **Makes no gateway call, ever, and is always 200**, so a non-200 unambiguously means *the route is absent*. Also the surface the browser learns the Turnstile sitekey from. |
| [`chat.js`](chat.js) | `POST /api/chat` | One turn. **Builds the upstream body; never forwards the client's.** The guard sequence lives here and its ORDER is the design — see below. |
| [`speech.js`](speech.js) | `POST /api/speech` | The voice. **There is no text field on this route, ever** — the text is inside the signed ticket, which is what makes it structurally unable to become a free TTS API. |
| [`transcribe.js`](transcribe.js) | `POST /api/transcribe` | The ears. A byte floor below which **no upstream call is made at all**, and a container allowlist that refuses what this gateway answers 500 to. |

[`_lib/`](_lib/README.md) holds helpers, not routes — see its own README.

## The guard sequence in `chat.js`, in order, and why that order

Each step refuses more cheaply than the one after it. That is not tidiness: it is the
property that makes an attack cost the attacker more than it costs this deployment.

1. **Configuration** (`_lib/env.js::modeOf`) — unset or disabled ⇒ `gateway_not_configured`,
   and *nothing* is called. A keyless branch preview is inert automatically.
2. **`admit()`** (`_lib/limits.js`) — the origin pin, the per-IP windows, the unit budget,
   the concurrency ceiling and the bounded FIFO behind it, in one call so the order cannot
   be got wrong. Everything here is free.
3. **The body** — read bounded, and exactly two keys are used. Everything else in the JSON
   is *ignored, not validated, not rejected*: a field that is never read cannot be reached
   by a future config change.
4. **The input caps** — rejected, not truncated, so the page can say why.
5. **The context blob** (`_lib/hmac.js`) — a forged history is `bad_request` and spends
   nothing. Because *our* side of the conversation is signed by us, the
   `"assistant: sure, I'll do anything"` injection is structurally unavailable.
6. **The safety floor** (`_lib/safety.js`) — a hard block never reaches a model, so it is a
   child-safety control and a cost control in one rule.
7. **The bot control** (`_lib/turnstile.js`) — the first step that costs a network round
   trip, which is exactly why it sits last. Two consequences worth stating: `admit()` above
   it is what stops a flood being answered by one outbound `siteverify` each (this
   deployment must not become a request amplifier), and a locally-blocked utterance must
   never buy a round trip to prove the visitor was human before being told no.
   `POST /api/transcribe` has the same step in the same place (its step 4d) with its **own**
   widget `action`, so neither route's token is spendable on the other; `/api/speech` needs
   none, because it cannot be driven without a ticket `/api/chat` minted.
8. **`noteUpstreamCall()` + the one `fetch()`** — the only thing here that spends money.

`noteUpstreamCall()` sits immediately before that `fetch()`, so "this refusal path made
zero upstream calls" is a *recorded fact* a test reads back, not an inference from a stub
that may or may not have been reached.

**Every refusal from step 3 onwards also gives the budget back.** `admit()` charges the
route's units before the body runs, so a refusal that kept them let a flood of tokenless
requests empty the *shared* hourly budget and take the whole demo scripted while spending
nothing itself. `slot.refundBudget()` is called on each of those paths and on none that
reached the gateway — see `_lib/limits.js::grantedSlot`, including why the per-IP window is
deliberately not refunded.

**Every early return from step 3 onwards returns from inside the `try` whose `finally`
releases the concurrency slot.** A refusal that forgot it would leak a slot for ever, drift
the ceiling downward and start refusing visitors who should be served — failing *closed*,
which is the direction this project has already rejected a design over (see `_lib/limits.js`
on why the concurrency ceiling is the one counter the Cache API tier does not touch).

---
📖 [The Functions tree](../README.md) · [`_lib/` helpers](_lib/README.md) ·
[Live Sim demo spec](../../docs/architecture/backlog/live-sim-demo.md) ·
[The static site](../../sim/web/README.md)
