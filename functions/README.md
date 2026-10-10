# functions — Cloudflare Pages Functions for the hosted Sim

The hosted site is a static Cloudflare Pages project (`pages_build_output_dir = "sim/web"` in
[`../wrangler.toml`](../wrangler.toml)). These Functions are the only server code it has. They answer
same-origin `/api/*` requests, so the browser never makes a cross-origin call and never holds a key.

Spec: [live Sim demo](../docs/architecture/backlog/live-sim-demo.md) (section numbers below refer to
it). Deploying: [deploy on Cloudflare](../docs/guides/deploy-cloudflare.md).

## Routes

| File | Route | What it does |
|---|---|---|
| [`api/health.js`](api/health.js) | `GET /api/health` | Mode and capacity probe. Never calls the gateway; always 200. Reports `mode`, `reason`, `load`, the limits the page may know, and the Turnstile sitekey (empty unless enforced). |
| [`api/chat.js`](api/chat.js) | `POST /api/chat` | One conversation turn. Builds the upstream request itself (fixed model, `max_tokens`, temperature, messages); anything the client sends for those is ignored. Returns the reply plus a signed speech ticket and signed context. |
| [`api/speech.js`](api/speech.js) | `POST /api/speech` | Speech for a line this deployment just wrote. It has no text field: the text is inside the signed ticket, so it cannot be used as a free text-to-speech API. |
| [`api/transcribe.js`](api/transcribe.js) | `POST /api/transcribe` | Speech-to-text for the microphone. Off unless `DEMO_STT_MODEL` is set; size and length capped. |

Helpers in [`api/_lib/`](api/_lib/README.md) (the leading underscore keeps them unroutable; none exports
an `onRequest` handler):

| File | Role |
|---|---|
| `env.js` | Reads and validates every `DEMO_*` variable. The only place secrets are read. |
| `limits.js` (+ `counters.js`, `sharedtier.js`, `clientip.js`, `body.js`) | Admission: origin pin, per-IP windows, the request-unit budget, the concurrency ceiling, bounded body readers. |
| `envelope.js` | The single response shape and the status / `Retry-After` mapping. |
| `hmac.js` | HKDF and HMAC-SHA-256 for the speech ticket and context blob, with separate domain labels and constant-time compare. |
| `prompt.js`, `reply.js`, `turnshape.js`, `docsearch.js` | Building the upstream prompt, parsing the reply, varying the kind of turn Moxie takes, and grounding answers in the site's own docs (retrieved server-side). |
| `upstream.js` | Shared handling of redirects, 429s and fetch failures for the spending routes. |
| `wire.js` | The chat and TTS response fields, matching `mqtt/moxie_sdk/`. |
| `ttscache.js` | Cache of synthesized audio. A hit makes no upstream call. Per-colo, fails open, off with `DEMO_TTS_CACHE=0`. |
| `wav.js` | WAV parser that keeps the file's own sample rate. |
| `turnstile.js` | The bot check (below). |
| `safety.js`, `safety.rules.js` | The safety floor, both sides of a turn. A blocked child line never reaches the gateway, and the child side of every category that blocks carries the robot's own table's words, phrases and guards (its story, accident and idiom guards are her side only; the one normalization difference, the German sharp S, is the robot's stricter side); an unsafe completion never reaches a voice ticket (the rule's redirect line is served in its place; a diagram that trips the table is dropped); a hurt child's reply that does not point them to a trusted grown-up gets one referral sentence appended (a reply that steers them away in the shapes the check lists — "don't tell a grown-up", "you don't have to", "maybe later", in the sentence that points or in any other — never counts, and she never says those or promises a secret), and a hurt child whose turn is blocked, whose reply is swapped, or whose gateway call fails after the check hears a referral line, never a change of subject (the refusal keeps its reason and status and carries the line in `messages`). |

## Rules

1. **The repo is public.** No key, token, account id or deployment hostname is committed or sent to
   the browser. Secrets arrive as Cloudflare environment bindings and are read only in `env.js`.
   `wrangler.toml` has no `[vars]` block.
