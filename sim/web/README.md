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
| `index.html` | the hub / landing page (`home.js`, `bg.js`, `wire-bg.js`) |
| `sim.html` | the SIM: the WebGL stage, the engineering rail (`<aside id="panel">`) and `#chat-dock`, the composer — the HUD grid's bottom row at every width, holding the cue line, `#transcript` and `#speech-input` + `#mic-btn` + `#speech-btn`. Those nodes live ONLY here (everything binds by id; a second text box is the trap [`mobile-first-visit.md`](../../docs/architecture/backlog/mobile-first-visit.md) names). Its one inline block is the importmap the CSP hashes |
| `moxie.js` | entry module: renderer, scene, rig, `window.moxie` API, animation loop |
| `moxie/config.js` | motor table (`MOTOR_DEFS`), rest pose, `motorAngle`, the spring-elbow curve |
| `moxie/geometry.js` | pure geometry: body lathe profile, arm shells, egg head, face panel |
| `moxie/textures.js` | canvas textures: head ears, grille, wordmark, heart LED, glows, debug labels |
| `moxie/face.js` | expressions, the canvas face, icon badges, blink/easing |
| `moxie/liveness.js` | additive idle micro-motion + gaze drift (deliberate idle beats are `life.js`) |
| `moxie/bubble.js` | speech bubble: typewriter, head/chest anchoring, `window.__bubbleAnchor` for tests |
| `moxie/stage.js` | camera framing inside the part of the viewport the dock/rail leave free |
| `moxie/panel.js` | the by-hand controls: motor sliders, expression chips, speech box, heart LED |
| `bridge.js` | MQTT→avatar bridge: drives `window.moxie` from `remote_chat` markup, `commands/tts` (the server voice), motors, telehealth, `response_actions`; publishes the activity log |
| `audio.js` | sound: UI SFX, pre-cached/Piper/browser voices, and `playCloudTTS` (base64 16-bit PCM with lip-sync) |
| `style.css` + `css/hud.css`, `css/dock.css`, `css/rail.css` | the mission-control HUD skin ([style guide](../../docs/design/style-guide.md)); sim.html links hud → dock → rail → style.css, and that order is the cascade |
| `mode.js` | what this deployment can DO: polls same-origin `GET /api/health` and publishes `window.moxieMode` (`live` / `degraded` / `offline`, reason, capacity) — [spec §6.3/§7](../../docs/architecture/backlog/live-sim-demo.md) |
| `turnstile.js` | browser half of the bot control: `window.moxieTurnstile.getToken(action)`, one widget per spending route, a fresh single-use token per send, inert unless `/api/health` reports a sitekey. Every failure resolves `null`, which `cloud-transport.js` turns into an honest sentence (never a silent dead Send) |
| `env.js` | the honest indicator: env badge, capacity pill, `needs-backend`/`dead` marks and the hosted banner — painted from the mode, not the hostname; lifts the banner clear of bottom-anchored chrome (`--eb-lift`) |
| `mic.js` | the ears: records and posts to `/api/transcribe` (hosted, 16 kHz WAV it encodes itself, capped at `DEMO_MAX_RECORD_MS`) or the local STT sidecar; failures fall back to a scripted line through the free `sendScriptedTurn` |
| `cloud-transport.js` | the live turn: `sendUserTurn` → same-origin `/api/chat` + `/api/speech` when `live` (TTS routed first: one voice); owns which control carries a typed line (`adopt()`), and `sendScriptedTurn` |
| `ambient.js` / `ambient.json` | her self-talk between turns (never over a live answer) |
| `life.js` | ALIVE mode: idle beats through the real motor targets, backing off joints a user holds |
| `diagram.js` | renders a mermaid diagram she drew into the log (lazy, same-origin, `securityLevel: strict`) |
| `stub.js` | offline stand-ins (brain replies with real markup, scripted STT) for a fully static deploy |
| `qr.js` | revival QR payloads + launch cards, byte-identical to the Python toolkit (`sim/test_qr.mjs`) |
| `hud.js`, `rail.js`, `sw-reset.js` | sim.html glue: panel wiring + openers, the phone rail drawer, stale service-worker self-heal |
| `home.js`, `setup.js`, `cloud.js`, `docs.js`, `bg.js`, `wire-bg.js`, `moxie-wire.js` | the hub, setup, cloud-console and docs pages and their backgrounds |
| `_headers` | Cloudflare Pages cache + security headers for the static pages (CSP, HSTS, nosniff…). The header comment is the rationale for every CSP host; the importmap hash is generated by [`../tools/build_csp_hashes.py`](../tools/build_csp_hashes.py). Every app script needs a `no-cache` entry (subdirectories: one `/dir/*` rule) — `sim/test_csp.mjs` enforces it. Does NOT apply to Pages Function responses (`functions/api/_lib/envelope.js` sets those) |

