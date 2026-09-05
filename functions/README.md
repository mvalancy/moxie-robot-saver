# functions/ — Cloudflare Pages Functions for the hosted Sim

Server logic for the hosted static site lives here, and **only** here. The site is a
static Cloudflare Pages project ([`../wrangler.toml`](../wrangler.toml):12 —
`pages_build_output_dir = "sim/web"`), so Pages Functions are the one place a request can
be answered by code instead of by a file. Everything is same-origin under `/api/*`, which
is why the browser never needs a cross-origin request and never needs a key.

Spec: [`../docs/architecture/backlog/live-sim-demo.md`](../docs/architecture/backlog/live-sim-demo.md).

## What is here

| file | route | what it does |
|---|---|---|
| [`api/health.js`](api/health.js) | `GET /api/health` | The mode + capacity probe. **Makes no gateway call, ever. Always 200.** Reports `mode`, `reason`, `load` and the caps the page may know. |
| [`api/_lib/env.js`](api/_lib/env.js) | — | Reads and validates every `DEMO_*` variable, with §5's defaults and clamps. Unset required values ⇒ `gateway_not_configured`. |
| [`api/chat.js`](api/chat.js) | `POST /api/chat` | One turn. **Builds the upstream body; never forwards the client's** — fixed model, fixed `max_tokens`, fixed `temperature`, fixed message array. A client `model`/`messages`/`tools`/`n` is *ignored*, not allowlisted. Returns the `remote_chat` payload `bridge.js` already renders, plus a speech ticket and a signed context blob. |
| [`api/speech.js`](api/speech.js) | `POST /api/speech` | The voice, and only for words we ourselves just wrote. **There is no text field on this route, ever** — the text lives inside the signed ticket, which is what makes it structurally unable to become a free TTS API. Sniffs the bytes, never the Content-Type. |
| [`api/_lib/envelope.js`](api/_lib/envelope.js) | — | The one response shape, built from a fixed key allowlist, plus the status and `Retry-After` mapping of §4.5. |
| [`api/_lib/hmac.js`](api/_lib/hmac.js) | — | RFC 5869 HKDF over `crypto.subtle` HMAC-SHA-256; mint/verify the speech ticket and the context blob under **separate domain labels**; a constant-time compare with no early exit. |
| [`api/_lib/limits.js`](api/_lib/limits.js) | — | Request admission: the origin pin, the per-IP windows, the request-**unit** budget, the concurrency ceiling, a bounded body reader. **Its counters are best-effort and in-process — not a global ceiling** (§4.6). |
| [`api/_lib/wire.js`](api/_lib/wire.js) | — | `build_chat_response`'s field set and `build_cloud_tts_response`'s, transcribed from `mqtt/moxie_sdk/`; the minimal markup floor built from the three mark templates `stub.js` already emits. |
| [`api/_lib/ttscache.js`](api/_lib/ttscache.js) | — | The synthesised-audio cache behind `/api/speech` (spec §4.8). A **hit makes zero upstream calls**; a miss costs one extra `match`. Keyed on the gateway, model, voice, format, sample rate and the **exact** text, under the full untruncated HMAC — a key that ignored the voice would serve one child a line in another's. **Fails open** on every failure, stores nothing but a successful synthesis, and `DEMO_TTS_CACHE=0` removes it entirely. **Per-colo, not global; a cold colo pays.** |
| [`api/_lib/wav.js`](api/_lib/wav.js) | — | RIFF walker → `{pcm, rate, channels}`, carrying the header's **own** rate out. Refuses 8/24-bit, a JSON body, and an HTML page. |
| [`api/_lib/turnstile.js`](api/_lib/turnstile.js) | — | The **bot control**: one Cloudflare Turnstile `siteverify` call between the free local refusals and the gateway call, on `POST /api/chat` **and** `POST /api/transcribe`, each with its own widget `action` (`TURNSTILE_ACTIONS`) so neither route's token is spendable on the other. All **three** mandatory checks — `success`, our own `action`, and a hostname allowlist that defaults to *the request's own hostname* so production can never be handed a `localhost` allowance by omission. **Fails CLOSED on a verdict of "no" and OPEN on a Cloudflare transport failure**, deliberately: the spend is already capped above it, and a third-party outage must not zero the public demo. Two refusal reasons, because "our secret is wrong" and "your token is bad" have opposite fixes. Off entirely unless both `DEMO_TURNSTILE_SECRET` and `DEMO_TURNSTILE_SITEKEY` are set. |
| [`api/_lib/safety.js`](api/_lib/safety.js) + [`api/_lib/safety.rules.js`](api/_lib/safety.rules.js) | — | The pre-inference floor. A hard block never calls the gateway, so it is a safety control and a cost control in one rule. **A floor, not a filter.** The rule table is a plain data module, not JSON — see below. |