2. **Safe by default.** With nothing configured, `/api/health` reports `gateway_not_configured` and
   the page runs the scripted demo. A preview with no secrets is therefore safe.
3. **Demo mode.** Nothing here writes durable state or reaches a supervisor or console endpoint.
4. **No JSON imports.** The Pages bundler rejects `import … with { type: "json" }` even though Node
   accepts it, so no `.json` file lives here; data goes in a `.js` module (see `safety.rules.js`).
   `sim/test_demo_proxy.mjs` enforces this.

## Security properties

- **The gateway key never leaves the Function.** It is stored non-enumerable, sent only as an
  outbound `Authorization` header, and no upstream status, body or header is forwarded.
  `sim/test_demo_proxy.mjs` checks every response for the key, base URL and model ids.
- **Every refusal costs nothing upstream.** Unconfigured, bad origin, too long, tampered context,
  safety block, rate limit, budget, capacity, failed bot check: all return before `fetch()`, and
  refunded units mean refusals do not drain the shared budget.
- **The rate limits are best-effort.** Counters are per-colo and fail open; they are not a global
  spending ceiling (§4.6). Put a hard budget on the gateway itself.

## Bot check (Cloudflare Turnstile)

Turnstile guards both routes a visitor can spend money through, each with its own action so a
token for one cannot be used on the other:

| Route | Action | Token location |
|---|---|---|
| `POST /api/chat` | `chat` | `cf-turnstile-response` in the JSON body |
| `POST /api/transcribe` | `transcribe` | `X-Turnstile-Response` header (the body is audio) |
| `POST /api/speech` | none | needs a ticket that only `/api/chat` issues |

- **Enable** by setting both `DEMO_TURNSTILE_SECRET` (secret) and `DEMO_TURNSTILE_SITEKEY`
  (plain variable). One without the other is reported as a misconfiguration. Leave both unset on
  previews: a preview hostname is not on the widget's allowed list.
- **Three checks:** `success`, the expected action, and the hostname (default: the request's own).
- **Fails closed on "no", open on a Cloudflare outage.** Spending is already capped, and a third-party
  outage should not take down the demo.
- **Two refusal reasons:** `turnstile_failed` (403, the visitor's token; that turn falls back to
  the script) and `turnstile_misconfigured` (503, our secret or hostname; degrades the page). A wrong
  secret comes back from Cloudflare as HTTP 400 with an error code, and is reported as
  misconfigured rather than treated as an outage.
- Nothing from Cloudflare's reply is forwarded to the browser.

## A gateway behind Cloudflare Access

A tunnel protected by Access answers an unauthenticated request with an HTML login page and status
200. Set `DEMO_GATEWAY_ACCESS_CLIENT_ID` and `DEMO_GATEWAY_ACCESS_CLIENT_SECRET` (secret); both are
sent as `CF-Access-Client-*` headers with every upstream call. Setting only one counts as
unconfigured. An HTML reply from upstream is reported as `gateway_unreachable_or_gated`.

## Configure

Cloudflare dashboard → Workers & Pages → the project → Settings → Environment variables, for
**Production only**, so previews stay keyless. The full variable table is §5 of the spec. With none
set, the site is the scripted demo.

## Test

The handlers are plain ES modules that take a `Request` and an `env` object, so they run under Node
with no Cloudflare account, key or Turnstile widget:

```sh
node sim/test_mode.mjs             # the page's mode machine and the health probe
node sim/test_demo_proxy.mjs       # caps, origin pin, no-leak sweep (sections in sim/tests/edge/demo_proxy/)
node sim/test_demo_tickets.mjs     # ticket forgery, expiry, replay, tampering
node sim/test_demo_ears.mjs        # /api/transcribe
node sim/test_wav_decode.mjs       # the audio contract
node sim/test_turnstile.mjs        # the bot check, using Cloudflare's documented test keys
node sim/test_cloud_transport.mjs  # voice-first ordering on an injected clock
node sim/test_fallback_coverage.mjs
python3 sim/tools/turnstile_mutation_check.py   # removes each guard and requires its test to fail
```

`sim/tests/test_ci_workflows.py` checks that the CI steps running these reference no credential.
