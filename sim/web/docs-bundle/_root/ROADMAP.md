# Roadmap

The goal: revive any Moxie robot on hardware its owner controls, and let any AI be its brain.
This page says what works, what is next, and what is blocked. Detailed designs for open items live
in [`docs/architecture/backlog/`](docs/architecture/backlog/README.md).

## Definition of done

The system is done when all six hold together:

| # | Criterion | Status |
|---|---|---|
| 1 | A child can talk to Moxie end to end: mic, speech-to-text, brain, markup, voice, and the Sim speaks and moves | Done in the Sim, with a real brain and real voice. |
| 2 | Content is data: activities are authored modules the brain runs | Done. |
| 3 | Cloud management: the parent console shows robot state, edits config, shows insights, and honors the `LoggingPolicy` privacy setting | Done. |
| 4 | Interchangeable clients: the Sim and a real robot connect to the same backend the same way | **Half done.** The Sim works; no physical Moxie has connected to this broker yet. |
| 5 | One command: `docker compose up` runs broker, supervisor, brain and speech | Done (voice and speech-to-text are opt-in profiles). |
| 6 | Green and tested: every feature has a test, CI is green, and a live test passes against a real gateway when keys are present | Done; CI on `dev` and `main` is green. |

Criterion 4 cannot be closed without a robot on the bench.

## What works today

**Parent app (control plane)** — `server/`
- Account-free reimplementation of the parent-app REST API, and a phone web app served on the LAN.
- Wi-Fi pairing QR, **verified on a real Moxie** (it scanned the code and joined the network).
- Recovery-key crypto matched to the original app, and a hardware-free pairing test.
- Unpair and factory reset: the robot leaves the account, stops being served and old pairing codes
  stop working; a reset then shows the robot's own `restore_factory` setup code.

**Robot cloud** — `mqtt/`
- Mosquitto broker with TLS, per-appliance certificates, per-robot ACLs and a device permit list.
- Endpoint QR generator (`tools/pairing/moxie_endpoint_qr.py`) that points a robot at your broker.
- Supervisor that speaks Moxie's protocol: connect detection, config push, `/state` and telemetry
  ingest, streamed conversation turns with a filler line while the brain thinks, input and output
  safety checks, and brain-driven actions (launch, exit, sleep).
- Brain: any OpenAI-compatible endpoint, chosen per child. No endpoint is built in; you configure one.
- Voice: a built-in tone, local Piper, or a gateway voice. Ears: local Whisper or a gateway.
  Both are picked in the console.
- Content modules, memory (`persist_data` and end-of-session summaries), an adaptive day plan,
  content packs (export and import with review) and a content editor.
- Expressive markup: every reply gets moods, gestures and behaviors.
- Remote puppet ("Be Moxie") mode.

**Parent console** — robot state, settings, insights, memory browser, voice picker, and erase
controls that respect the privacy setting.

**Packaging** — one `docker compose up`; multi-arch images (`amd64`, `arm64`) published to GHCR on
release tags, with a no-clone compose file ([guide](docs/guides/one-command-stack.md)).

**Simulator and hosted demo** — `sim/`, `functions/`
- A 3D Moxie in the browser that speaks the real protocol, plus a virtual robot for tests.
- A static hosted version on Cloudflare Pages with a real brain, voice and ears, per-visitor and
  global rate limits, a capacity indicator, and a scripted fallback when the gateway is down
  ([deploy guide](docs/guides/deploy-cloudflare.md)).
- Setup page, example parent console and a docs explorer, all served from the same site.

## Unproven: needs a real robot

Everything below is built and tested against the Sim, but no physical robot has exercised it.

- A re-homed robot (firmware 24.10.801/803) connecting to our broker and holding a conversation.
- Our markup and moods played on the robot's own face and body.
- On-device vision events (face found or lost) driving greetings.
- Puppet mode.
- Unpair and factory reset on the robot itself: what Moxie shows when it is sent the not-paired
  settings, and whether the `restore_factory` code resets it.
- Reflashing an older (pre-801) robot to 803 with `rkdeveloptool`. The method and a signed image are
  documented in [`hardware/firmware-and-older-robots.md`](hardware/firmware-and-older-robots.md).

## Next

Ordered by priority.

1. **First visit to the hosted Sim.** Walk the full path (instructions, microphone permission or
   refusal, waiting, reply, interruption, second turn, goodbye, degraded mode) and fix the worst
   stranger-facing defect.
2. **Spending protection.** The edge rate-limit counters are per-colo and fail open, so they are not a
   global ceiling. Confirm a hard budget at the gateway before claiming one.
3. **Answer quality on the hosted demo.** Run the grounding check with a real negative control
   ([brief](docs/architecture/backlog/live-brain-open-issues.md)).
4. **A second brain for the demo.** Today one gateway outage silences it. Needs a second credential
   and an owner cost decision ([brief](docs/architecture/backlog/live-brain-open-issues.md)).
5. **Parent app depth.** Partly done: unpair and factory reset are in the web app, behind a typed
   confirmation ([what is built](docs/features/robot-lifecycle.md#built-here-unpair-and-factory-reset)).
   Still open: a robot paired by scanning the QR gets no account record, so its robot card and
   Unpair button do not appear (for it, unpair is Revoke in Robot access today), and no physical
   robot has been reset this way.
6. **Storage.** Per-robot state is JSON files. That is fine for one home; move to a database only if
   multi-process access needs it.

## Deliberately not now

- **OTA push** (the 801-to-803 upgrade over the air). Specified, not built, on purpose
  ([brief](docs/architecture/backlog/ota-push.md)).
- **Gamified missions in the public Sim.** The owner chose a simple meet-Moxie chat
  ([evidence](docs/architecture/backlog/gamify-the-public-sim.md)).
- **Pointing the original Android app at this server.** Not planned; the web app replaces it.
- **Multi-tenant hosting for other owners.** Not planned; self-hosting is the model.

## Research tracks

- **Moxie sees.** Use on-device vision events first; an external camera with a local vision model
  later. The robot's own camera frames are not reachable in stock firmware.
  See [`docs/architecture/vision.md`](docs/architecture/vision.md).
- **Older robots.** Pre-801 firmware pins the cloud address and cannot be moved by QR. The known
  route is a reflash; bootloader and verified-boot details are in the hardware docs.

## Where it runs

Any machine on your home network: a PC, a home server, a Raspberry Pi 4/5 (control plane and
gateway-backed speech), or a GPU box such as a Jetson Orin for fully local speech and models.
