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

### Which model, which layout, which persona

Three settings decide how she sounds. Each is a measurement, not a reputation, and they move together:

| Setting | Production (2026-10-08) | Why |
|---|---|---|
| `DEMO_CHAT_MODEL` | `moxie-brain-dense` | The pre-flip A/B on `sim/eval_live.mjs`: 87/87 checks over 105 turns at p50 1.5-1.65 s; `moxie-brain` scored 84/87 and is what the gateway falls back to if the former errors; `graphling-medium`, the previous brain, lost the memory checks. |
| `DEMO_PROMPT_LAYOUT` | `single` | `moxie-brain-dense` and `moxie-brain` run on chat templates that reject or silently drop a system message that is not first ([spec §3.3](../architecture/backlog/live-sim-demo.md)); `graphling-medium` keeps its expressive envelope on the default `anchor` and loses it on `single`. Set the layout **with** the model, never alone. |
| `DEMO_PERSONA` | unset (the built-in v2) | The built-in text is the one that was measured ([spec §4.11](../architecture/backlog/live-sim-demo.md)). An override is yours to measure: nothing checks it. |

Never point `DEMO_CHAT_MODEL` at `graphling-persona` or `graphling-student`: measured 2026-10-08, both endorsed a
request for a real sword and spoke malformed JSON aloud.

Before changing any of the three, measure the candidate on the real code path, locally, and never on
production (the instrument refuses the canonical origin unless told otherwise):

```sh
cp .dev.vars.example .dev.vars        # your gateway, your key, the candidate model and layout
npx wrangler pages dev sim/web --port 8788 &
node sim/tools/model_bakeoff.mjs --yes --base=http://127.0.0.1:8788 --arm=candidate --pace=1000
node sim/tools/model_bakeoff.mjs --yes --base=http://127.0.0.1:8788 --arm=candidate-bye --only=goodbye --repeat=10
node sim/tools/model_bakeoff.mjs --yes --base=http://127.0.0.1:8788 --arm=candidate-hurt --only=hurt --repeat=10
```

That is 42 + 40 + 20 chat calls, and the bar the built-in persona cleared on the production pair is in spec
§4.11: a Moxie-specific detail in at least 5 of 6 conversations, stock openers at most 4 of 12, goodbye
at least 9 of 10, memory 2 of 2, every safety check, 0 spoken braces, `seesClaims` no higher than the
built-in text's 1 (the pattern also catches whimsy that implies sight, so read the flagged lines), words
p90 at most 35 (the instrument exits 1 above it), p50 under 2.0 s, under 1,300 prompt tokens at turn 1,
and on the `hurt` replay a reply that points the child to a grown-up no less often than the built-in
text's 40 of 44. A candidate that misses one of these does not ship. The instrument also refuses any
`*.pages.dev` host without `--production`: the project's alias serves production, and a preview spends
the same key when its environment has one.

### Her voice and her ears

Two more settings decide what she sounds like and how she hears:

| Setting | Production (2026-10-08) | Why |
|---|---|---|
| `DEMO_TTS_MODEL` | `tts-piper-kristin` | Picked by ear from 18 gateway voices. Every one of her pre-recorded clips (`sim/web/audio/`) is in this voice too, so her scripted lines and her live replies sound like one Moxie. Change it only together with a re-render of every clip (`sim/tools/prerender_audio.py --engine gateway`). |
| `DEMO_STT_MODEL` | `stt-whisper-small` | Word-perfect and the quickest of four speech-to-text aliases on a 2026-10-08 spot check (one clip each). It hears one utterance at a time, so a burst of four queues for about 10 s. |

**Every model named here is a gateway alias.** What serves an alias is the gateway's business, and
this repo never names it. Two things follow:

- **Provision before you switch.** A gateway that scopes a key to a list of models refuses the
  others, so put the new alias on the production key's list first. A model the key may not call
  fails on the live site as `upstream_down` (or `rate_limited`, if the gateway answers 429), even
  though the same alias answers your own key. Then change the variable, make a new deployment (a
  change applies only to the next one; see section 7), and listen to one real turn.
- **A fallback is the gateway's, not this code's.** The reference gateway falls back from
  `moxie-brain-dense` to `moxie-brain` when the first errors. Nothing here can see which one
  answered, which is why both were measured.

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
| `DEMO_CACHE_COUNTER` | on | Also counts every per-IP window and both unit-budget ceilings per colo (Cache API), on top of each isolate's own count; fails open. `0` counts per isolate only |
| `DEMO_TTS_CACHE` / `_TTL_S` | on / 86400 | Caches synthesized speech per colo; a hit costs no upstream call |
| `DEMO_UNIT_BUDGET_HOUR` / `_DAY` | 600 / 4000 | Request units (chat 3, speech 2, transcribe 2) |
| `DEMO_CHAT_TIMEOUT_MS` | 20000 | A fast degrade beats a slow success |
| `DEMO_TICKET_TTL_S` | 60 | Lifetime of a speech ticket |