`api/_lib/` holds helpers, not routes. A leading underscore is Pages' convention for
"not routable"; §10 assumption 9 records that as **inferred, not verified** — if a real
deploy ever routes `/api/_lib/env.js`, the fallback is to inline the helpers into each
route file. Nothing in `_lib/` exports an `onRequest*` handler, so even if it were routed
it would answer 405 rather than run.

## The three rules this tree is built under

1. **The repo is public.** No key, token, account id or deployment hostname is committed
   here or shipped to the browser. Secrets arrive at runtime as Cloudflare environment
   bindings on `context.env` and are read in `api/_lib/env.js` and nowhere else.
   `wrangler.toml` gets **no** `[vars]` block — it is committed and world-readable.
2. **Fail-safe default.** With no variables set at all, `/api/health` answers
   `gateway_not_configured` and the page is exactly today's static demo. A branch preview
   with no secrets is therefore automatically safe.
3. **Demo mode.** Nothing here writes durable state, and nothing here reaches a
   supervisor or parent-console endpoint. See the spec's §4.4 for the list of endpoints
   that must be *absent* rather than merely refused.

## The three security properties worth stating on their own

1. **The key never leaves this process.** It is read once, as
   `context.env.DEMO_GATEWAY_API_KEY`, inside `api/_lib/env.js` — which defines it
   **non-enumerable**, so `JSON.stringify(cfg)` cannot contain it. It appears only as an
   outbound `Authorization` header. No upstream status, body or header is ever forwarded,
   because those bodies routinely echo model names, org identifiers and key prefixes.
   `sim/test_demo_proxy.mjs` sweeps **every** response it produces — success, refusal,
   safety block, a hostile upstream 500 — for the key, the base URL and every model id, in
   the body *and* in every header.
2. **Every refusal path makes zero upstream calls.** Unconfigured, half-credentialled,
   forbidden origin, over-length, empty, tampered context, hard-blocked utterance,
   rate-limited, over budget, at capacity, **a refused bot check**: all of them return
   before `fetch()`. `api/_lib/limits.js::noteUpstreamCall()` sits immediately before the
   one `fetch()` in each route, so that is a recorded fact rather than an inference. The
   converse is asserted too, and it is the ordering rule the bot control had to obey:
   every refusal *cheaper* than Turnstile makes **zero `siteverify` calls**, so a blocked
   utterance never buys a round trip to prove the visitor is human, and `admit()` — not
   Cloudflare — is what absorbs a flood. And since 2026-09-05 those refusals cost **zero
   units** as well, not merely zero calls: `slot.refundBudget()` hands back what admission
   charged on every path that returns without reaching the gateway.
3. **`/api/speech` cannot become a free text-to-speech API.** It has no text field. The
   text is inside the ticket's signature, so the only text this deployment will ever
   synthesize is text it wrote itself in the last `DEMO_TICKET_TTL_S` seconds — a
   structural property, not a counter.

## The bot control: Cloudflare Turnstile

Everything else in this tree bounds the **cost** of a request that has already been made.
None of it can tell a child from a script — `api/_lib/limits.js::checkOrigin` says so in
capitals, because `curl` forges an `Origin` header trivially. Turnstile is the piece that
can, and it guards **both** visitor-driven spending routes:

| route | action | where the token rides |
| --- | --- | --- |
| `POST /api/chat` | `chat` | the JSON body's `cf-turnstile-response` field (Cloudflare's own form-field name) |
| `POST /api/transcribe` | `transcribe` | the `X-Turnstile-Response` **header** — the body is raw audio and has no field to put it in |
| `POST /api/speech` | *(none, and that is structural)* | it cannot be driven without a ticket `/api/chat` minted, so gating the turn gates the voice |

**The two actions are the point, not decoration.** The routes do not cost the same: a chat
turn is 160 tokens of completion, and a microphone turn is up to 15 s of billable
speech-to-text. One deployment-wide action would satisfy check 2 in appearance only — a
token minted by typing would buy the ears. So `TURNSTILE_ACTIONS` is a table, the page
renders one widget per action, and each is refused in the other's place.

