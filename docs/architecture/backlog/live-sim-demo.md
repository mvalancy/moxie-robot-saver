# Live Sim demo: the hosted Sim with a real brain, voice and ears

**Status:** shipped. The routes are [`functions/api/`](../../../functions/api/) (`health.js`, `chat.js`,
`speech.js`, `transcribe.js`, helpers in `_lib/`). The client is `sim/web/mode.js`,
`cloud-transport.js`, `mic.js` and `turnstile.js`. Tested by `sim/test_demo_proxy.mjs`,
`test_demo_tickets.mjs`, `test_demo_ears.mjs`, `test_turnstile.mjs`, `test_mode.mjs` and the rest of §8.1.
The items still open are in §9.

This is the security and spend spec for the public demo. The code cites it by section number, so
the numbering is stable. When this page and the code disagree, the code wins. Fix this page.

## 1. What it is

Three same-origin Cloudflare Pages Functions turn a typed or spoken sentence into the two JSON payloads
`bridge.js` `route()` already renders: a `remote_chat` reply and a `CloudTTSResponse`. The avatar then
answers in the gateway voice with face, gestures and lip-sync. `bridge.js` and `audio.js` are not
modified. Around this sit caps that stop a stranger from spending more than a bounded amount. There is
also an honest fallback to the scripted Sim.

> **Definition of done:** a stranger opens the production domain, types or speaks a sentence, and Moxie
> answers in her gateway voice. The browser never holds the gateway key. No visitor can spend more than a
> capped number of request units. When the gateway is unconfigured, over budget, at capacity or down, the
> page degrades to the pre-cached scripted Moxie and says so on screen.

| # | Binding constraint | How it is met |
|---|---|---|
| C1 | The repo is public. | Every secret is a Pages environment binding read as `context.env.*`. `wrangler.toml` has no `[vars]`. §8.1 test 9 fails the build on a key, gateway host or account id under `functions/` or `sim/web/`. |
| C2 | The site is a static Pages site. | All server logic is Pages Functions on the same origin (`/api/*`). The Cloudflare GitHub App still owns the deploy. |
| C3 | No hard-coded gateway or site hostname. | Both are deployment config (§5). The origin allowlist defaults to the request's own origin, so a fork works on any domain. An unset gateway means degraded. The code never falls back to guessing ours. |
| C4 | Demo mode: a visitor can break nothing. | Three POST routes and one GET. None writes durable state (§4.4). |
| C5 | Degradation is mandatory. | With no variables set, every route answers `gateway_not_configured` and the page is the static demo. So a keyless branch preview is safe. |
| C6 | Be honest about what cannot run here. | §2.6. |

## 2. The contracts the edge mirrors

### 2.1 The Sim's seams

`route(topic, payloadString)` in `bridge.js` is the single ingress. It takes a JSON **string**, and
live MQTT and session replay both feed it. `handleTts` latches `cloudVoice`, after which `speakLocally`
does nothing. `speakLocally` speaks **immediately** when no MQTT client is connected. §3.4 exists because
of that. `window.moxieBridge` exposes seven members, which tests pin.

### 2.2 The cloud/turn wire contract

- **Chat reply** = `wire.build_chat_response` (`mqtt/moxie_sdk/wire.py`):
  `{command:"remote_chat", result:<ResultCode NAME>, backend, event_id, output:{text, markup}, end_turn}`.
  `chunk_num` and `consistency_control` are **omitted** on a single-chunk turn. **No `emotion` field**
  is ever emitted, because the mood mark in `markup` carries the face. The Sim ignores `result`.
  `end_turn` is `true` on a goodbye turn (§4.10) and `false` otherwise; the goodbye's markup also
  carries the `Bht_Sign_off` tree mark, byte-identical to `vocab.tree_mark("Gesture_None", "Bht_Sign_off")`.
- **TTS reply** = `build_cloud_tts_response`:
  `{request_source:"ROBOT_TTS_REQUEST", audio:{buffer:<base64 raw LE s16 PCM>, channels, sample_rate}, marks:[], event_id, chunk_num}`.
  The buffer is raw PCM, not a container. An empty `marks` still lip-syncs, because the mouth follows
  the audio envelope.
- **Sniff the bytes, never the Content-Type.** The gateway labels a valid RIFF/WAVE body `audio/mpeg`.
  `_lib/wav.js` walks RIFF chunks, requires 16-bit, and carries the **header's own** rate and channels
  out. A headerless body is accepted only under `DEMO_TTS_FORMAT=pcm`, at `DEMO_TTS_SAMPLE_RATE`.
  A JSON or HTML body, an 8- or 24-bit WAV, or an empty body becomes `upstream_down`. It never
  reaches the visitor as noise.

### 2.3 The deploy

The Pages project is `moxie-robot-saver`, deployed by the Cloudflare GitHub App. Every branch push
publishes a public preview at `https://<branch>.moxie-robot-saver.pages.dev`. `functions/` at the
**repo root** is routed even though `pages_build_output_dir = "sim/web"` (assumption 8). `functions/api/_lib/`
is not routable (assumption 9). `sim/web/_headers` does **not** apply to Function responses
(assumption 27), so §4.7.1 sets `/api/*` headers in code.

### 2.4 Fallback assets

All of these exist and work without a server: `stub.js` (matchers and fallbacks that emit real markup),
the pre-rendered clips in `sim/web/audio/index.json` keyed by exact utterance, `ambient.json` plus
`ambient.js`, and `sessions/demo.json`. Every line a degraded page can say must have a clip.
`sim/test_fallback_coverage.mjs` enforces that (§6.2).

### 2.5 The abuse surface

- **Chat volume** (18–45 s of gateway time per completion) is met by the per-IP windows, the
  concurrency ceiling and the unit budget.
- **TTS text**, the priciest per-request vector, is met by the speech ticket (§3.2).
- **STT uploads**, billed by duration, are met by byte caps, WAV-only input and a server-side
  duration cap.
- **Model or parameter substitution**, and use as a free general-purpose LLM, are met by the
  server-built body (§4.1).
- **One key covering brain, voice and ears** is met by a separate budget-scoped key (§4.2).

The supervisor, the parent-app server and `mqtt/status_proxy.py` are never part of this deploy (§4.4).

### 2.6 What cannot run on a static host

| Cannot run | Hosted substitute |
|---|---|
| MQTT broker | Same-origin HTTP request/response. The Sim's MQTT panel stays as a peer transport for self-hosters. |
| Python supervisor and permit gate | Nothing. The Functions are a demo brain, not a supervisor: no permits, fleet, telehealth or motors. |
| Durable store | **Nothing persists.** Context lives in a signed blob the browser holds (§3.3) and dies with the tab. |
| Safety journal and parent review | **Pre-inference blocking only, with no record kept.** A blocked turn spends nothing. `flag`-level categories are allowed through. |
| `automarkup.annotate` (Python) | A **minimal markup floor** in JS (`_lib/wire.js::markupFloor`) built from the mark templates `stub.js` uses. Each field is validated against a closed table. |

## 3. The architecture

### 3.1 The picture

```mermaid
flowchart LR
  tr["cloud-transport.js"] -->|POST text| c["/api/chat"]
  tr -->|POST ticket| s["/api/speech"]
  mic["mic.js"] -->|POST WAV| t["/api/transcribe"]
  mode["mode.js"] -->|poll| h["/api/health"]
  c & s & t --> lib["_lib: caps, tickets, budget, wire, WAV"]
  lib -->|server-built body| gw["OpenAI-compatible gateway"]
  tr -->|route topic,payload| br["bridge.js (unchanged)"]
  mode -->|degraded / offline| st["stub.js + clips"]
```

### 3.2 The routes and the envelope

The routes are ESM Pages Functions. Each answers `Cache-Control: no-store`. **None sends
`Access-Control-Allow-Origin`.** Every response, success or failure, uses one envelope built by
`_lib/envelope.js` from a fixed key allowlist (`PUBLIC_KEYS`):

