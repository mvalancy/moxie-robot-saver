# 🖥️ The SIM as a backend client — interchangeable with a real robot

> **Spec version 1.** How the software-in-the-loop simulator (the [SIL](sil-and-cicd.md)) consumes our
> backend. The point of this spec: **the SIM is not a special case** — it is another *client* of the
> exact same contracts a real robot speaks ([AI seam](ai-seam.md), [config & telemetry](config-and-telemetry-contract.md),
> [content modules](content-module-contract.md), over [MQTT](mqtt-and-conversation.md)). Build the
> backend to those specs and the SIM and a re-homed Moxie are drop-in replacements for each other.
> Reads standalone; cites the study for provenance.

## The interchangeability guarantee

The backend speaks **only** the reverse-engineered MQTT/JSON/markup protocol — never anything
SIM-specific. Therefore:

```mermaid
flowchart LR
  be["🧠 our backend<br/>(mqtt/ + server/ + ai/)"] <-->|"same MQTT topics<br/>same contracts"| broker["📡 broker"]
  broker <-->|"MQTT-over-WebSocket"| sim["🖥️ SIL: browser 3D Moxie"]
  broker <-->|"MQTT/TLS"| real(["🤖 re-homed Moxie"])
  classDef s fill:#0e0e14,stroke:#00f0ff,color:#e8edf5;
  class be,broker,sim,real s;
```

**Anything proven in the sim works on hardware, and vice-versa** — because both clients subscribe to
the same topics and honor the same request/response contracts. The SIM is the test/display surface;
the robot is production; the backend cannot tell them apart at the protocol layer.

## What the SIM substitutes vs what's identical

| Concern | Real robot | SIM | Same contract? |
|---|---|---|---|
| Transport | MQTT/TLS :8883 | MQTT-over-WebSocket :9001 (same broker, same topics) | ✅ topics identical |
| Config | `/config` `RobotCloudConfig` | consumes the same `/config` | ✅ [config contract](config-and-telemetry-contract.md) |
| Status/telemetry | `/state` `RobotStatus` | publishes a synthetic `/state` | ✅ same shapes |
| Brain / turn | `RemoteChatRequest`↔`Response` | identical | ✅ [AI seam ②](ai-seam.md) |
| Content/activities | server-side modules | identical | ✅ [content contract](content-module-contract.md) |
| Behavior markup | drives face/motors | drives WebGL face/arms/head/body | ✅ same `<mark cmd:…>` |
| Body / render | physical DLP face + 7-DOF motors | WebGL 3D avatar | ⛔ client-side only (not a backend concern) |
| Mic (STT in) | XMOS → audio bus | browser mic → same STT seam | ✅ [AI seam ①](ai-seam.md) |
| **Voice (TTS out)** | **on-device** — server sends text+markup, robot synthesizes | **browser plays the server's `CloudTTSResponse`** through Web Audio | ⚠️ **see below** |

Everything above the render layer is contract-identical. Only the **body render** (purely client-side)
and the **TTS boundary** differ.

## The one divergence a spec must call out: TTS

On a real robot, TTS is **on-device** — the backend sends *text + behavior markup* and Moxie speaks it
([perception-pipeline](../reverse-engineering/runtime/perception-pipeline.md), [ai-seam ③](ai-seam.md)).
A browser has no on-device Moxie voice, so the SIM needs **audio it can play**. Two options, both
compatible with the AI seam's TTS contract:

1. **Server-side TTS** (Piper → PCM) rendering the same `CloudTTSResponse{audio, marks}` the seam
   defines — the browser plays the PCM and lip-syncs from the `TTSMark`s. This is the path that also
   lets a real robot use a server voice. **This is the one we built** (see below).
2. **Pre-rendered audio** for scripted demos (the SIM can talk with no backend at all).

> **Implication for the backend:** to drive the SIM's voice you implement AI-seam **③ TTS out** (which
> a real robot doesn't require, since it self-synthesizes). This is the *only* backend capability the
> SIM needs that a real robot doesn't — so build TTS-out anyway and both clients are covered.

