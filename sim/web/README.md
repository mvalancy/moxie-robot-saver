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