`ok, degraded, reason, retry_after_s, message, mode, load{level,inflight,capacity}, limits, turnstile,
messages[{topic,payload}], speech[{ticket,event_id,chunk_num}], context, transcript, voice, ears, diagram, cited`

`limits` holds exactly `max_input_chars, max_tts_chars, max_tokens, chat_per_min, max_record_ms,
max_audio_bytes, min_audio_bytes` (`PUBLIC_LIMIT_KEYS`). `turnstile` is the public sitekey, or `""`
when the check is not enforced. `diagram` (≤ 1200 chars of mermaid source) and `cited`
(`"<title>|<path>"`, ≤ 300 chars) come from the docs lookup in `_lib/docsearch.js`, which runs server-side.

**`reason` is a closed set** (`REASONS`): `null` · `rate_limited` · `at_capacity` · `budget_exhausted` ·
`upstream_down` · `gateway_unreachable_or_gated` · `gateway_not_configured` · `timeout` · `bad_request` ·
`too_long` · `too_short` · `bad_ticket` · `blocked` · `forbidden_origin` · `turnstile_failed` ·
`turnstile_misconfigured`. An unknown value is coerced to `bad_request` on the server. `mode.js` maps
unknown values to `null`. Upstream status codes, bodies and headers are **never** forwarded, because
they can echo model names, org ids and key prefixes. `message` is sanitized: URLs and `sk-`-style keys
are stripped, and it is capped at 200 chars.

**`GET /api/health`** makes **no gateway call, ever**. It is synchronous and always 200, so any non-200
means the route is absent, which `mode.js` reads as `offline`. It has no origin pin. `mode` is `live`
only when all of these hold: `DEMO_GATEWAY_BASE_URL`, `DEMO_GATEWAY_API_KEY` and `DEMO_CHAT_MODEL` are
set, `DEMO_ENABLED` is on, no half-configured Access or Turnstile pair exists, and this isolate sees no
spent budget. It reports `voice`/`ears`, which say whether a model is configured, never which one.
`budget` and `load` are **this isolate's view only** (§4.6).

**`POST /api/chat`** reads exactly two keys, `text` and `context`, plus the Turnstile token field
`cf-turnstile-response`. Anything else (`model`, `max_tokens`, `messages`, `system`, `tools`...) is
**ignored, not validated**. The order is fixed:

1. config gate
2. `admit()`, which charges before the body is parsed
3. body parse
4. input caps: empty is `too_short`, over `DEMO_MAX_INPUT_CHARS` is `too_long`; text is rejected, not truncated
5. context check
6. safety floor
7. Turnstile
8. gateway call, plus the §4.9 re-roll
9. reply
10. speech ticket (only when `voice` is on) and the next context blob

The reply's `messages[0].payload` is a JSON **string** with §2.2's field set, on
`/devices/<DEMO_DEVICE_ID>/commands/remote_chat`. An empty completion is `upstream_down`: never a 200
with empty text.

**`POST /api/speech`** accepts `{ticket}` and nothing else. **It has no text field.** A ticket is
`v1.<base64url(JSON {t, e, c, x})>.<base64url(HMAC-SHA-256)>` minted by `chat.js` (`_lib/hmac.js`):
`t` is **one chunk** of our reply, `e`/`c` are the event id and chunk, and `x` is the expiry
`now + DEMO_TICKET_TTL_S` (60 s). A reply is minted as **one ticket per sentence**, chunk 0 first
(`_lib/hmac.js::splitForSpeech` / `mintTickets`): at most `MAX_SPEECH_CHUNKS` (3) tickets, each at most
`DEMO_MAX_TTS_CHARS`, a chunk under 24 characters merged with its neighbour (a tiny chunk starts the
voice no sooner: synthesis time is mostly overhead), a sentence longer than the cap cut at a space, never
inside a word, and nothing splits inside a number, after an abbreviation or an initial, or inside a
mermaid fence. Joined with one space the chunks are the reply up to the cap. Until 2026-10-08 the one
ticket was the reply cut at 300 characters, mid-word ("…it rains, lo" was spoken for a 311-char reply). Redemption checks the signature first, before parsing the
payload, using a constant-time compare. It then checks expiry and re-checks the char cap. An over-cap
ticket answers `too_long`; forged, malformed or expired answers `bad_ticket`. A per-isolate spent-set
(2000 entries) refuses a replayed ticket as `bad_ticket`. As a result `/api/speech` can only
synthesize text this deployment wrote in the last minute, so it cannot become a free TTS API. The
audio cache (§4.8) sits after all of these checks.

**`POST /api/transcribe`** takes the raw audio bytes as the body. The Turnstile token comes on the
`X-Turnstile-Response` header. The route re-posts the body as a multipart `file` to
`/audio/transcriptions` with a server-fixed model and nothing else. Checks, in order:

1. byte floor (`DEMO_MIN_AUDIO_BYTES`, refused as `too_short`) and byte ceiling
2. the sniffed container must be in `DEMO_STT_FORMATS` (default `wav`), else 400 `bad_request` with no call
3. a RIFF/WAVE duration over `DEMO_MAX_RECORD_MS` is `too_long`
4. Turnstile

The answer is the house envelope with `transcript`, not a Deepgram body. The envelope carries the
reason, so `mic.js` can degrade honestly. `transcribe.js::reasonForUpstreamStatus` maps upstream
statuses:

- 429 → `rate_limited`
- 413 → `too_long`
- 401, 403 or 407 → `upstream_down`, because it is an operator problem
- any other 4xx → `bad_request`, per-turn, and the page does not degrade
- 3xx → `gateway_unreachable_or_gated`
- 5xx → `upstream_down`

### 3.3 The context blob

`chat.js` returns `context`, a signed blob under its own HKDF label (`CONTEXT_INFO`). It holds the last
`DEMO_MAX_HISTORY_TURNS` (**12**) `{role, content}` messages. The roles are `user` and `assistant` only,
and each message is capped at `DEMO_MAX_INPUT_CHARS`. Total content is capped at
`DEMO_MAX_CONTEXT_CHARS` (**4000**), dropping the oldest first. The character cap is the real bound on
the prompt. The blob is re-minted each turn and expires after `CONTEXT_TTL_S` = 3600 s.

- A forged or tampered blob is `bad_request`. Because assistant turns are signed, a visitor cannot
  forge Moxie's side of the history.
- An **expired** blob is served with the history dropped. It is not refused, because refusing wedged a
  tab left open for an hour.
- **The persona is sent once, first; our anchor is last** (`DEMO_PROMPT_LAYOUT=anchor`, the default).
  The anchor after the child's line is short: a restatement of the rules a visitor's text could try to
  talk her out of, the move for this turn (§4.10) and the format rule, so the last instruction the model
  reads is still ours. Until 2026-10-08 this rule read "first **and** last" and the whole 2,889-char
  persona was repeated after the child's line. Measured, that put 94 % of the prompt behind a 15-char
  "Okay bye Moxie!" and she answered an earlier turn instead: 0/4 goodbyes acknowledged on production,
  0/5 in replay on the same model, 4/5 with the repeat removed, 5/5 with the goodbye cue. The security
  intent is unchanged (owner-approved wording change); `sim/test_demo_proxy.mjs` §21 pins it. The
  anchor is sized to a token budget: 1,837 chars on a goodbye turn and at most 2,000 on any other
  (§21 pins 1,900 and 2,050), which put turn 5 of the five-turn conversation ending "Okay bye Moxie!"
  at 1,282 and 1,304 prompt tokens on the production model (1,889 with the repeated persona; 1,350
  with the first, 2,164-char anchor). Growing the anchor means measuring that again.
