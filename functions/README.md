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
| [`api/_lib/envelope.js`](api/_lib/envelope.js) | — | The one response shape, built from a fixed key allowlist, plus the status and `Retry-After` mapping of §4.5. |

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

## Configuring it

Cloudflare dashboard → Workers & Pages → the Pages project → Settings → Environment
variables, **Production only** (so previews stay keyless). The full table is the spec's
§5. Set none of them and the site is the scripted demo, forever, safely.

## Testing it

The handlers are ES modules that take a synthetic `Request` and a plain object as
`context.env`, so they are unit-testable under bare node with no Cloudflare account —
and none may ever be required by a test:

```sh
node sim/test_mode.mjs
```

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
