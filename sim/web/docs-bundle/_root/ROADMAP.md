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
- A static hosted version on Cloudflare Pages with a real brain, voice and ears, a capacity
  indicator, and a scripted fallback when the gateway is down
  ([deploy guide](docs/guides/deploy-cloudflare.md)). Spending is held back by per-visitor rate
  limits and a request-unit budget, all best effort: each server isolate counts in its own memory,
  and each Cloudflare location (colo) also shares a count in a cache that admits whenever it fails.
  None of them is a global ceiling; only a budget on the gateway key can be one.
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
   stranger-facing defect. Shipped so far:
   - **Personality:** persona v2 (identity first, the child as her mentor, the habits of her
     self-talk, honest senses), sent once instead of again after the child's line, so she answers
     the newest line ([spec §4.11](docs/architecture/backlog/live-sim-demo.md)).
   - **Goodbye:** a goodbye closes the turn with a sign-off wave instead of a new question.
   - **Voice:** one voice per reply, never the browser's voice cut off by hers; her first sentence
     plays as soon as it is synthesized and the rest follow in order; every pre-recorded clip is
     in that same voice.
   - **Ears:** a tap with nothing said is never sent as a turn, and one turn is heard at a time.
   - **Stub and degraded states:** when the brain is away the scripted stub answers in character,
     with clips, and never shows a scripted line as the child's words; the page says honestly
     whether the brain is down, busy or resting.
   - **Ambient:** creepy-cute self-talk between turns, one row at a time on a toy-first screen.

   Still open: two turns in flight and talking over her (barge-in); a check on what she says as
   well as on what she is told; growing the creature (seasonal lines, a rare glitch, an aside
   after goodbye); a lighter hub page on a phone; and, on the robot path, a goodbye that ends a
   content module's chat.
2. **Spending protection.** The rate limits and the unit budget are counted per isolate and per
   colo and fail open, so they are not a global ceiling. Confirm a hard budget on the gateway key
   before claiming one. Also open: a per-visitor day for the voice and the ears (they have only a
   minute and an hour), keying IPv6 by a wider prefix than the /64 (one home holds many),
   keeping superseded deployment URLs from spending, a log line per refusal, and the bot check
   (built, and off on the reference deployment).
3. **Answer quality on the hosted demo.** Run the grounding check with a real negative control
   ([brief](docs/architecture/backlog/live-brain-open-issues.md)).
4. **A second brain for the demo.** Today one gateway outage silences it. (A failing model does
   not: the reference gateway falls back to a second model under the same alias.) Needs a second
   credential and an owner cost decision ([brief](docs/architecture/backlog/live-brain-open-issues.md)).
5. **Parent app depth.** Partly done: unpair and factory reset are in the web app, behind a typed
   confirmation ([what is built](docs/features/robot-lifecycle.md#built-here-unpair-and-factory-reset)).
   A robot paired by scanning the codes now joins the account with one click, **Add to my
   account**, which gives it the robot card and Unpair; the Wi-Fi tab's first code is Wi-Fi only
   ([bench runbook](docs/guides/bench-runbook.md)). Still open: all of it is tested against the
   simulator and hermetic doubles only; no physical robot has been added, unpaired or reset this way.
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
