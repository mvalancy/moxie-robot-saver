# functions/api/_lib/ — helpers, not routes

Nothing here is a route. A leading underscore is Cloudflare Pages' convention for
"not routable"; the spec's §10 assumption 9 records that as **inferred, not verified**.
The fallback if a real deploy ever routes `/api/_lib/env.js` is to inline these into each
route file — and in the meantime nothing here exports an `onRequest*` handler, so even if
it were routed it would answer 405 rather than run.

| file | what it owns |
|---|---|
| [`env.js`](env.js) | **The only place a `DEMO_*` variable is read.** Every default, clamp and required-value rule lives once. `baseUrl`, `apiKey`, both Access halves, the ticket secret and the Turnstile secret are defined **non-enumerable**, so `JSON.stringify(cfg)` — the shape of every accidental leak — cannot contain them. |
| [`envelope.js`](envelope.js) | The one response shape, built from a **fixed key allowlist** rather than by spreading a caller's object, plus the status and `Retry-After` table and the frozen `/api/*` header set (`_headers` does **not** apply to a Function response — that was settled by a real deploy). |
| [`limits.js`](limits.js) | Request admission in one function: origin pin → per-IP windows → unit budget → concurrency ceiling → the bounded FIFO. Plus the Cache API tier for the per-IP *minute* window, which is per-colo, defeated by a burst, and **fails open by construction** — every error is an undercount. |
| [`hmac.js`](hmac.js) | HKDF over `crypto.subtle` HMAC-SHA-256; mint/verify the speech ticket and the context blob under **separate domain labels**; a constant-time compare with no early exit. |
| [`turnstile.js`](turnstile.js) | The bot control, in front of **both** spending routes with one `action` each (`TURNSTILE_ACTIONS`). One `siteverify` call, **all three** mandatory checks (`success`, this route's `action` compared EXACTLY, a hostname allowlist that defaults to the request's own hostname), and the deliberate split: **fail CLOSED on a verdict, fail OPEN on a transport failure** — with the one carve-out that a wrong secret arrives as an HTTP **400** and must refuse anyway, or the whole control switches itself off silently for a one-character typo. |
| [`safety.js`](safety.js) + [`safety.rules.js`](safety.rules.js) | The pre-inference floor, and the rule table as a plain `.js` data module. **No `.json` may be imported anywhere under `functions/`** — the Pages build rejects import attributes, and it took a real deploy to find out. |
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
   differs per helper and is argued in each file's header — `limits.js`'s cache tier fails
   open because an undercount costs a few extra turns; its concurrency ceiling is
   deliberately *not* in that tier because a lost write there would leak a slot and fail
   closed; `turnstile.js` fails closed on a verdict and open on a transport failure, and
   both halves are in the mutation table because neither shows up in a green suite.

---
📖 [The routes](../README.md) · [The Functions tree](../../README.md) ·
[Live Sim demo spec](../../../docs/architecture/backlog/live-sim-demo.md)
