# Deploy the simulator to Cloudflare Pages

Publish the [Moxie simulator](../../sim/web/) on Cloudflare Pages, either as a static demo that needs no
configuration or as a live demo where visitors talk to a real brain in Moxie's voice. The difference
is configuration, not code.

| | Static demo | Live demo |
|---|---|---|
| Configuration | none | three values (below) |
| Brain | a scripted stub ([`stub.js`](../../sim/web/stub.js)) | your OpenAI-compatible gateway |
| Voice | pre-rendered clips ([`audio/index.json`](../../sim/web/audio/index.json)) | your gateway, clips as fallback |
| Ears | a scripted child line | your gateway |
| Cost | none | metered and capped |

With nothing configured you get the static demo. That is the safe default, and every preview
deployment stays in it because secrets are set on Production only. The page shows which mode it is in.

## 1. Deploy

`sim/web/` is a ready-built static site: no build step, every dependency vendored, and the docs bundle
committed. It is about 19 MB across ~335 files; the largest file (`vendor/mermaid.min.js`, 3.2 MB) is
well under Pages' 25 MB per-file limit. Server logic lives in [`functions/`](../../functions/README.md).

[`wrangler.toml`](../../wrangler.toml) already sets `pages_build_output_dir = "sim/web"`. In the Pages
dashboard, connect the repo with **Build command** empty, **Framework preset** None and **Output
directory** `sim/web`. The Cloudflare GitHub app deploys every push; no workflow in this repo does. From
the command line instead:

```sh
npx wrangler pages deploy sim/web --project-name <your-project>
```

## 2. What works with no configuration

Everything client-side: the 3D Moxie and its liveness, **Play demo**, hand controls, a stub
conversation with real behavior markup, Moxie's pre-rendered voice and ambient self-talk, the revival
QR codes, the setup page, and the docs explorer. The mic button falls back to a scripted child line.

## 3. Make it live

Set these on **Production only**, so previews stay keyless. They are defined in
[`functions/api/_lib/env.js`](../../functions/api/_lib/env.js) (`REQUIRED_FOR_LIVE`):

| Variable | Kind | Notes |
|---|---|---|
| `DEMO_GATEWAY_BASE_URL` | variable | Any OpenAI-compatible base URL, e.g. `https://your-gateway.example/v1`. No default. |
| `DEMO_GATEWAY_API_KEY` | **secret** | Read only inside the Function; never sent to the browser. |
| `DEMO_CHAT_MODEL` | variable | The chat model id. No default. |

Unset means degraded, never "guess a gateway". Optional:

| Variable | Gives you |
|---|---|
| `DEMO_TTS_MODEL` | Moxie's voice from the gateway (otherwise clips only). |
| `DEMO_STT_MODEL` | Ears (otherwise the scripted mic fallback). |
| `DEMO_GATEWAY_ACCESS_CLIENT_ID` + `_SECRET` | For a gateway behind Cloudflare Access. Both or neither. |
| `DEMO_TURNSTILE_SITEKEY` + `DEMO_TURNSTILE_SECRET` | A Cloudflare Turnstile bot check. Both or neither; neither turns it off. |
| `DEMO_ENABLED` | Kill switch: `0` forces degraded mode without removing the secret. |

**Use a separate, budget-limited key for the public demo.** A hard budget and rate limit at the gateway
holds even if this code is wrong; nothing in the Function can promise that.

## 4. Caps

A public demo that proxies a paid gateway needs limits. Each is a `DEMO_*` variable; defaults are in
`env.js`.

| Control | Default | Purpose |
|---|--:|---|
| `DEMO_MAX_TOKENS` | 160 | Completion length ceiling |
| `DEMO_MAX_INPUT_CHARS` | 500 | Longer input is rejected, not truncated |
| `DEMO_MAX_TTS_CHARS` | 300 | About three sentences of speech |
| `DEMO_MAX_RECORD_MS` | 15000 | Recording length ceiling |
| `DEMO_MAX_AUDIO_BYTES` / `DEMO_MIN_AUDIO_BYTES` | 500000 / 2000 | Below the floor, no upstream call |
| `DEMO_CHAT_PER_MIN` / `_HOUR` / `_DAY` | 5 / 40 / 150 | Per visitor IP |
| `DEMO_SPEECH_PER_MIN` / `_HOUR` | 10 / 80 | Per visitor IP |
| `DEMO_STT_PER_MIN` / `_HOUR` | 10 / 60 | Per visitor IP |
| `DEMO_MAX_CONCURRENT_CHAT` / `_SPEECH` | 4 / 8 | Matched to the upstream key's parallel limit; raise the queue, not these |
| `DEMO_QUEUE_MAX_WAIT_MS` / `_MAX_DEPTH` | 2500 / 8 | At the ceiling a request waits briefly instead of being refused; `0` disables |
| `DEMO_CACHE_COUNTER` | on | Counts the per-minute limits per colo (Cache API) instead of per isolate; fails open |
| `DEMO_TTS_CACHE` / `_TTL_S` | on / 86400 | Caches synthesized speech per colo; a hit costs no upstream call |
| `DEMO_UNIT_BUDGET_HOUR` / `_DAY` | 600 / 4000 | Request units (chat 3, speech 2, transcribe 2) |
| `DEMO_CHAT_TIMEOUT_MS` | 20000 | A fast degrade beats a slow success |
| `DEMO_TICKET_TTL_S` | 60 | Lifetime of a speech ticket |

These counters are **best effort**: most live in one isolate's memory and the per-minute window is per
colo. They are not a global spending ceiling; that needs a budget at the gateway (or a Durable Object,
which is not built).

## 5. Platform behavior worth knowing

- Pages routes `functions/` from the repo root even though the output directory is `sim/web`.
- `functions/api/_lib/` is not exposed. A missing route answers **200 with the site's HTML**, not 404,
  so check the content type.
- `sim/web/_headers` does **not** apply to `/api/*` responses. API security headers are set in code
  ([`envelope.js`](../../functions/api/_lib/envelope.js)).
- Every branch push publishes a public preview, which is the easiest place to test a change.

## 6. Which mode am I in?

```sh
curl -s https://YOUR-DOMAIN/api/health
```

| `mode` | Meaning |
|---|---|
| `live` | Configured; visitors get a real brain. |
| `busy` | At the concurrency ceiling. |
| `degraded` | Not configured, switched off, over budget, or upstream down; `reason` says which. |
| `offline` | No API at all (plain static hosting); behaves like the static demo. |

**Health reads configuration only and never calls the gateway**, so it is free to poll but cannot see
an upstream outage: it can say `live` while chat fails with `upstream_down`. To test the brain, spend a
real request (one of the visitor's five per minute):

```sh
curl -s -X POST https://YOUR-DOMAIN/api/chat \
  -H 'content-type: application/json' -H 'origin: https://YOUR-DOMAIN' \
  -d '{"text":"hello"}'
```

The field is `text` (an OpenAI `messages` array is ignored and gives `too_short`), and the `origin`
header is required (otherwise `forbidden_origin`).

`node sim/check_deployed.mjs <url>` checks a deployment in a phone-sized browser without spending
anything; `node sim/check_hosted_mic.mjs` exercises the microphone path and does spend.

## Known gaps

- Moxie says when the cloud goes quiet, but not when it comes back.
- Typed text has no pre-rendered clip, so in degraded mode it uses the browser's own voice.

---
[Live Sim design](../architecture/backlog/live-sim-demo.md) · [The static site](../architecture/static-experience.md) · [Guides](README.md)
