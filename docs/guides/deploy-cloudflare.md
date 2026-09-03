# ☁️ Deploying the Moxie Sim to Cloudflare Pages

> **What this is.** How to publish the [Moxie simulator](../../sim/web/) on Cloudflare Pages —
> **twice over**: as a static demo that needs no configuration at all, and as a *live* demo where a
> visitor talks to a real brain in Moxie's real voice. Robot behaviour is grounded in firmware
> **v3.6.4-Zephyr / OTA v24.10.803**.

## The one thing to understand first

There are **two deployments in this repo, and the difference is configuration, not code**.

| | Static demo | Live demo |
|---|---|---|
| Configuration | **none** | three values (§3) |
| Brain | the scripted stub ([`stub.js`](../../sim/web/stub.js)) | your gateway |
| Voice | 30 pre-rendered clips ([`audio/index.json`](../../sim/web/audio/index.json)) | your gateway, clips as fallback |
| Ears | a scripted child line | your gateway |
| Cost | zero | metered, and capped (§4) |

**With nothing set, you get the static demo** — and that is the safe default, not a failure state.
Every preview deployment is in it permanently, because the secrets live on Production only. The page
says which one it is in, out loud, and so does `/api/health` (§6).

## 1. Deploy it

`sim/web/` is a pre-built static bundle: no build step, all dependencies vendored (three.js, MQTT.js,
marked, mermaid, highlight.js, qrcode, Inter + JetBrains Mono), and the docs bundle is committed.

**Measured 2026-09-03, not estimated:** **16 MB across 256 files**, the largest being
`vendor/mermaid.min.js` at **3.2 MB** — comfortably under Cloudflare's 25 MB per-file limit. (Two
earlier figures in this guide, "1.9 MB" and "8 MB, ~100 files", were both stale; the docs bundle and
18 new voice clips have landed since.)

### Point Cloudflare Pages at the repo

[`wrangler.toml`](../../wrangler.toml):11-12 already declares it:

```toml
name = "moxie"
pages_build_output_dir = "sim/web"
```

In the dashboard: **Build command = empty**, **Framework preset = None**, **Output directory =
`sim/web`**. The Cloudflare GitHub App owns the deploy — **no workflow in this repo deploys the
site**, which is why you will not find one in `.github/workflows/`.

### CLI alternative

```sh
npx wrangler pages deploy sim/web --project-name moxie
```

## 2. What the static demo actually does

Every row below was re-derived from the code on 2026-09-03. **Two rows in the previous version of
this guide were wrong**; they are marked.

| Feature | Static? | Why |
|---|---|:--|
| 3D Moxie, rig, liveness, expressions, HUD | ✅ | pure client-side WebGL |
| **Play demo** (canned replay) | ✅ | replays `sessions/demo.json`, no server |
| Hand controls (motors, face, LED, light) | ✅ | direct `window.moxie` calls |
| **Conversation** | ✅ | the stub brain emits real behaviour markup ([`stub.js`](../../sim/web/stub.js)) |
| **Moxie's voice** | ✅ | 30 pre-rendered clips, including all 9 stub replies, the 8 "thinking" lines and the degraded line |
| **Ambient self-talk** | ✅ | 56 clips + 457 lines, client-side by design |
| **Child's voice (audible)** | ❌ **was claimed, is false** | `audio.js`:160 `speak(text, who)` accepts a `who`, and the manifest holds 2 child clips — but **no caller ever passes `"child"`** (`bridge.js`:300,306; `ambient.js`:106 pass nothing or `"ambient"`). The clips are unreachable. |
| Mic button | ✅ degrades | falls back to a scripted child line. **The old "no STT model" reason is stale** — there is a transcription route now (§3), it is simply not configured in a static deploy |
| **Revival QR** | ✅ | payloads are plain JSON, built client-side |
| **Setup page**, **Docs explorer** | ✅ | client-side; reads the committed docs bundle |

