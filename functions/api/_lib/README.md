# functions/api/_lib/ — helpers, not routes

Nothing here is a route. A leading underscore is Cloudflare Pages' convention for
"not routable"; the spec's §10 assumption 9 records that as **inferred, not verified**.
Under `wrangler pages dev`, `GET /api/_lib/env.js` answers the static site's fallback page,
not the module — so locally, at least, `_lib/` is not routed. Nothing here exports an
`onRequest*` handler either way.

| file | what it owns |
|---|---|
| [`env.js`](env.js) | **The only place a `DEMO_*` variable is read.** Every default, clamp and required-value rule lives once. `baseUrl`, `apiKey`, both Access halves, the ticket secret and the Turnstile secret are defined **non-enumerable**, so `JSON.stringify(cfg)` — the shape of every accidental leak — cannot contain them. |
| [`envelope.js`](envelope.js) | The one response shape, built from a **fixed key allowlist** rather than by spreading a caller's object, plus the status and `Retry-After` table and the frozen `/api/*` header set (`_headers` does **not** apply to a Function response — that was settled by a real deploy). |
| [`limits.js`](limits.js) | Request admission in one function, `admit()`: origin pin → per-IP windows → unit budget → concurrency ceiling → the bounded FIFO, and the granted slot's refund/release rules. Re-exports the four modules below, so every importer still reads one surface. |
| [`counters.js`](counters.js) | The one in-process `state` object and the minute/hour/day ledgers `limits.js` and `sharedtier.js` both charge. **Best-effort and per-isolate — not a global ceiling.** |
| [`sharedtier.js`](sharedtier.js) | The Cache API tier: every per-IP window (minute, hour, day) and both unit-budget ceilings (hour, day), per-colo, defeated by a burst, and **fail open by construction** — every error is an undercount. The concurrency ceiling is deliberately *not* here: a lost write would leak a slot and fail closed. |
| [`clientip.js`](clientip.js) | `clientIp` / `ipKey` (IPv6 collapsed to its /64) and `checkOrigin`, the origin pin. `curl` forges `Origin` trivially — it is a cost control, not a bot control. |
| [`body.js`](body.js) | The bounded JSON and audio body readers. |
| [`upstream.js`](upstream.js) | What the three spending routes share around their one gateway `fetch()` (which stays in each route): `redirect: "manual"`'s argument, the 429/3xx mapping, the fetch-failure mapping and the refusal envelope. |
| [`prompt.js`](prompt.js) | `buildUpstreamBody` — the server-built chat request (fixed model, `max_tokens`, message array); the client's fields are never forwarded. |
| [`reply.js`](reply.js) | Pure parsing of a completion: the expressive tags, the diagram split, the echo check behind the one re-roll. |
| [`hmac.js`](hmac.js) | HKDF over `crypto.subtle` HMAC-SHA-256; mint/verify the speech ticket and the context blob under **separate domain labels**; a constant-time compare with no early exit. |
| [`turnstile.js`](turnstile.js) | The bot control, in front of **both** spending routes with one `action` each (`TURNSTILE_ACTIONS`). One `siteverify` call, **all three** mandatory checks (`success`, this route's `action` compared EXACTLY, a hostname allowlist that defaults to the request's own hostname), and the deliberate split: **fail CLOSED on a verdict, fail OPEN on a transport failure** — with the one carve-out that a wrong secret arrives as an HTTP **400** and must refuse anyway, or the whole control switches itself off silently for a one-character typo. |
| [`safety.js`](safety.js) + [`safety.rules.js`](safety.rules.js) | The pre-inference floor, and the rule table as a plain `.js` data module. **No `.json` may be imported anywhere under `functions/`** — the Pages build rejects import attributes, and it took a real deploy to find out. |
| [`turnshape.js`](turnshape.js) | The per-turn **shape cue**: three moves (`tell`/`ask`/`offer`) recognised from a finished sentence by structural tests with no threshold in them, and the rule that names the one she has gone longest without making. It reads only the **signed** history, so no visitor text can reach the cue. Measured: the longest run of a single move in a `loop` conversation fell from **6 turns to 2** while `questionRate` did not move — see `docs/architecture/backlog/live-sim-demo.md` §4.10 for why that is the point rather than a disappointment. |
| [`wire.js`](wire.js) | The two payload field sets, transcribed from `mqtt/moxie_sdk/`, and the minimal markup floor. |
| [`wav.js`](wav.js) | RIFF walker → `{pcm, rate, channels}`, carrying the header's **own** rate out. |
| [`ttscache.js`](ttscache.js) | The synthesised-audio cache behind `/api/speech`. Keyed on the voice as well as the text, because a key that ignored it would play one child another's line. |

## The two rules that hold across all of them

1. **A secret is read here and appears in exactly one outbound request.** Never in a
   response body, a response header, an error string, a log line or a thrown stack. The
   non-enumerable definitions in `env.js` are the *structural* half of that promise;
   `sim/test_demo_proxy.mjs` and `sim/test_turnstile.mjs` sweep **every** response on
   **every** path for each secret as the empirical half.
2. **Fail in the direction that cannot cost anyone anything.** Which direction that is
   differs per helper and is argued in each file's header — `sharedtier.js` (the cache tier) fails
   open because an undercount costs a few extra turns; its concurrency ceiling is
   deliberately *not* in that tier because a lost write there would leak a slot and fail
   closed; `turnstile.js` fails closed on a verdict and open on a transport failure, and
   both halves are in the mutation table because neither shows up in a green suite.

---
📖 [The routes](../README.md) · [The Functions tree](../../README.md) ·
[Live Sim demo spec](../../../docs/architecture/backlog/live-sim-demo.md)
