# sim/web — Moxie 3D model (simulator front-end)

A self-contained WebGL (three.js) model of the Moxie robot: teal teardrop shell,
tilted oval face-screen with an animated canvas face, two-segment paddle arms,
and a 7-DOF rig matching the real robot's motors. This is the visual half of the
simulator; [`bridge.js`](bridge.js) drives it live over MQTT via the `window.moxie` API
(and the by-hand control panel works with no bus at all).

## Viewing it

```sh
cd sim/web
python3 -m http.server 8080
# then open http://localhost:8080/
```

Any static server works, **fully offline** — three.js (r160) and MQTT.js (5.10.1)
are vendored in [`vendor/`](vendor/) (no CDN).
Drag to orbit the camera, scroll to zoom. The right-hand panel drives every
API call by hand, so the model is demonstrable with no bus running; `bridge.js`
drives the same API live from MQTT when a broker + supervisor are connected.

## Files

| file | purpose |
|---|---|
| `index.html` | page shell, importmap (three@0.160.0), HUD rail markup, bus-status→HUD glue script |
| `moxie.js` | model, rig, face renderer, animation loop, `window.moxie` API |
| `bridge.js` | MQTT→avatar bridge: subscribes to the bus and drives `window.moxie` from live `remote-chat`/markup/motor traffic — including `/commands/tts` (the server voice) |
| `audio.js` | sound: UI SFX, the pre-cached/Piper/browser voices, and **`playCloudTTS`** — decodes the server's `CloudTTSResponse` (base64 raw 16-bit PCM) and plays it with mouth lip-sync |
| `style.css` | mission-control HUD skin (dark void + cyan telemetry, per [docs/design/style-guide.md](../../docs/design/style-guide.md)) |
| `mode.js` | what this deployment can actually DO: polls same-origin `GET /api/health` ([`../../functions/api/health.js`](../../functions/api/health.js)) and publishes `window.moxieMode` — `live` / `degraded` / `offline`, the reason, the capacity signal and the poll schedule ([spec §6.3/§7](../../docs/architecture/backlog/live-sim-demo.md)) |
| `env.js` | the honest indicator: paints the env badge, the capacity pill, the `needs-backend` marks and the hosted banner **from the mode**, not from the hostname. Renders correctly before `mode.js` answers, and with `mode.js` absent. A mark can now also be **`dead`** — a control that cannot work on this origin (`#tts-test`, `#bus-connect`, `#tts-base`, `#stt-base`) is *disabled*, not merely hinted, because a click could only ever fire a cross-origin request the CSP refuses; `#mic-btn` is deliberately marked but never disabled, since "Listen" really does play a scripted line. It also measures the bottom-anchored `#panel` and lifts the hosted banner clear of it (`--eb-lift`) — the banner used to sit exactly on top of `#rail-toggle` on a phone and swallow every tap on it |
| `mic.js` | the ears: records, and posts to whichever STT this deployment has — the same-origin [`POST /api/transcribe`](../../functions/api/transcribe.js) when `mode.js` reports `ears`, else the local sidecar at `<base>/stt` (`sim/stt/server.py`), with an explicit `moxie.sttBase` always winning. **Stops itself at `DEMO_MAX_RECORD_MS` (15 s, published in `/api/health`'s `limits`)** — the byte cap is a size cap, not a duration cap, for a compressed container. On the hosted path it **encodes 16 kHz mono WAV itself**, because the gateway answers HTTP 500 to webm/Opus, ogg/Opus and mp4/AAC ([spec §10 assumption 15](../../docs/architecture/backlog/live-sim-demo.md)); the sidecar keeps `MediaRecorder`. Any refusal falls back to the scripted child line, with the reason on the status line |
| `cloud-transport.js` | the live turn: wraps `window.moxieBridge`'s `sendUserTurn` and takes it to the same-origin `POST /api/chat` + `POST /api/speech` ([`../../functions/api/chat.js`](../../functions/api/chat.js), [`speech.js`](../../functions/api/speech.js)) when `mode.js` says the deployment is `live`. **Routes the TTS message before the chat message** so there is one voice and not two ([spec §3.4](../../docs/architecture/backlog/live-sim-demo.md)); a connected MQTT broker always wins the turn; anything else delegates to `bridge.js` untouched. It also owns **where a typed line comes from**: when no local Piper sidecar answers, `env.js` calls `adopt()` and the page's existing `#speech-input`/`#speech-btn` become the typed turn ("Say" → "Ask") instead of a dead TTS control that silently failed against `:8081`; with a sidecar reachable the button is untouched and the **Talk** box (`#chat-input`/`#chat-send`) is injected instead. Exactly one typed control is ever visible, and both go through the same `sendTyped` — so the same `admit()` gate the microphone passes. |
| `_headers` | Cloudflare Pages cache policy **and** the site's security headers (`/api/*` `no-store`, `nosniff`, `Referrer-Policy`, `Permissions-Policy`, **HSTS**, and a CSP whose `connect-src 'self'` and `script-src 'self'` are the real controls). `'unsafe-inline'` for scripts stays until the inline blocks are hashed or moved — the specific blocker is written down in the file. **It applies to these static pages only, never to Pages *Function* responses** (assumption-ledger row 27). Exercised for real by `sim/test_csp.mjs`, which serves every page with this file's headers actually applied |

## What this deployment can do (`mode.js` + `window.moxieMode`)

The page used to decide everything from the **hostname**: any non-local host was assumed
to have no backend, so every visitor was told *"hosted demo — only pre-scripted lines have
audio"* whether it was true or not, and it never re-checked. Now `mode.js` asks one
same-origin route and `env.js` paints the answer.

| state | when | what the visitor gets |
|---|---|---|
| `offline` | `/api/health` is not there at all — a fork with no Pages Functions, a plain CDN, `file://`, a 404 | **Byte-identical to the site as it shipped**: `HOSTED DEMO`, stub + clips, and nothing is polled again this session |
| `degraded` | the route exists and answered honestly — nothing configured, over budget, or the brain is unreachable | The same page, plus the reason on screen: a badge suffix and a pill. `gateway_not_configured` keeps today's exact copy and fires exactly **one** request |
| `live` | a brain is configured and reachable | `HOSTED DEMO · LIVE`, and the page stops claiming the mic needs a locally-run server, because with a same-origin route that claim is false |

`window.moxieMode` exposes `state()`, `reason()`, `badge()`, `message()`, `load()`,
`limits()`, `voice()`, `ears()`, `apiBase()`, `canSpendLiveTurn()`, `note()`,
`noteTransportError()`, `snapshot()`, `onChange()`, `refresh()` and `stats()`. The poll
schedule is `Retry-After` when the server sent one, otherwise 30 s doubling to a 5-minute
ceiling and resetting on success; it never polls while `document.hidden`.

Two things it deliberately does **not** do. It never claims `LIVE` until something is
loaded that can use a live mode (`cloud-transport.js`, which sets
`window.moxieCloudTransport`) — painting LIVE over a page that still answers from
`stub.js` is the exact dishonesty the module exists to remove. And `#bus-connect` keeps
its `needs-backend` mark in **every** mode: a real robot's MQTT broker genuinely is not
available here, and no same-origin route can change that.

Contract and configuration:
[docs/architecture/backlog/live-sim-demo.md](../../docs/architecture/backlog/live-sim-demo.md);
the routes: [`functions/`](../../functions/README.md); tests: `sim/test_mode.mjs`,
`sim/test_env_hosted.mjs`, `sim/test_typed_turn.mjs` (the typed turn end to end in a real
browser, asserted at the Web Audio layer), `sim/test_mobile_layout.mjs` (phone hit tests)
and `sim/test_csp.mjs` (the shipped security headers, applied).

## The server voice (`CloudTTSResponse`)

When a supervisor is linked, it publishes rendered audio on
`/devices/{id}/commands/tts`. `bridge.js` routes it to
`window.moxieAudio.playCloudTTS(payload)`, which **decodes the wire itself** — base64 →
little-endian signed 16-bit PCM → Float32 → an `AudioBuffer` at the payload's
`sample_rate`/`channels` (raw PCM has no container header, so `decodeAudioData` cannot be
used) — plays chunks of one `event_id` in `chunk_num` order, and animates the mouth from
`marks[]` (or the audio envelope when a voice sends none). No server SDK is imported: the
browser is a protocol client, exactly like robot firmware. If the browser's autoplay policy
has the audio context suspended, the audio is queued and plays on the next user gesture.
Contract: [docs/architecture/sim-as-a-client.md](../../docs/architecture/sim-as-a-client.md);
tests: `sim/test_audio.mjs` + `sim/tests/test_sil.py`.

### Chunk order (and what happens when a chunk is lost)

A streamed turn arrives as several `CloudTTSResponse`s sharing one `event_id`, numbered by
`chunk_num`, and they must be *started* in that order — a child who hears the end of a
sentence before its middle is holding a broken toy. Keeping the queue sorted is not enough,
because the queue only holds what is still **waiting**: with short chunks and one MQTT
message per round trip, chunk 0 can finish and empty the queue before chunk 1 lands, and
chunk 2 — alone in the queue, therefore "first" — starts ahead of it. That is what the SIL
test caught (recorded order `[0,2,1]`), and it was pure timing: the identical code had
passed on a slower box the day before. So the **player** owns the order, not the queue:

| rule | what `audio.js` does |
|---|---|
| **ordering** | Within one `event_id`, chunk *n+1* starts only after chunk *n* has started, and an event's first chunk is `chunk_num` 0. A chunk that arrives ahead of its turn **waits**, however idle the player is. |
| **gap** | The wait is bounded by `TTS_GAP_MS` (1.2 s, measured from the moment the player ran dry). If the chunk it is waiting for has not arrived by then it is written off as lost and the lowest chunk in hand starts instead — a skipped sentence beats a robot that stops talking. A chunk that turns up *after* its slot has passed (a duplicate, or one already written off) is dropped as `{played:false, reason:"late"}` rather than played out of turn. |
| **event** | An event stays current for `TTS_EVENT_MS` (5 s) after its last chunk drained, then closes — the same `event_id` seen later is a **new** utterance, because a replayed session re-sends the very same ids and must not be silenced by the ordering rule. A chunk of a *different* event closes the current utterance at once (events stay FIFO) and releases anything still held for it as `{reason:"superseded"}`. A payload with **no** `event_id` is not part of a stream at all: it is a one-off and plays FIFO, unordered. |

The consequence worth remembering: the order chunks are STARTED in is ascending **by
construction**, not by luck, so `lastPlaybackStats().order` is a real assertion and not a
timing bet. `sim/test_audio.mjs` §6 drives all four arrival shapes (in order, out of order
across a silent gap, a shuffled burst, and a chunk that never arrives).

Playback is a live pipeline, so `audio.js` also **records** each utterance for anyone who
has to reason about it after the fact: `moxieAudio.lastMouthPeak()` is the loudest mouth
frame, and `moxieAudio.lastPlaybackStats()` returns
`{event_id, chunks_played, order:[chunk_num…], max_pending}` — the chunks that played, the
order they started in, and the deepest the queue ever got. Both reset when a **new
utterance** starts — a chunk of a different `event_id`, not merely the false→true `speaking`
edge, because a chunked utterance legitimately falls silent between chunks while it waits
for the next one, and the record has to survive that gap (`max_pending` is seeded from
whatever is already queued, so a burst that piled up while the context was still suspended
still counts) — and are frozen once playback ends.
`moxieAudio.ttsPending()` is the live gauge; the recorded stats are what the tests assert
on, because a short chunk drains before an outside observer can sample it.

**`#tts-status` has one owner: `audio.js`.** Two independent things want that line —
the live `🔊 speaking — cloud TTS …` indicator and the async probe in `env.js` that
reports whether the optional Piper sidecar is up. Writing it from both meant whichever
landed last won, so a probe resolving mid-utterance wiped the speaking indicator (and
was itself wiped when playback restored the pre-probe text). Anything else that wants
to say something there calls `moxieAudio.setTtsHint(hint)` — a plain string, or
`{text}`/`{html}` plus an optional `warn` — and `audio.js` paints it only while nothing
is speaking. `moxieAudio.hasCloudVoice()` reports whether a `CloudTTSResponse` has ever
arrived, so `env.js` stops claiming "no TTS server" when the server voice is the one
talking.

## JS control API (`window.moxie`)

Attached to `window` when the module loads; a `moxie-ready` CustomEvent fires
on `window` with the API in `event.detail`.

```js
moxie.setMotor(index, value)     // value 0..32767, animates smoothly to target
moxie.getMotor(index)            // current (smoothed) position, rounded int
moxie.setFace(expression)        // "neutral" | "happy" | "sad" | "surprised" | "thinking" | "blink"
moxie.setSpeech(text)            // speech bubble + mouth "talking" animation
moxie.setMouthOpen(0..1)         // external lip-sync drive (audio.js calls this while speaking)
moxie.getMouthOpen()             // current lip-sync drive (0..1)
moxie.setHeartLED(on, "#ff5577") // chest LED on/off, optional color
moxie.centerAll()                // every motor back to 16384 (extra convenience)
```

Motor values use the real hardware range: **0..32767** (`MOTOR_MAX_POS`), with
**16384** as the center/rest pose. Values are mapped piecewise-linearly to joint
angles, so center is always the rest pose even where the range is asymmetric
(an arm can swing much further up than it can tuck down).

## Motor index → joint

| index | joint | motion at low → high value |
|---|---|---|
| 0 | LEFT shoulder | left arm tucked down → raised up (~-20° → +109°) |
| 1 | LEFT elbow | left forearm out → folded in across the front (~-26° → +86°) |
| 2 | RIGHT shoulder | right arm tucked down → raised up |
| 3 | RIGHT elbow | right forearm out → folded in |
| 4 | HEAD tilt | face nods down → up (±16°; body leans along slightly) |
| 5 | BODY yaw | body turns right → left on the base (±60°) |
| 6 | BODY lean | leans back → forward (±17°) |

The rig is a tree of named `THREE.Group` pivots
(`yaw → lean → { head/face, shoulderL → elbowL, shoulderR → elbowR }`), one
group rotation per DOF. The base disc stays fixed while the body yaws/leans,
like the real robot. Elbow hinges are pre-tilted so folding "in" carries the
forearm slightly across the front, hug-style, instead of clipping the shell.

## Notes / assumptions

- There is no separate head ball on Moxie, so motor 4 tilts the face-screen
  about a pivot inside the shell and couples ~30% into the body lean.
- The face is drawn to a 512×512 canvas texture each frame (eyes, brows,
  mouth, blush), with an idle blink every few seconds. `setSpeech` overlays a
  mouth-flap animation for the bubble's duration.
- Gentle idle "breathing" sway is additive at render time and never disturbs
  the commanded motor values reported by `getMotor`.