## 3. Make it live: the minimum configuration

Three values, and the code is the authority — [`functions/api/_lib/env.js`](../../functions/api/_lib/env.js):84-86
lists exactly these as required:

| Variable | Kind | Notes |
|---|---|---|
| `DEMO_GATEWAY_BASE_URL` | var | any OpenAI-compatible base, e.g. `https://your-gateway.example/v1`. **No default** |
| `DEMO_GATEWAY_API_KEY` | **secret** | read only as `context.env.DEMO_GATEWAY_API_KEY`, inside the Function |
| `DEMO_CHAT_MODEL` | var | e.g. `gpt-4o-mini`. **No default** — a wrong id costs a failed *paid* request |

**Unset means degraded, never "guess a gateway."** That is deliberate: `env.js`:252-254 default all
three to `""`, so an unconfigured deployment is inert rather than pointed at somebody else's server.

Then, optionally:

| Variable | Kind | Gives you |
|---|---|---|
| `DEMO_TTS_MODEL` | var | Moxie's voice from the gateway. Unset ⇒ `voice: false`, clips only |
| `DEMO_STT_MODEL` | var | ears. Unset ⇒ `ears: false` and the mic keeps its scripted fallback |
| `DEMO_GATEWAY_ACCESS_CLIENT_ID` + `..._SECRET` | var + **secret** | only if your gateway sits behind a **Cloudflare Access**-protected tunnel. Set **both or neither**; one alone is refused as misconfiguration rather than sent half-credentialled |
| `DEMO_ENABLED` | var | the kill switch. `0` forces degraded **without deleting the secret** — the fastest incident response there is |

### Set them on Production only

This is what keeps every preview deployment keyless, and therefore safe: a branch preview inherits no
secret, answers `gateway_not_configured`, and serves the static demo.

### Before you paste a key

- **Use a separate, budget-scoped key for the public demo** — not the one your local stack and live
  tests use. If your gateway can mint a virtual key with a hard budget and rate limits, do that: it
  binds even if our code is wrong, which no amount of application-level care can promise.
- The browser never receives the key **or the gateway's address**. `health.js`:29-31 records that
  those and every model id are *structurally absent* from the response — never copied in, rather than
  filtered out afterwards.

## 4. The caps, and why they exist

A public demo that proxies a paid gateway is an open invoice unless it is bounded. All of these are
`DEMO_*` variables with the defaults below, from `env.js`:29-56:

| Control | Default | |
|---|--:|---|
| `DEMO_MAX_TOKENS` | 160 | the ceiling on the expensive half of a completion |
| `DEMO_MAX_INPUT_CHARS` | 500 | a child's utterance; longer is **rejected**, not truncated |
| `DEMO_MAX_TTS_CHARS` | 300 | ~3 sentences of speech |
| `DEMO_MAX_RECORD_MS` | 15000 | the honest ceiling on a recording — the byte cap alone is not one for compressed audio |
| `DEMO_MAX_AUDIO_BYTES` / `_MIN_` | 500000 / 2000 | below the floor, **no upstream call at all** |
| `DEMO_CHAT_PER_MIN` / `_HOUR` / `_DAY` | 5 / 40 / 150 | per visitor IP |
| `DEMO_SPEECH_PER_MIN` / `_HOUR` | 10 / 80 | |
| `DEMO_STT_PER_MIN` / `_HOUR` | 10 / 60 | |
| `DEMO_MAX_CONCURRENT_CHAT` / `_SPEECH` | 4 / 8 | concurrency, not token count, is what makes a demo feel dead under load |
| `DEMO_UNIT_BUDGET_HOUR` / `_DAY` | 600 / 4000 | denominated in **request units** (chat 3, speech 2, transcribe 2), because no price sheet exists in this repo to convert to money honestly |
| `DEMO_CHAT_TIMEOUT_MS` | 20000 | deliberately **below** the measured worst case: a fast honest degrade beats a slow success |
| `DEMO_TICKET_TTL_S` | 60 | a speech ticket's life — long enough for a slow client, short enough that a leaked one is worthless |