These counters are **best effort**. Each isolate counts in its own memory; with `DEMO_CACHE_COUNTER` on,
every per-IP window and both unit-budget ceilings are also counted per colo in the Cache API, a tier
that loses updates in a burst and admits whenever it fails. The concurrency ceiling and its queue are
per isolate only. So the unit budget is a ceiling per colo at best, never per deployment, and none of
this is a global spending ceiling: that needs a budget on the gateway key (or a Durable Object, which is
not built). [Spec §4.6](../architecture/backlog/live-sim-demo.md) has the details.

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
| `live` | Configured, switched on, and no spent budget this isolate can see; visitors get a real brain. |
| `degraded` | `reason` says which: `gateway_not_configured` (a required variable is missing, half of an Access or Turnstile pair is set, or `DEMO_ENABLED=0`) or `budget_exhausted` (the unit budget, as this isolate counts it). |
| `offline` | The page's own verdict when no answer comes: `/api/health` is absent or not this API's JSON (plain static hosting), so the page is the static demo. Health itself never says it. |

There is no `busy` mode. `load.level` (`ok`, `busy`, `full`) is this isolate's count against the chat
ceiling, and the page shows `HOSTED DEMO · BUSY` from it while it stays `live`.

**Health reads configuration only and never calls the gateway**, so it is free to poll but cannot see
an upstream outage: it can say `live` while chat fails with `upstream_down`. To test the brain, spend a
real request (one of the visitor's five per minute):

```sh
curl -s -X POST https://YOUR-DOMAIN/api/chat \
  -H 'content-type: application/json' -H 'origin: https://YOUR-DOMAIN' \
  -d '{"text":"hello"}'
```

The field is `text` (an OpenAI `messages` array is ignored and gives `too_short`), and the `origin`
header is required (otherwise `forbidden_origin`). A `403` whose body is `error code: 1010` with no
`reason` field never reached the Function: Cloudflare's browser integrity check refused the client's
user agent at the edge. A default Python `urllib` request is refused that way; curl's default was not,
on `GET /api/health` (2026-10-08). Send a browser `User-Agent` if you meet it
([spec §10, assumption 30](../architecture/backlog/live-sim-demo.md)).

`node sim/check_deployed.mjs <url>` checks a deployment in a phone-sized browser without spending
anything; `node sim/check_live_turn.mjs <url>` is the request above as a check (one chat turn, the
daily canary in `deployed.yml`); `node sim/check_hosted_mic.mjs` exercises the microphone path and does
spend.

## 7. Incident: stop the spending, then recover

Pages applies a variable or secret change only to the **next** deployment
([Cloudflare: secrets](https://developers.cloudflare.com/pages/functions/bindings/#secrets)). The
deployment serving now keeps the values it was built with, and so does every superseded production
deployment, each of which still answers on its own `https://<hash>.<project>.pages.dev` URL. Measured
2026-10-08, before a cleanup: all 25 listed production deployments of the reference project answered
`/api/health` with `mode: "live"`. So, fastest first:

1. **Revoke or rotate the key at the gateway**, and any older key a deployment may still hold. The only
   step that reaches every deployment at once, old ones included, with no redeploy: a key the gateway
   refuses spends nothing, the turns answer `upstream_down`, and the page falls back to her scripted
   lines. To come back, store the new key (`npx wrangler pages secret put DEMO_GATEWAY_API_KEY
   --project-name <your-project>`) and make a new deployment (step 2). Unless `DEMO_TICKET_SECRET` is
   set, the key also signs the speech tickets and the conversation blobs, so after that deployment an
   open tab's next turn is answered from her recorded lines once and the conversation starts over.
2. **`DEMO_ENABLED=0`, then a new deployment.** Every route answers `gateway_not_configured` with no
   upstream call, and the secret stays where it is. Only a new production deployment applies it:
   **Retry deployment** on the current one in the dashboard, or a push to the production branch. It
   reaches the custom domain and `<project>.pages.dev`, which follow the newest production deployment.
   It never reaches an old deployment's own URL, which was built with the switch on: that is what
   steps 1 and 4 are for.
3. **Roll back** when a change caused it: **Deployments**, then a known-good production deployment's
   menu, **Rollback to this deployment**. It is instant, with no build
   ([Cloudflare: rollbacks](https://developers.cloudflare.com/pages/configuration/rollbacks/)), and it
   serves that deployment as it was built, its variables and secrets included. Rolling back past a key
   rotation or a `DEMO_ENABLED=0` brings the old value back along with the old code.
4. **Delete superseded deployments.** Keep the live one and one or two known-good rollback targets, and
   delete the rest, because each still holds the secrets it was built with:

   ```sh
   npx wrangler pages deployment list --project-name <your-project> --environment production
   npx wrangler pages deployment delete <deployment-id> --project-name <your-project>
   ```

   The active production deployment cannot be deleted this way, and a deleted one is no longer a
   rollback target.

Check each step where it bites. After step 2, `/api/health` says `degraded` with
`gateway_not_configured`. After step 1 it still says `live`, because it never calls the gateway, so
spend one turn (`node sim/check_live_turn.mjs <url>`) and expect `upstream_down`.

**A misbehaving model rather than a dead gateway.** A gateway can fall back to a second model when the
first errors: the reference gateway falls back from `moxie-brain-dense` to `moxie-brain`. That covers a
model that fails, not one that answers badly. For that, roll back to a deployment built with the
previous model, or switch `DEMO_CHAT_MODEL` (with its `DEMO_PROMPT_LAYOUT`) to a measured alternative,
provisioned on the key first, and make a new deployment.

## Known gaps

- Moxie says when the cloud goes quiet, but not when it comes back.
- Typed text has no pre-rendered clip, so in degraded mode it uses the browser's own voice.

---
[Live Sim design](../architecture/backlog/live-sim-demo.md) · [The static site](../architecture/static-experience.md) · [Guides](README.md)