* **The ears were the gap, and they were the expensive half.** An earlier version of this
  slice guarded `/api/chat` and deferred `/api/transcribe`. With the per-IP windows at
  10/min and 60/hour and **no daily window at all**, that left 15 minutes of billable
  transcription per hour reachable from one address by a `curl` loop with a 2 KB RIFF
  header and a forged `Origin`.
* **A refusal gives the shared budget back.** `admit()` charges the route's units before
  the route body runs, so a refusal that kept them turned a *paid* drain into a *free*
  one: 200 tokenless POSTs, all correctly refused with zero gateway calls, emptied
  `DEMO_UNIT_BUDGET_HOUR` and answered the next real visitor `budget_exhausted` with a
  SCRIPTED page for the rest of the hour. Every refusal inside the admitted section now
  calls `slot.refundBudget()`. The per-IP window is deliberately **not** refunded — it is
  self-inflicted, and it is the only thing that makes a flood of free refusals from one
  address go quiet; `api/_lib/limits.js::grantedSlot` carries the whole argument.
* **Both or neither.** `DEMO_TURNSTILE_SECRET` (a **secret** binding) plus
  `DEMO_TURNSTILE_SITEKEY` (a plain variable — a sitekey is public). Exactly one of the
  pair is a misconfiguration and is reported in `missing`, for the same reason half an
  Access token is: a secret with no sitekey refuses every visitor because no browser can
  mint a token, and a sitekey with no secret renders a widget nothing verifies.
* **The sitekey reaches the browser from `/api/health`**, on the envelope's `turnstile`
  field, and is `""` whenever the control is not enforced. It is deliberately **not** in
  any shipped HTML: a sitekey committed here would be *this* deployment's sitekey in a
  public repo, and every fork and every branch preview would render a widget bound to a
  domain list they are not on.
* **Previews enforce nothing, and must not.** Turnstile authorizes a hostname and all of
  its subdomains; a preview deployment's platform-assigned hostname is not on the widget's
  list, so a real challenge there could never pass. Leave both variables unset on preview
  and the whole thing is inert — no widget, no third-party script, no verification call.
* **Two reasons, opposite fixes.** `turnstile_failed` (403) is the visitor's token —
  missing, expired, replayed, wrong action; the page answers that one turn from the stub
  and **stays live**. `turnstile_misconfigured` (503, `Retry-After: 60`) is ours — a wrong
  secret, a malformed request, a hostname we do not allow; it degrades the page, because it
  will refuse every visitor identically until a variable changes. **That second reason is
  how the production secret gets validated without anyone printing it:** deploy, type one
  sentence, read the reason.
* **A wrong secret is an HTTP 400, and that is not a transport failure.** Measured against
  the live endpoint: `invalid-input-secret` and `missing-input-secret` come back as **400**,
  while every genuine verdict (`invalid-input-response`, `timeout-or-duplicate`,
  `missing-input-response`) is a 200. So a fail-open keyed on the status alone switched the
  entire control off — silently and permanently — for a secret wrong by one character, and
  made `turnstile_misconfigured` unreachable for the exact fault it exists to report. A
  non-2xx now has its `error-codes` read (and *only* those, into a boolean): our-fault
  codes refuse at any status, and a 5xx, a 3xx, an unparseable body or a 4xx naming the
  visitor's token still fail open.
* **Nothing from Cloudflare's reply is forwarded** — not the `error-codes`, not the
  hostname, not the timestamp. `sim/test_turnstile.mjs` sweeps every response for the
  widget secret *and* for every documented error-code string, including on the 400 path
  that is now parsed.

## A gateway behind a Cloudflare Tunnel

A plain public tunnel hostname is just a base URL and needs nothing extra. A tunnel
protected by **Cloudflare Access** answers an unauthenticated server-side `fetch` with an
**HTML login page at status 200**, which looks exactly like a broken gateway. So:

* `DEMO_GATEWAY_ACCESS_CLIENT_ID` + `DEMO_GATEWAY_ACCESS_CLIENT_SECRET` (a **secret**
  binding) are sent as `CF-Access-Client-Id` / `CF-Access-Client-Secret` on every upstream
  call when **both** are set, alongside the gateway key rather than instead of it.