**Honest about the ceiling:** these counters are **best-effort, in-process**. A Worker isolate is not
a shared counter, so under real concurrency the true limits are the per-request caps, the ticket's
structural property, and a budget-scoped key at your gateway. Exact counters need durable storage and
are not built.

## 5. What a real deploy settled — and what is still open

**Settled 2026-09-03 on a branch preview, which any pull request publishes automatically.** These
were the document's open questions; two of the three are now answered, and you can re-check them
yourself on any PR's preview URL with the `curl` in §4.

> **Does Pages route `functions/` from the repo root when the build output directory is `sim/web`?**
> **Yes.** `GET /api/health` on a branch preview returned HTTP 200 `application/json` with
> `{"reason":"gateway_not_configured","mode":"degraded"}`. This was the highest-risk unknown — the
> failure mode would have been a silently 404-serving static site — and it is closed.

> **Is `functions/api/_lib/` exposed?** **No.** `GET /api/_lib/env.js` returns the site's static HTML
> fallback, not module source and not a route. Note it answers **200 with HTML**, not 404, so anything
> testing for "route missing" must check the content type rather than the status code.

> **Does `sim/web/_headers` apply to an `/api/*` response?** **No — and this one mattered.** The same
> preview served `/sim.html` with the `/*` block's `Referrer-Policy` (so `_headers` works) and served
> `/api/health` with **no `Referrer-Policy` at all**. The only headers a Function carries are the ones
> `functions/api/_lib/envelope.js` sets in code. The `/api/*` block in `_headers` is kept as
> documentation but has **no effect**; `Referrer-Policy` was moved into `envelope.js`, and a test now
> fails if the two ever disagree. **If you add a security header for the API, add it in the code.**

Still unverified from inside the repo, and each needs your dashboard or a deliberate experiment: the
Functions wall-clock and request-body limits on your plan; the free-tier request allowance; and
whether Production and Preview variables are truly separate — the preview is keyless *today*, but so
is Production, so that is not yet a proof. Check it with one `curl` of a preview **after** you set
Production-only variables, because **every branch push publishes a public preview**.

## 6. After deploying: which mode am I in?

The page says so — a badge and a pill. To check from a terminal:

```sh
curl -s https://YOUR-DOMAIN/api/health
```

`/api/health` **never calls the gateway** (`health.js`:14-15) — the mode is derived from configuration
alone, so probing is free. What comes back:

| `mode` | Means |
|---|---|
| `live` | configured and working; a visitor gets a real brain |
| `busy` | at the concurrency ceiling; the page says so and degrades gracefully |
| `degraded` | configured but unusable right now (over budget, upstream down, or `DEMO_ENABLED=0`), **or** not configured at all — `reason` says which |
| `offline` | the route itself is absent (404, a plain CDN, `file://`) — behaviour and copy byte-identical to the static demo |

`voice` and `ears` are booleans only: whether a TTS/STT model is configured, never which one.

## 7. What still does not work

- **The child's voice is mute** (§2). Its clips exist and are unreachable.
- **No spoken recovery line.** Moxie says the cloud went quiet when it does; she does not announce
  when it comes back.
- **Freely typed text has no pre-rendered clip**, by definition — in degraded mode it falls to the
  browser voice.
- **The microphone-to-gateway join has never run end to end.** Each half is verified against the
  other — the browser's WAV encoder parsed by the server's own RIFF walker, its header matching a clip
  that transcribed live — but **no automated test may open a microphone**. One person, one browser,
  one sentence settles it.
- **Nothing here is verified on a real Pages deployment.** Every claim above is from the code or from
  a local run.

---
📖 [The live-Sim spec](../architecture/backlog/live-sim-demo.md) · [The static experience](../architecture/static-experience.md) · [Guides](README.md)