- **`single`** sends exactly **one** system message, first, carrying — in this order — the persona, the
  anchor's restatement, the cue, the reference passage, the diagram cue, the format rule and the re-roll
  line; the child's line is the **last** message. It exists because some chat templates (Qwen3-family
  templates under llama.cpp, for one) answer HTTP 500 "System message must be at the beginning" to a
  system message that is not first, or drop it silently. **What `single` gives up:** the last text the
  model reads is the visitor's, and the restatement sits early in the one system message (straight
  after the persona; the passage, the diagram cue, the format rule and the re-roll line all come after
  it). **What defends it instead:** the pre-inference safety floor, which runs before any call and is
  unchanged; the server-built body, so a visitor can add words but never a message or a role; and the
  measurement gate: a model is pointed at this layout only after `sim/eval_live.mjs
  --only=safety,injection` passes on it. `single` also weakens the move rotation (§4.10), because the
  cue is read before the whole conversation rather than straight after the child's line: measured
  2026-10-08, a seven-turn `loop` under `single` used 2 of the 3 moves with a longest run of 4, against
  3 of 3 and a longest run of 2 under `anchor` on the production model.
- No layout except `anchor` emits a system message that is not first. An unknown `DEMO_PROMPT_LAYOUT`
  falls back to `anchor` with a note, never to an unmeasured layout. (A third arm that appended the
  anchor to the child's own turn was built and dropped unmeasured: a knob value nobody has measured is
  a trap, not an option.)
- Which layout a model needs is measured, not assumed. On the gateway's strongest chat alias the
  trailing anchor was dropped on every turn (2026-10-08: prompt tokens 682 against 1,744; 0 of 39
  envelopes), so under `anchor` that alias answers with no envelope and whatever goodbye is its own
  habit; it is usable only with `single`. The production model keeps its envelope under `anchor`
  (28/28 and 38/38 in two measurements) and loses it under `single` (10/33).
- Nothing reaches disk.

### 3.4 Voice-first ordering, one ticket per sentence

`cloud-transport.js` posts `/api/chat`, tells the bridge to expect that event's voice
(`expectCloudVoice`, per event since 2026-10-08: a line whose voice is on the way waits silently rather
than starting a local stand-in that the late voice then cuts), and immediately posts `/api/speech` with
**chunk 0's** ticket. It routes chunk 0's TTS message **before** the chat message; the chat message goes
out when chunk 0 lands or after `SPEECH_WAIT_MS` = **2500 ms** (client-side), still expecting the voice,
which plays when it lands. If chunk 0 fails — refused, unreachable, or no answer by the client's own 15 s
deadline — the held words are spoken locally once (`releaseCloudVoice`) and a voice turning up later is
dropped. The later chunks are redeemed **one at a time**: chunk k+1 is requested the moment chunk k
lands, so it synthesises while chunk k plays (two at once were measured to slow chunk 0 from 1.6–2.6 s
to 2.7–3.7 s, 2026-10-08), and they are routed in order behind chunk 0 (`voice/cloud.js` starts chunks
in `chunk_num` order and writes a missing one off after 1.2 s, so arrival order would lose a slow
sentence). The first chunk that fails ends the voice: nothing later is redeemed, and no local voice ever
stands in for a later chunk — the words are on screen and her first sentence was heard. The result is
one voice per turn, her first words after one short synthesis; the bridge's per-event seam is its only
change.

### 3.5 `cloud-transport.js` wraps, it does not replace

It is loaded after `bridge.js`. It wraps `window.moxieBridge.sendUserTurn` and `isLive`. The other
members pass through, so the seven-member surface is intact. When the mode is not `live`, it
delegates to the original `sendUserTurn`, so the MQTT and stub paths are unchanged. When live, it
echoes the user turn through `inner.route()`. It also injects the **Talk** box, because nothing on the
page could otherwise send a child's turn. Vision events are not sent to `/api/chat`.

## 4. The security model

### 4.1 The controls

**Build the upstream body, never forward the client's** (`_lib/prompt.js::buildUpstreamBody`). The
model, `max_tokens`, `temperature` (0.8), penalties and message array all come from config. This one
rule rules out model substitution, `n`/`tools` amplification and system-prompt override.