### What each SIM client does with a `CloudTTSResponse`

The supervisor synthesizes the turn and publishes a `CloudTTSResponse` on
`/devices/{id}/commands/tts` ([AI seam ③](ai-seam.md)). Both SIM clients consume that one message:

| client | module | what "playing" means |
|---|---|---|
| headless SIL robot | `sim/virtual_moxie.py::_play_tts` | decodes + records that Moxie spoke (bytes, rate, marks) — asserted by `sim/run_smoke.sh --expect-tts` |
| browser SIM | `sim/web/audio.js::playCloudTTS` (routed in by `bridge.js`) | **real sound** through the shared Web Audio context, with the face's mouth animating while it plays |

**The decode contract.** `AudioBuffer{buffer, channels, sample_rate}` +
`TTSMark{time, start, end, type, value}` — a client must honor all of this itself:

- `audio.buffer` is base64 of **raw little-endian signed 16-bit PCM**. It is *not* a container: there
  is no RIFF/OGG header, so `decodeAudioData()` cannot read it. The client builds the audio buffer by
  hand — `sample / 32768` → Float32, de-interleaved by `channels`, played at `sample_rate`.
- **Chunked replies:** chunks sharing an `event_id` play in `chunk_num` order (a serial queue), so a
  streamed line is heard as one utterance even if the chunks arrive out of order.
- **Lip-sync:** `marks[]` drive the mouth when present (viseme/word marks, timed by `time` in ms from
  the start of the utterance); with no marks the mouth follows the audio's own envelope, so the face
  still visibly speaks. Marks are recommended, never required.
- **Never throw:** a missing `sample_rate` defaults to 24000, an odd trailing byte is dropped, junk
  base64 decodes to silence. A client that throws on bad audio is a client that goes mute.

**The SIM decodes the wire itself** — it never imports the server SDK (`moxie_sdk.tts`), exactly like
robot firmware. That is the interchangeability guarantee doing real work: the same bytes reach a
browser and a robot, and neither client shares code with the server. Guarded by `sim/test_audio.mjs`
(which also round-trips the browser decoder against the *real* server encoder) and by the Playwright
tests in `sim/tests/test_sil.py`.

**One browser constraint a robot doesn't have:** a page may not make sound before a user gesture
(the autoplay policy). The SIM **queues** the audio and plays it on the next click/keypress rather
than dropping it, and the existing sound toggle mutes it. The backend is unaffected — this is entirely
client-side, like the body render.

## How the SIM connects

The browser subscribes MQTT-over-WebSocket to the **same topics the robot sees**, so the avatar
animates from the **real** `remote_chat` replies + markup + motor commands — a live window into the
bus, not a mock. The virtual robot (`sim/virtual_moxie.py`) publishes `/state` and consumes `/config`
and commands exactly as hardware would. Build/run detail + the 3D model reference:
[`sil-and-cicd.md`](sil-and-cicd.md).

## Conformance

- [ ] The backend publishes only standard contracts (config/state/remote_chat/markup) — nothing SIM-specific.
- [ ] The SIM subscribes to the same MQTT topics and honors the same config/turn/markup contracts as a robot.
- [ ] A backend built to the [AI seam](ai-seam.md) + [config](config-and-telemetry-contract.md) +
      [content](content-module-contract.md) specs runs the SIM with **zero backend changes**; only the
      client render + audio playback are SIM-side.
- [ ] AI-seam **③ TTS out** is implemented server-side (the SIM's only extra need vs a real robot).
- [ ] The SIM plays that `CloudTTSResponse` by decoding the raw PCM **client-side** (no server-SDK
      import), in `chunk_num` order, lip-syncing from `marks[]` when they are present.

Where it lives: [`../../sim/`](../../sim/) (the SIL client + web UI) talking to [`../../mqtt/`](../../mqtt/)
(the backend). Same backend, two interchangeable clients.

---
📖 [Docs index](../README.md) · [SIL design & build plan](sil-and-cicd.md) · [AI seam](ai-seam.md) · [Ecosystem build plan](moxie-ecosystem.md)