* Exactly **one** of the pair is treated as a misconfiguration: every route answers
  `gateway_not_configured` and makes no upstream call, because calling half-credentialled
  would produce that same login page while looking configured.
* A non-JSON (or HTML) upstream reply answers the envelope with
  `reason: "gateway_unreachable_or_gated"` rather than a bare 502. The visitor sees exactly
  what a dead gateway shows; only the operator learns the door is locked rather than the
  room empty. **This is P0-b's one addition to the spec's §3.2 reason set**, and it is
  mirrored in `sim/web/mode.js` — an unknown reason there is coerced to `null` and would be
  misread as a healthy turn.

## One thing a deploy already settled: no JSON imports here

`api/_lib/safety.js` originally loaded its rule table with
`import RULES from "./safety.json" with { type: "json" }`. **The Cloudflare Pages build
rejects that**, and it took a real deploy to find out: the Pages check went `FAILURE` on the
branch that added it while the same check was green on `dev`, and that one line was the only
structural difference in this tree. Node 20 accepts the syntax, so the entire hermetic suite
was green — a bundler-specific extension cannot be validated by the runtime the tests run on.

So: **nothing under `functions/` may import a `.json` file, with or without an import
attribute, and no `.json` file lives here at all.** Data goes in a `.js` module exporting a
const, the way [`api/_lib/safety.rules.js`](api/_lib/safety.rules.js) does. `sim/test_demo_proxy.mjs`
asserts all of that, so the next attempt fails in a second locally instead of in a build log.
Recorded as assumption 26 in the spec's §10 ledger.

## Configuring it

Cloudflare dashboard → Workers & Pages → the Pages project → Settings → Environment
variables, **Production only** (so previews stay keyless). The full table is the spec's
§5. Set none of them and the site is the scripted demo, forever, safely.

## Testing it

The handlers are ES modules that take a synthetic `Request` and a plain object as
`context.env`, so they are unit-testable under bare node with no Cloudflare account —
and none may ever be required by a test:

```sh
node sim/test_mode.mjs             # the mode machine + the probe
node sim/test_demo_proxy.mjs       # the caps, the origin pin, the no-leak sweep
node sim/test_demo_tickets.mjs     # forgery, expiry, replay, tampering, constant-time
node sim/test_wav_decode.mjs       # both halves of the audio contract, sample for sample
node sim/test_turnstile.mjs        # the bot control: three checks, both halves of the
                                   # fail-open/closed split, the slot release, zero cost
node sim/test_cloud_transport.mjs  # the voice-first ordering, on an injected clock
node sim/test_fallback_coverage.mjs
```

Not one of them may ever require a Cloudflare account, a gateway key **or a Turnstile
widget**, and `sim/tests/test_ci_workflows.py` asserts that the steps running them
reference no credential at all. `sim/test_turnstile.mjs` uses Cloudflare's own
[documented dummy keys](https://developers.cloudflare.com/turnstile/troubleshooting/testing/)
as fixtures and stubs `siteverify` to answer what each of those keys is documented to
answer, so the tests are written against the published contract rather than against
somebody's recollection of it.

The guards are also proven in the other direction:

```sh
python3 sim/tools/turnstile_mutation_check.py        # 57 rows; every one must say "caught"
python3 sim/tools/turnstile_mutation_check.py D3     # …or re-check one row
```

Each row deletes one guard and requires **the check that names that guard** to redden —
stricter than the other mutation tables in this repo, which accept any non-zero exit. That
strictness earned its keep immediately: it found four assertions that passed with their
guard removed, including a `success:false` case that was actually being refused by the
*action* check.

## Unverified

**Where `functions/` must live for a Pages project whose build output directory is
`sim/web` is not established by anything in this repo** (spec §10, assumption 8 — the
highest-risk unknown in the document). It is placed at the repo root here, which is
Cloudflare's documented convention for a Git-connected project, and no `wrangler.toml`
change is believed to be needed. The failure mode if that is wrong is a silently
404-serving static site, not a build error — which `mode.js` reads as `offline` and
degrades cleanly, so the site cannot break either way. Settle it with one preview deploy
and record the answer in the spec's §10.

---
📖 [Repo README](../README.md) · [Structure map](../STRUCTURE.md) ·
[Live Sim demo spec](../docs/architecture/backlog/live-sim-demo.md) ·
[Deploy on Cloudflare](../docs/guides/deploy-cloudflare.md) ·
[The static site](../sim/web/README.md)