| Control | Default | Why |
|---|--:|---|
| `DEMO_MAX_TOKENS` | 160 | caps the expensive half of a completion |
| `DEMO_MAX_INPUT_CHARS` | 500 | a child's utterance; **rejected** (`too_long`), not truncated |
| `DEMO_MAX_TTS_CHARS` | 300 | about 3 short sentences; enforced at mint and at redemption |
| `DEMO_MAX_AUDIO_BYTES` / `DEMO_MIN_AUDIO_BYTES` | 500 000 / 2 000 | a **size** cap, not a duration cap. 500 KB is about 15 s at 16 kHz s16, but over 60 s at 8 kHz 8-bit. The floor answers `too_short` for free. |
| `DEMO_MAX_RECORD_MS` | 15 000 | **The real ceiling on STT cost.** `mic.js` hard-stops the recorder. The server reads a WAV header's own `rate × channels × bits` against the data size (`_lib/wav.js::wavDurationMs`) and refuses `too_long` with zero upstream calls. Compressed containers cannot be measured without a decoder. The WAV-only default for `DEMO_STT_FORMATS` is what makes the cap total. Widening that list re-opens the gap. |
| Per-IP chat | 5/min · 40/hour · 150/day | generous for a person, cheap for us |
| Per-IP speech | 10/min · 80/hour | no day window (the unit budget's day covers it). Sized for **two** voice chunks per chat turn at full pace (5/min, 40/hour); a visitor whose every reply is three sentences at full pace has later chunks refused `rate_limited`, which ends that reply's voice (the words stay on screen). Raising them is a `DEFAULTS` change. |
| Per-IP transcribe | 10/min · 60/hour | no day window |
| Concurrency | chat 4 · speech 8 | `transcribe` **shares chat's ceiling**. Matched to the upstream key's parallel limit, which protects a neighbouring service. Deliberately not raised. |
| `DEMO_QUEUE_MAX_WAIT_MS` / `_DEPTH` | 2 500 ms / 8 | At the ceiling a request waits in a bounded FIFO. Past the depth, or when the wait expires, it is refused `at_capacity`. **Either set to 0** gives instant refusal. |
| Timeouts, chat / speech / STT | 20 000 / 12 000 / 12 000 ms | Chat is below the 45 s worst case on purpose: a fast honest degrade beats a slow success. |
| Unit budget | 600/hour · 4 000/day | **Request units**, not dollars, because no price sheet exists (assumption 19). chat = 3, speech = 2, transcribe = 2 (`_lib/counters.js::UNITS`). A turn is 3 + 2 per voice chunk: 5 units with one chunk (120 turns an hour, 800 a day), 9 with the three-chunk maximum (66 an hour, 444 a day); measured 2026-10-08, ten typed turns made 16 chunks, 7 units a turn on average (about 85 turns an hour). |
| `DEMO_TICKET_TTL_S` | 60 | long enough for a slow client, short enough that a leaked ticket is useless |
| `DEMO_ENABLED` | on | kill switch: `0` forces `gateway_not_configured` without deleting the secret |

**What "per-IP" keys on** (`_lib/clientip.js`). The key is `CF-Connecting-IP`, with IPv6 truncated to
its **/64**, so one subscriber is one bucket. `::ffff:a.b.c.d` is unmapped to the v4 address. It is not
truncated, which would collapse all of IPv4 into one bucket. `X-Forwarded-For` is honoured only with
`DEMO_TRUST_XFF`, which must stay **unset in production**. Callers who cannot be identified share one
`unknown` bucket.

**Admission order** (`_lib/limits.js::admit`) is origin pin, then per-IP windows, then unit budget, then
concurrency (with the FIFO), then the shared tier. Every free refusal happens before any expensive one.
The concurrency slot, the only thing that must be given back, is taken last and released in a `finally`.
Inside the FIFO, `release()` **hands the slot to the longest waiter** without decrementing, so a late
arrival cannot overtake.

**Refunds.** Admission charges the window and the budget before the route body runs.

- A refusal inside the admitted section makes no gateway call. Examples: `too_short`, `too_long`,
  `bad_request`, `bad_ticket`, `blocked`, `turnstile_failed`. These call `slot.refundBudget()`, which
  gives back the **unit budget but not the per-IP window**. Without it, 200 tokenless POSTs would empty
  the shared hour for free. Keeping the window charged is what quiets a flood from one address.
- A queue timeout or full queue, and a shared-tier refusal, refund **both** (`refundCharges`), because
  neither is the requester's fault.
- The chosen cost: while a request waits, its charge is held. A concurrent request can therefore be
  refused on units about to come back, bounded by depth × cost (8 × 3 = 24 units).
- The rejected alternative was waiting before charging. It would let a script with no rate-limit budget
  occupy queue slots.

**Upstream fetches use `redirect: "manual"`.** The request carries the only credential, plus the
`CF-Access-*` pair when configured. An unfollowed 3xx answers `gateway_unreachable_or_gated`: a door
problem such as an Access login, a moved endpoint or an `http://` base, rather than `upstream_down`.
Write the `https://` URL. An Access login page served at 200 is recognised as the same reason. An
upstream 429 becomes our 429, with `Retry-After` taken from the gateway, clamped to 300, default 10.

**Pre-inference safety** (`_lib/safety.js` + `safety.rules.js`, a plain JS module because the Pages build
rejects JSON import attributes, assumption 26). A hard block returns `reason: "blocked"`, 200,
`ok: true, degraded: true`, and the rule table's own redirect line. It spends nothing. **It is a
floor, not a filter.** The persona and model alignment sit above it.

**Bot control: Turnstile** (`_lib/turnstile.js`, `sim/web/turnstile.js`). It is enforced only when
`DEMO_TURNSTILE_SECRET` and `DEMO_TURNSTILE_SITEKEY` are **both** set. Exactly one of them set counts as
unconfigured.

- It guards `/api/chat` (action `chat`) and `/api/transcribe` (action `transcribe`). A token for one is
  refused by the other. `/api/speech` needs no token, because it cannot be driven without a ticket
  from a guarded turn.
- It is placed after every free refusal and after `admit()`, so the per-IP windows protect siteverify
  and not the reverse. It is also after the safety floor, and immediately before the gateway call.
- Three mandatory checks: `success === true`, `action` equals the route's action, and `hostname` is in
  `DEMO_TURNSTILE_HOSTS` (default: the request's own hostname, exact match).
- **Fail closed on a verdict, fail open on transport.**
  - `success:false` or a mismatch → `turnstile_failed` (403; per-turn, the page stays live).
  - Error codes that name our secret → `turnstile_misconfigured` (503; the page reads scripted), at
    any HTTP status.
  - Unreachable, timed out (`DEMO_TURNSTILE_TIMEOUT_MS` 2000), non-JSON or `internal-error` → allow.
- The client mints a **fresh token per send**. Tokens are single-use and live 300 s. No session
  cookie is set, and the deployment sets no cookies at all.
- Turnstile removes the cheapest attack, a loop with no browser. **It does not bound the bill.**

### 4.2 What the browser may know

**Allowed:** mode, reason, `retry_after_s`, `limits`, `load`, the two message payloads, the opaque ticket
and context blobs, and the Turnstile sitekey. **Never:** the gateway base URL, any key, model ids,
upstream status or body text, the account id, the ticket secret. `/v1/models` is not proxied. In `cfg`,
the credentials are non-enumerable properties, so `JSON.stringify` cannot leak them.

**Deployment requirement:** use a **separate, budget-scoped gateway key** for the public demo. If the
gateway can mint a virtual key with a hard budget and RPM/TPM limits, that is the strongest control
available, and everything in this section becomes defence in depth (assumption 14).

### 4.3 The origin pin (`clientip.js::checkOrigin`)

1. If `Sec-Fetch-Site` is present and not `same-origin`, the request is refused.
2. `Origin`, or failing that the `Referer`'s origin, must equal the request's own origin or appear in
   `DEMO_ALLOWED_ORIGINS`.
3. With no `Origin` and no `Referer`, the request is allowed only if `Sec-Fetch-Site: same-origin`.
4. A mismatch is `403 forbidden_origin` with no gateway call.
5. No `Access-Control-Allow-Origin` is ever sent.

**This stops browser hotlinking only. `curl` forges these headers trivially.** What bounds cost is the
per-request caps, the ticket, the budget and the gateway-side key budget. Note that Cloudflare's browser
integrity check at the edge 403s default non-browser user agents before the Function runs (assumption 30).

### 4.4 Demo mode: absent, not merely refused

None of these is routable or present in the bundle:

- the supervisor's write surface: `POST /config` (including `scope=fleet`), `/safety`, `/permits`,
  `/telehealth`, `/voice`, `/voice/test`, `POST`/`DELETE /memory`
- its read surface: `/status`, `/telemetry`, `/schedule`, `/memory`, and the rest. Read-only is still
  not public-safe, because it shows device ids and a child's remembered text.
- the parent-app server, whose `/local/*` is unauthenticated
- `mqtt/status_proxy.py`

The `/api/*` routes write nothing durable anywhere.

### 4.5 Statuses, and how the Sim reacts

| Status | `reason` | `Retry-After` | The Sim |
|---|---|---|---|
| 429 | `rate_limited` (per-IP window, or the gateway's own 429) | window reset / upstream value | Stays `live`, shows the *slow down* chip, answers this turn from `stub.js`, suppresses live turns until `Retry-After`. |
| 503 | `at_capacity` (ceiling reached and queue full or wait expired) | 15 | Busy pill, answers from the stub. |
| 503 | `budget_exhausted` | seconds to the window reset | Full degrade. Next health poll at `Retry-After`. |
| 503 | `upstream_down`, `gateway_unreachable_or_gated` | 60 | Full degrade. |
| 503 | `gateway_not_configured` | none | Full degrade for the session. |
| 503 | `turnstile_misconfigured` | 60 | Degraded, scripted copy. |
| 504 | `timeout` (our own `AbortSignal`) | 10 | Answers from the stub. Counts toward the 3-strike degrade. |
| 400 | `bad_request`, `too_long`, `too_short`, `bad_ticket` | none | Plain reason inline. Mode does **not** change. |
| 403 | `forbidden_origin` | none | Treated as offline. |
| 403 | `turnstile_failed` | none | Stays live; "give that another try". |
| 200 | `blocked` | none | Answers with the redirect line. |

`X-RateLimit-Limit/-Remaining/-Reset` (when admission ran) and `X-Moxie-Mode` ride every response, so
the page can pace itself before it is refused. The Python SDK already parses `Retry-After`.
**Never a bare 500, never a 200 with an empty string.**

A spend refusal opens no client-side suppression window. `budget_exhausted` leaves `live` outright,
which is stronger. Recovery is gated by the server's `Retry-After`, clamped by `mode.js`'s
`POLL_MAX_MS` (5 min).

### 4.6 Counters, honestly

**The counters are best-effort. They are not a global ceiling.** This page may not describe any of
this as a hard limit.

- **Tier 1:** a module-scope `Map` per isolate (`_lib/counters.js`). Race-free.
- **Tier 2:** the Cache API (`caches.default`, `_lib/sharedtier.js`). It is shared per **colo**, and
  every per-IP window and both unit-budget ceilings are also counted here. `DEMO_CACHE_COUNTER=0`
  switches tier 2 off. `DEMO_CACHE_TIMEOUT_MS` (250) is the deadline for each cache operation.
- **Per-isolate only, on purpose:** the concurrency ceiling and its FIFO. A lost give-back would leak
  a slot for ever, which fails closed. `/api/health`'s `budget`/`load` are per-isolate too, because a
  probe must not wait on a cache.
- **The multiplier left over is colos.** A burst loses writes. Every tier-2 error is an
  **undercount**, so the tier can only add refusals, never wrongly refuse. What actually bounds cost
  is the per-request caps, the ticket and the gateway-side key budget. A true single-writer count
  needs a Durable Object (§9).

#### 4.6.1 The Cache API tier, measured

A throwaway preview probe (since deleted) measured the following. Cloudflare documents that Pages
Functions get functional cache operations on `*.pages.dev` and on custom domains.

Writes persist across requests and across isolates: 30 of 40 sequential reads saw another isolate's
write. Sequential counting is **exact** (41 writes, stored 41). A burst **loses updates** (31 concurrent
writes, stored 9), always as an undercount. One client reached 7 isolates in 1 colo, so the
in-isolate-only multiplier was at least 7. Three operations cost ≤ 44 ms, about 15 ms each. The regime where the tier is exact, sustained sequential draining, is the one that threatens the budget.
The concurrency ceiling and queue already refuse bursts. Keys rotate by time bucket under
`<origin>/__moxie/rl/`.

#### 4.6.2 The unit budget on the shared tier: no refund writes

A lost `prev + 1` undercounts, which is safe. A lost **refund** (`prev − cost`) would **overcount**. It
would leave the colo's budget empty early and refuse real visitors, which fails closed. So the shared
budget **never writes a charge it might have to un-write**:

- `admit()` only **reads** the shared entry.
- Units wait in an isolate-local ledger (`state.units`, `state.unitsDay`). They are added **only by an
  un-refunded `release()`**, meaning the turn really reached the gateway. A refused request publishes
  nothing.
- The next admission publishes them as `put(seen + owed)`. The ledger clears on the **attempt**, not on
  confirmation, so a publish is never counted twice.
- Units from a past hour or day bucket are **dropped**, never moved.

The cost of this design is a lag of at most one settled request per isolate, in the permissive
direction.

#### 4.6.3 The wide windows and the day budget

| Entry | Key | Body | `max-age` |
|---|---|---|---|
| per-IP minute | `…/rl/<route>/<tag>/<minute bucket>` | `{n}` | 60 |
| per-IP hour + day | `…/rl/<route>/<tag>/w<day bucket>` | `{h, hb, d, db}` | 86400 |
| budget hour | `…/rl/units/<hour bucket>` | `{n}` | 3600 |
| budget day | `…/rl/units/d<day bucket>` | `{n}` | 86400 |

- **Hour and day share one entry, so they cost one round trip.** Each scale's bucket is stamped in the
  body. A count stamped with any other bucket reads as **zero**.
- **Keys cannot collide.** The `w` and `d` marks separate the wide keys from the narrow ones, because a
  decimal bucket cannot start with a letter. `<tag>` is a keyed HMAC (`COUNTER_INFO`), so an IP never
  appears in a key.
- **The day budget repeats §4.6.2 exactly**, with a second ledger, because hour and day roll on
  different clocks.
- **Every failure mode admits.** Examples: a hung or throwing `match`/`put`, a stale entry, an
  unparseable body, a foreign bucket stamp, a lost update, a recycled isolate, a day boundary.
- The wide windows are consulted **before** the minute window writes. So a per-IP refusal costs zero
  cache writes, and a visitor over their own limit gets a 429 rather than a deployment-wide 503.
- **Known residual:** a `budget_exhausted` refusal from the shared tier leaves that visitor's window
  entries one higher than earned. It is bounded at one increment per refused request and only bites
  while the deployment is already out of budget. The fix is a read-phase/write-phase split.
  `helpers_shared_ceilings.mjs` §K pins the residual.
- **Cost:** a first admitted turn is 4 reads and 2 writes; a turn that owes units is 4 reads and 4
  writes; a refusal is 1–4 reads and no writes.
- With `DEMO_UNIT_BUDGET_HOUR=0` (uncapped hour), the day ceiling is still enforced.
- **Only the hermetic suites exercise this tier.** A Pages preview has no gateway secrets, so every
  POST refuses `gateway_not_configured` before any counter. The evidence is
  `sim/tests/helpers_shared_ceilings.mjs` (injected fake store, two isolates via `__reset()`) and
  `sim/tools/unit_budget_mutation_check.py`. Multi-colo behaviour, real eviction and real burst
  losses are out of reach of any test here.

### 4.7 Page headers (`sim/web/_headers`)

The static pages ship `X-Content-Type-Options`, `Referrer-Policy: strict-origin-when-cross-origin`,
`Permissions-Policy: microphone=(self), camera=(), geolocation=()`, HSTS, and a CSP whose `script-src`
is `'self'` plus one hash plus the Cloudflare Insights and Turnstile origins. `style-src` still carries `'unsafe-inline'` because mermaid needs it. That file owns the details. A
broken `docs.html` is a worse outcome than a missing header, so verify CSP changes on a preview.

#### 4.7.1 The `/api/*` headers are set in code

`_headers` does not reach Function responses (assumption 27). `envelope.js::respond` applies
`API_SECURITY_HEADERS` to **every** reply, **after** any caller-supplied headers, so a caller cannot
weaken them:

```
X-Content-Type-Options: nosniff
Referrer-Policy: same-origin
Strict-Transport-Security: max-age=31536000; includeSubDomains
Content-Security-Policy: default-src 'none'; frame-ancestors 'none'; base-uri 'none'
Cross-Origin-Resource-Policy: same-origin
```

HSTS is byte-identical to the pages' header, with no `preload`. The CSP is the lockdown form, because a
JSON body loads nothing. `REJECTED_SECURITY_HEADERS` records why `X-Frame-Options`,
`Permissions-Policy`, COOP, COEP and `Access-Control-Allow-Origin` are **not** sent. The `/api/*` block
in `_headers` is inert. `sim/test_api_headers.mjs` proves CORP cannot break the same-origin site and
that the CSP has effect.

### 4.8 The synthesized-audio cache (`_lib/ttscache.js`)

`/api/speech` keeps successful synthesis in `caches.default` under `/__moxie/tts/`. A hit is one
`match` and **zero upstream calls**. A miss is one `match` and one `put`.

- **Key.** The full 256-bit keyed HMAC (`TTS_CACHE_INFO`) of a length-prefixed join of: entry format
  `v1`, `DEMO_GATEWAY_BASE_URL`, `DEMO_TTS_MODEL`, the voice actually sent, `DEMO_TTS_FORMAT`,
  `DEMO_TTS_SAMPLE_RATE`, and the **exact** text. A key missing any of these could serve one child
  another voice. It is keyed, so outsiders cannot enumerate audio by guessing sentences. The event id,
  chunk and visitor are not in the key.
- **Placement.** The cache sits after every cap, the ticket and replay checks. A refused request makes
  zero cache calls.
- **Fail-open, and only successes are stored.** Every cache failure falls through to the gateway. Only
  a decoded, non-empty 16-bit result is written. The entry is a WAV, so the hit path decodes with the
  same `pcmFromAudio` as the miss path.
- **Settings.** `DEMO_TTS_CACHE=0` removes every cache call. `DEMO_TTS_CACHE_TTL_S` is 86 400 (range
  60..604 800). `DEMO_TTS_CACHE_TIMEOUT_MS` is 1000 per operation (range 50..5000). This switch is
  separate from `DEMO_CACHE_COUNTER` because the two fail in opposite directions.
- **Limits.** The cache is per-colo, and a cold colo pays. The hit rate is **unmeasured**. Only
  gateway replies are ever cached, because scripted lines never reach this route.

### 4.9 Repetition levers

The owner reported that "Moxie gets stuck in a loop repeating the same things". `sim/eval_live.mjs`
measures it by driving scripted conversations at a live deployment. It needs `--yes`, because every turn
costs money. It scores `repeatOpening`, `maxOverlap`, `exactDupes`, `questionRate` and `runMax`.

| # | Lever | Where | Cost |
|---|---|---|---|
| 1 | Persona rules: keep the conversation moving, never repeat a sentence, do not end every turn with a question | `DEFAULT_PERSONA` / `DEMO_PERSONA` | free |
| 2 | `frequency_penalty` 0.4 / `presence_penalty` 0.3. A value of 0 is not sent. A gateway that 400s on them has them dropped for the life of the isolate, and the call is retried once. | `DEMO_FREQUENCY_PENALTY`, `DEMO_PRESENCE_PENALTY` | free |
| 3 | **Re-roll:** a reply that exactly matches (ignoring case, whitespace and punctuation: "That's okay." and "That's okay!" were served live as two turns of one conversation) any assistant turn in the signed window is asked again **once**, with a server-built system message forbidding that line. The second body carries the same reference passage as the first, and the diagram served is the one drawn for the served line. | `DEMO_REROLL`, `chat.js` step 8b, `_lib/reply.js::echoOf` | **one extra completion** |

Re-roll accounting:

- The per-IP window is charged **once**, because the visitor typed one sentence.
- The unit budget is charged `UNITS.chat` (3) **again, before the call** (`slot.chargeExtra()`), in both
  tiers.
- If no ceiling has headroom, the re-roll is skipped. It is never turned into a refusal.
- The second call gets only what remains of `DEMO_CHAT_TIMEOUT_MS`, and only if that is at least what
  the first call took.
- `rerollOnce` has no loop. Every failure, including a second echo, keeps the first reply.

The known limits are metric limits. A lexical metric improved while the conversation still read as a
loop. Near-duplicates pass, since no threshold has been measured. Repeats from outside the signed window
are invisible. **Read the transcripts, not just the numbers.**

### 4.10 Turn shape (`_lib/turnshape.js`)

The deeper defect was a repeated **move**, not a repeated sentence: six "Let's …!" proposals in a row
scored well on every lexical metric. Each assistant turn in the signed history is classified as one of
three moves: **`ask`** (ends with `?`), **`offer`** (a proposal marker such as "let's", not a question)
or **`tell`** (anything else). One sentence naming the first move that is not one of the last two is placed in the trailing
anchor (§3.3), in the same block as the anchor's restatement and before the format rule. This costs no
extra call. It is a closed loop: the next cue follows what she *actually* said.
With `DEMO_TURN_SHAPE=0`, the upstream body is byte-identical to one without the cue.

**The close move (2026-10-08).** A fourth move sits outside the rotation. When the child's whole line is
a leave-taking (`_lib/turnshape.js::isGoodbye`: anchored and whole-utterance, so "okay bye moxie!",
"i have to go to bed" and "night night" count, while "my dog died and I had to say goodbye", "good night
story please!" and "I don't want to say bye" do not), the cue is `close` (say goodbye; no question, no
new topic, no offer), the wire's `end_turn` is `true`, and the markup carries the `Bht_Sign_off` wave,
which the model may also name itself as the `wave` gesture. A miss falls back to the rotation; a false
hit would hang up on a child mid-talk, so the grammar errs towards missing: a bare "later" (how a child
defers an offer), "I'm done playing" (a game ending as often as a visit) and distress lines ("goodbye
forever") are misses by design, while a phone keyboard's curly apostrophe ("I’m going to bed") and a
waving hand or smiley after the goodbye ("bye 👋") are folded away before the match. `DEMO_TURN_SHAPE=0`
removes the cue but not `end_turn` or the wave — the trade-off is deliberate: with the switch off a
goodbye can be answered with a question while the wire still says the turn is over, which is why the
switch is a measurement control and not a production setting (`sim/test_demo_proxy.mjs` §20 pins
it). Measured before the fix: production acknowledged 0 of 4 goodbyes; in replay on the same model an
explicit close cue restored it 5/5, 5/5 and 3/3 across three harnesses. `sim/eval_live.mjs
--only=goodbye --repeat=N` is the instrument.

**Measured**: 6 conversations of 7 turns per arm, against the same gateway and model.

| `loop` scenario | Moves used | Mean `runMax` | Worst `runMax` | `questionRate` |
|---|---|---|---|---|
| cue off | 2.33 | 3.67 | 6 | 38 % |
| cue on | 3.00 | 1.33 | 2 | 36 % |

What remains wrong:

- Near-duplicate offers survive.
- Perfect obedience can still braid three templates, and `runMax` would score that as perfect.
- The cue must stay in the same block as the anchor's restatement. As a separate system message, the
  wording collapsed.
- After any edit to the cue strings, check `repeatOpening` and `maxOverlap` on `feelings`.

## 5. Configuration

Set variables on the Pages project, **Production environment only**. Secrets use the encrypted type or
`npx wrangler pages secret put`, never `wrangler.toml`. Previews then stay keyless and answer
`gateway_not_configured`. No GitHub Actions secret is needed, because the GitHub App deploys. Do not add
a `wrangler-action` workflow, which would double-deploy. `.dev.vars.example` at the repo root is the
local template for `npx wrangler pages dev sim/web`. `.dev.vars` is git-ignored.

Numeric values outside the accepted range, or not numbers, **fall back to the default**; they are not
clamped. `_lib/env.js::DEFAULTS` is the source of truth.

| Variable | Default | Range / notes |
|---|---|---|
| `DEMO_ENABLED` | on | kill switch |
| `DEMO_GATEWAY_BASE_URL` | none | **required for live**; any OpenAI-compatible `/v1` base, `https://` |
| `DEMO_GATEWAY_API_KEY` (secret) | none | **required for live**; never echoed or logged |
| `DEMO_CHAT_MODEL` | none | **required for live** |
| `DEMO_GATEWAY_ACCESS_CLIENT_ID` / `_SECRET` (secret) | none | Cloudflare Access service token; **both or neither**, one alone is unconfigured |
| `DEMO_TTS_MODEL` | none | unset means `voice: false` (text still works, spoken from clips) |
| `DEMO_TTS_VOICE` | derived | unset derives from the model id's last `-` segment (`piper-amy` gives `amy`), else `alloy` |
| `DEMO_TTS_FORMAT` | `wav` | `wav` or `pcm` only |
| `DEMO_TTS_SAMPLE_RATE` | 22050 | 3000..384000; used only under `pcm` |
| `DEMO_STT_MODEL` | none | unset means `ears: false`. **Ears also need the three live variables.** |
| `DEMO_STT_FORMATS` | `wav` | subset of `wav,webm,ogg,mp4,mp3,flac`; the default is a measurement (assumption 15) |
| `DEMO_MAX_RECORD_MS` | 15000 | 1000..600000 |
| `DEMO_MAX_AUDIO_BYTES` / `DEMO_MIN_AUDIO_BYTES` | 500000 / 2000 | 1..5e7 / 0..5e7 |
| `DEMO_TRUST_XFF` | off | **leave unset in production** |
| `DEMO_PERSONA` | built-in | the system prompt |
| `DEMO_DEVICE_ID` | `d_sim` | topic segment |
| `DEMO_ALLOWED_ORIGINS` | none (the request's own origin) | comma-separated extra origins |
| `DEMO_TICKET_SECRET` (secret) | HKDF of the API key | set it if you rotate the key often |
| `DEMO_TICKET_TTL_S` | 60 | 5..3600 |
| `DEMO_MAX_TOKENS` | 160 | 1..4096 |
| `DEMO_MAX_INPUT_CHARS` / `DEMO_MAX_TTS_CHARS` | 500 / 300 | 1..20000 |
| `DEMO_MAX_CONTEXT_CHARS` / `DEMO_MAX_HISTORY_TURNS` | 4000 / 12 | 0..100000 / 0..64 |
| `DEMO_FREQUENCY_PENALTY` / `DEMO_PRESENCE_PENALTY` | 0.4 / 0.3 | −2..2; 0 is not sent |
| `DEMO_TURN_SHAPE` / `DEMO_REROLL` | on / on | §4.10 / §4.9 |
| `DEMO_PROMPT_LAYOUT` | `anchor` | `anchor` · `single` (§3.3); an unknown value falls back to `anchor` with a note; measure a model on `single` before switching production to it |
| `DEMO_CHAT_PER_MIN` / `_HOUR` / `_DAY` | 5 / 40 / 150 | ≥ 1 |
| `DEMO_SPEECH_PER_MIN` / `_HOUR` | 10 / 80 | ≥ 1 |
| `DEMO_STT_PER_MIN` / `_HOUR` | 10 / 60 | ≥ 1 |
| `DEMO_MAX_CONCURRENT_CHAT` / `_SPEECH` | 4 / 8 | 1..10000; transcribe uses chat's |
| `DEMO_QUEUE_MAX_WAIT_MS` / `_DEPTH` | 2500 / 8 | 0..10000 / 0..1000; 0 disables the queue |
| `DEMO_CACHE_COUNTER` / `DEMO_CACHE_TIMEOUT_MS` | on / 250 | timeout 10..2000 |
| `DEMO_TTS_CACHE` / `_TTL_S` / `_TIMEOUT_MS` | on / 86400 / 1000 | 60..604800 / 50..5000 |
| `DEMO_UNIT_BUDGET_HOUR` / `_DAY` | 600 / 4000 | 0 means uncapped |
| `DEMO_CHAT_TIMEOUT_MS` / `_SPEECH_` / `_STT_` | 20000 / 12000 / 12000 | 1000..120000 |
| `DEMO_TURNSTILE_SECRET` (secret) / `_SITEKEY` | none | **both or neither**; leave unset on Preview |
| `DEMO_TURNSTILE_HOSTS` | the request's own hostname | exact match |
| `DEMO_TURNSTILE_TIMEOUT_MS` | 2000 | 100..10000; a slow answer fails open |

**Forking:**

1. Connect the fork to a Pages project.
2. Add a custom domain. No code changes are needed.
3. Set the three live variables, plus `DEMO_TTS_MODEL` for voice.
4. Redeploy. `/api/health` should say `mode: "live"`.

With nothing set, the fork is the static demo.

## 6. The fallback experience

### 6.1 Reused as-is

`stub.js` answers every degraded reply: the transport delegates to the original `sendUserTurn`. Clips
come from `audio/index.json`. Ambient self-talk runs client-side in every mode. `mic.js` keeps its
scripted child line on any refusal, and shows the reason.

### 6.2 Content built for the fallback

- Every stub reply and filler line has a pre-rendered clip. They were rendered with local Piper and zero
  gateway calls, reproducibly via `sim/ci/fetch_piper_voices.py`.
- **One in-character degraded line** is a top-level `degraded` key in `ambient.json`, outside `lines[]`,
  so the random bag cannot reach it. Its clip is in the manifest's `moxie` group. `ambient.js` speaks it
  **once, on entering `degraded` only**, not `offline`. If autoplay is locked, the tab is hidden or
  liveness is off, it arms and speaks on the next unlock.
- **The 1.4 s Piper probe is skipped when `degraded`** (`audio.js::skipProbe`). It still runs in
  `offline`, where a self-hoster's local Piper is the reason it exists. An explicit `moxie.ttsBase`
  wins in every state.
- `sim/test_fallback_coverage.mjs` inventories every line a degraded page can say and requires a clip
  for each.

### 6.3 The state machine (`sim/web/mode.js`)

```mermaid
stateDiagram-v2
  [*] --> boot
  boot --> offline: /api/health absent · non-200 · network error
  boot --> degraded: health ok · mode not live
  boot --> live: health ok · mode live
  live --> degraded: 503 · 3 consecutive transport errors · budget_exhausted · upstream_down
  live --> live: 429 rate_limited · turns suppressed for Retry-After
  degraded --> live: a health poll returns mode live
  offline --> offline: never polls again this session
```

- **`offline`**: no Functions (a plain CDN or `file://`). The page is byte-identical to the static demo:
  `HOSTED DEMO` badge, stub plus clips, no polling, no new requests.
- **`degraded`**: the route answered honestly. Stub plus clips, with the pill and the reason.
- **`live`**: the HTTP transport is used.

Polls follow `Retry-After` when sent. Otherwise they start at 30 s (`POLL_MIN_MS`) and double to 5 min
(`POLL_MAX_MS`), resetting on success. There is **no polling while `document.hidden`**. Recovery flips
the badge back to `MOXIE ONLINE`. The spoken "I'm back" line is not built (§9).

## 7. Capacity signalling

`load.level` is `ok` below 60 % of the route's ceiling, `busy` from 60 % up to the ceiling, and `full`
at it (`envelope.js::loadLevel`). `/api/health` reports the chat ceiling, which `transcribe` shares.
Copy lives in `mode.js`:

| Signal | Badge | Copy |
|---|---|---|
| `ok` | `MOXIE ONLINE` | none |
| `busy` / `full` | `HOSTED DEMO · BUSY` | "…talking with a few other people…" / "Moxie has her hands full right now…" |
| `budget_exhausted` | `HOSTED DEMO · SCRIPTED` | "Moxie's live brain has used up today's demo budget…" |
| `upstream_down` / `gateway_unreachable_or_gated` / `timeout` | `HOSTED DEMO · SCRIPTED` | "…brain is unreachable…" |
| `turnstile_misconfigured` | `HOSTED DEMO · SCRIPTED` | "…visitor check isn't set up right…" |
| `turnstile_failed` | `MOXIE ONLINE` | "Moxie needs to check you're a real person…" |
| `rate_limited` | `MOXIE ONLINE` + chip | "One at a time! Give Moxie a few seconds." |
| `gateway_not_configured` | `HOSTED DEMO` | the static-demo copy |

`env.js` paints the badge, banner and `needs-backend` marks from the mode, not the hostname.
`#bus-connect` keeps `needs-backend` in every mode. Raw status codes and upstream text are never shown.
`inflight` is one isolate's count, which is why the copy is human rather than a gauge.

## 8. Tests and acceptance

### 8.1 Hermetic tests (bare node, stubbed `fetch`, no account)

The handlers are ESM, so the tests import them and pass a synthetic `Request` and a fake `env`. Each
suite is split into modules under `sim/tests/edge/<suite>/`.

| # | Test | Pins |
|--:|---|---|
| 1 | `sim/test_demo_proxy.mjs` | Unknown keys dropped; the upstream body uses the configured model and `max_tokens`; `too_long`; origin refusal with zero upstream calls; upstream 429/500 sanitized (no model or key text in any response); `budget_exhausted`; `X-RateLimit-*` on success; the §2.2 field set with no `chunk_num`, `consistency_control` or `emotion`. Also the queue (block 13), shared tier (§15), TTS cache (§16), re-roll and turn shape, the goodbye close and the prompt layouts (§19–22: persona once, anchor last, no non-first system message outside `anchor`, no brace ever in the spoken text or the tickets), one ticket per sentence (§10f: the measured 311-char reply yields 2–3 tickets that join back to the whole reply, every one redeemable with its `chunk_num`; the three-chunk cap; a word-bounded cut; a three-chunk turn is 9 units), API headers, and a fail on any `.json` import under `functions/`. |
| 2 | `sim/test_demo_tickets.mjs` | Forged, expired, over-length, replayed or tampered ticket or context; round-trip; constant-time compare; the sentence splitter (numbers, abbreviations, initials, ellipses and mermaid fences never split; chunks join back to the reply; the cap and the word-bounded cut) and `mintTickets`. |
| 3 | `sim/test_wav_decode.mjs` | The RIFF walker uses the header's own rate and channels, refuses 8- and 24-bit and JSON, and agrees sample-for-sample with `audio.js`'s decoder; `wavDurationMs`. |
| 4 | `sim/test_mode.mjs` | The state machine, backoff, hidden-tab rule, `offline` never polls; env defaults and envelope. |
| 5 | `sim/test_cloud_transport.mjs` | Seven members intact; TTS routed before chat; chat lands by the 2.5 s wait; delegation when not live; the naive ordering proven to double-voice; one ticket per sentence (§4b–4h: chunks redeemed one at a time and routed in order behind chunk 0, a later chunk's failure ends the voice with no local stand-in, a hanging chunk given up at the deadline, three chunks heard in order on the real `voice/`). |
| 6 | `sim/test_fallback_coverage.mjs` | Every line the degraded page can utter has a clip on disk; the prerender tool keeps every manifest group. |
| 6b | `sim/test_demo_ears.mjs` | `/api/transcribe`: byte caps, windows, budget, timeout, format allowlist returning 400 with no call, the upstream status table, secret sweeps. Plus the real `mic.js`: 15 s hard stop, target selection, browser WAV encoder read back by the server walker. |
| 7 | `sim/test_env_hosted.mjs` | Zero `:8081`/`:8082` probes on a hosted host; badge per mode in Chrome. |
| 8 | `sim/tests/test_ci_workflows.py` | The node tests are wired into `sim/ci/ci.yml`. |
| 9 | repo lint (`sim/tests/edge/demo_proxy/04_deploy_only.mjs`) | No key, gateway host or account id under `functions/` or `sim/web/`; no `[vars]` in `wrangler.toml`. |

Also: `sim/test_turnstile.mjs` (checks, fail-open/closed split, refund, slot release, client table
parity), `sim/test_api_headers.mjs` (real socket and Chrome), `sim/tests/helpers_shared_ceilings.mjs`
(§4.6.3). The mutation checks are `sim/tools/unit_budget_mutation_check.py` and
`turnstile_mutation_check.py`.

### 8.2 What only a real deploy settles

Previews carry no secrets, so they prove routing, envelopes and headers but nothing past the config
gate. Production-only questions: Pages CPU, wall-clock and body limits against a 20 s chat timeout
(assumption 10); whether Production and Preview variables are truly separate (assumption 11); plan
features (assumption 13); the gateway key budget (assumption 14). `sim/tests/test_live_hosted_ears.py`
exercises the real route (assumption 29).

### 8.3 Acceptance criteria

1. A typed sentence on production gets a spoken, lip-synced, markup-driven answer inside
   `DEMO_CHAT_TIMEOUT_MS`.
2. `DEMO_ENABLED=0` gives the scripted demo and no gateway request.
3. No key in `sim/web`, and no request to any host but the site's own origin (Turnstile's origin aside,
   when enforced).
4. A foreign `Origin` gets 403 with no gateway call.
5. The sixth rapid turn from one IP gets a 429 with `Retry-After`, and the page still answers from the stub.
6. A spent budget gets 503 `budget_exhausted` and a degraded page within one turn.
7. A hand-made or expired ticket gets 400.
8. A keyless preview is the scripted demo.
9. A killed gateway degrades honestly, and the page recovers within one poll.
10. Real spoken words come back as those words through `/api/transcribe`: overlap ≥ 0.7, with a decoy
    control below 0.35. Proven at 1.00 / 0.07. **No human has yet spoken into the hosted microphone.**

## 9. Not built

- **Exact global counters** on a Durable Object or KV. These are gated on whether the plan offers them
  (assumption 13).
- **Read/write phase split** of the shared tier, which closes §4.6.3's residual.
- The spoken **recovery line** on `degraded → live` (§6.3).
- **Near-duplicate** detection for the re-roll, which first needs a hand-labelled distribution.
- **P2:**
  - streaming chunks (`chunk_num` with `consistency_control`) under the same budget
  - a faithful JS port of `automarkup.annotate` against the Python goldens
  - the child's voice made audible in replays
  - a session library with a picker
  - vision-event turns through `/api/chat`
- **Do not cache STT.** That is a privacy problem, not a saving.

## 10. Assumption ledger

These numbers are stable, and code cites them.

| # | Assumption | State |
|--:|---|---|
| 1–2 | `route()` is the only ingress; `bridge.js` and `audio.js` need no change | proven (test 5) |
| 3 | `build_chat_response`'s field set is the whole chat contract | proven |
| 4 | Omitting `chunk_num`/`consistency_control` is byte-identical to the pre-streaming wire | proven |
| 5 | The Sim ignores `result` | proven (`stub.js` sends `"OK"`) |
| 6–7 | Raw s16 PCM at the header's rate plays; empty `marks` still lip-sync | proven (test 3) |
| 8 | `functions/` at the repo root is routed with output dir `sim/web` | **settled true** by a preview `curl` |
| 9 | `functions/api/_lib/` is not routable | **settled true**: it serves the static HTML fallback (200, not 404; check the content type) |
| 10 | Pages allows a 20 s wall clock and a ~500 KB body | unverified; every timeout is a variable |
| 11 | Production and Preview variables are separate | partial: previews hold only Pages' own 5 env keys, but separation is unproven until Production holds secrets |
| 12 | Free-tier Functions limits (requests, CPU) | unverified; nowhere in the repo |
| 13 | KV / Durable Objects / WAF rate limiting exist on this plan | split. The runtime has **no** stateful binding configured, and whether the plan offers one is a dashboard question. The Cache API needs no binding, so §4.6.1 did not depend on this. |
| 14 | The gateway can mint a budget-scoped virtual key | unverified; **check first** |
| 15 | The gateway accepts webm/Opus for STT | **settled false**: it returns 500 to webm/ogg/mp4 and transcribes 16 kHz mono WAV. So `DEMO_STT_FORMATS=wav`, and `mic.js` encodes WAV in the browser. |
| 16 | `MediaRecorder` defaults and mic sample rate | moot for the hosted path, which no longer uses `MediaRecorder`; the encoder writes the true rate |
| 17 | An `https://` page cannot open `ws://` | inferred; irrelevant to the HTTP path |
| 18 | A robot plays chunk 1+ of an event | unverified on a robot. The SIM does: the hosted turn is up to three chunks and `voice/cloud.js` plays them in order (test_cloud_transport §4b–4g; measured live 2026-10-08 over 20 chunked turns, the longest gap between chunks 139 ms). |
| 19 | Gateway cost per token or second | unknown: no price sheet, so budgets are in request units |
| 20 | `emotion` is not in the chat contract | proven |
| 21–23 | Clip rendering is reproducible; child clips are not played; the account id is public in check-run URLs | proven |
| 24 | Origin checks stop only hotlinking | proven by reasoning (§4.3) |
| 25 | The counters are not a global ceiling | proven (§4.6) |
| 26 | Pages builds accept JSON import attributes | **settled false**; the rule table is a JS module, and test 1 guards it |
| 27 | `_headers` applies to Function responses | **settled false**; §4.7.1 |
| 28 | A bounded queue beats a higher ceiling | proven by test (block 13); the ~1.2 s turn premise is not re-measured, so re-derive the depth if turns slow |
| 29 | `/api/transcribe` returns the spoken words | **settled true** (`test_live_hosted_ears.py`); the route only, not a real microphone |
| 30 | Non-browser clients reach `/api/*` in production | **settled false**. Cloudflare's browser integrity check returns 403 `error_code: 1010` at the edge, as RFC-7807 JSON **without** our `reason` field. A missing `reason` is the tell. Clients need a real `User-Agent`. |

---

📖 [Docs index](../../README.md) · [Architecture index](../README.md) · [Backlog briefs](README.md) ·
[Orchestration plan](../agent-workflow.md) · [Deploy on Cloudflare](../../guides/deploy-cloudflare.md) ·
[MQTT and the conversation](../mqtt-and-conversation.md) · [The AI seam](../ai-seam.md) ·
[The static experience](../static-experience.md)