All scripts are same-origin files so `script-src` needs no `'unsafe-inline'`; classic scripts
keep their document order (no `defer`/`async`).

## What this deployment can do (`mode.js` + `window.moxieMode`)

The page used to decide everything from the **hostname**: any non-local host was assumed
to have no backend, so every visitor was told *"hosted demo — only pre-scripted lines have
audio"* whether it was true or not, and it never re-checked. Now `mode.js` asks one
same-origin route and `env.js` paints the answer.

| state | when | what the visitor gets |
|---|---|---|
| `offline` | `/api/health` is not there at all — a fork with no Pages Functions, a plain CDN, `file://`, a 404 | **Byte-identical to the site as it shipped**: `HOSTED DEMO`, stub + clips, and nothing is polled again this session |
| `degraded` | the route exists and answered honestly — nothing configured, over budget, or the brain is unreachable | The same page, plus the reason on screen: a badge suffix and a pill. `gateway_not_configured` keeps today's exact copy and fires exactly **one** request |
| `live` | a brain is configured and reachable | `MOXIE ONLINE`, and the page stops claiming the mic needs a locally-run server, because with a same-origin route that claim is false |

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
browser, asserted at the Web Audio layer), `sim/test_mic_spend.mjs` (a refused microphone
consoles the visitor audibly and buys nothing), `sim/test_mobile_layout.mjs` (phone hit
tests) and `sim/test_csp.mjs` (the shipped security headers, applied).

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
moxie.setFace(expression)        // any of moxie.expressions (11 moods + sleep/thinking) or "blink"
moxie.setSpeech(text)            // speech bubble + mouth "talking" animation
moxie.setMouthOpen(0..1)         // external lip-sync drive (audio.js calls this while speaking)
moxie.getMouthOpen()             // current lip-sync drive (0..1)
moxie.setHeartLED(on, "#ff5577") // chest LED on/off, optional color
moxie.showIcons([...]) / clearIcons()  // up to 4 icon badges over the face
moxie.centerAll()                // every motor back to its rest pose
moxie.setIdle(bool) / isAlive() / isUserHeld(i)  // liveness + life.js hooks
moxie.setSceneLight(0..1)        // 0 = dark room lit by the projected face
moxie.setShowAxes(bool)          // debug: labelled axis triads on every rig node
```

Motor values use the real hardware range **0..32767** (`MOTOR_MAX_POS`), **16384** = center.
Values map piecewise-linearly to joint angles, so center is the rest pose even where the range
is asymmetric.

## Motor index → joint

Source of truth: `MOTOR_DEFS` in [`moxie/config.js`](moxie/config.js).

| index | joint | motion at low → high value |
|---|---|---|
| 0 | LEFT shoulder up/down | arm slightly back → raised up (~-17° → +109°) |
| 1 | LEFT shoulder in/out | against the body (rest = 0) → swung out (~60°); the spring elbow folds as it swings clear |
| 2 | RIGHT shoulder up/down | as 0, mirrored |
| 3 | RIGHT shoulder in/out | as 1, mirrored |
| 4 | HEAD tilt | nod (±22°) |
| 5 | BODY yaw | turns on the base (±60°) |
| 6 | BODY lean | upper torso leans back ↔ forward at the chest seam (±16°); the speaker section stays planted |

The elbows have no motor: `springElbowFromMotor` derives the fold from the in/out axis (the body
holds the forearm straight at rest). The rig is a tree of named `THREE.Group` pivots
(`yaw → breathe → { lower torso, lean → upper torso → { head tilt → face, arms } }`); the base
disc stays fixed. `moxie.setShowAxes(true)` labels every pivot.

## Notes

- The face is drawn to a 512×512 canvas texture each frame (eyes, brows, mouth, blush, icon
  badges); `setSpeech` overlays a mouth-flap for the bubble's duration.
- Liveness (breathing, micro-sway, gaze drift) is additive at render time and never disturbs the
  commanded values `getMotor` reports.
