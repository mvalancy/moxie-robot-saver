# 🏗️ Architecture

How all the pieces fit together — and, below, the **build contracts** a clean-room implementation works
from. Start with the two orientation docs, build from the contracts, and consult the vision/plan docs
for the bigger picture.

## Orientation
- [`overview.md`](overview.md) — the two-channel model, the components, the appliance vision, privacy.
- [`revival-path.md`](revival-path.md) — the exact steps + firmware gate to revive a robot (the 3-QR sequence).

## The build contracts — *what to implement*
Versioned, standalone specs distilled from the [reverse-engineering study](../reverse-engineering/README.md).
A backend + Sim are built from these directly; each cites the study but reads on its own.
**Now building:** see [`implementation-plan.md`](implementation-plan.md) — the roadmap + honest status.
- [`orchestration-plan.md`](orchestration-plan.md) — **how the work gets done**: the three top-level outcomes, the orchestrator + Opus-agent protocol, workstreams, and the 24/7 layered loops.
- [`rest-api-contract.md`](rest-api-contract.md) — **Channel 1, the control plane**: the REST services the
  parent-app server exposes (auth, children, pairing, robot settings) + the minimum-viable-server path.
- [`mqtt-and-conversation.md`](mqtt-and-conversation.md) — **Channel 2, the robot cloud**: endpoint QR, the
  MQTT broker, topics, conversation flow, and the local-AI plug-in points.
- [`ai-seam.md`](ai-seam.md) — **the AI interface**: the three seams a backend fills (STT in /
  brain-RemoteChat / TTS out) — exact wire shapes + a conformance checklist. Build any AI into Moxie from this.
- [`config-and-telemetry-contract.md`](config-and-telemetry-contract.md) — the robot's **remotely-managed
  state**: the `/config` (`RobotCloudConfig`) pushed down, the `/state` reported up, and the telemetry +
  `LoggingPolicy` privacy gate — the data model behind the parent console.
- [`content-module-contract.md`](content-module-contract.md) — the **content layer**: the activity/module
  JSON format (conversations/globals/schedules), the per-turn `volley`/`session` API, and execution
  actions — how a server defines what Moxie *does*, on top of the AI seam.
- [`sim-as-a-client.md`](sim-as-a-client.md) — **the SIM is just another backend client**: interchangeable
  with a real robot, what it substitutes vs what's contract-identical, and the one TTS divergence.

## Vision, plans & research
- [`moxie-as-a-platform.md`](moxie-as-a-platform.md) — **the SDK**: how any AI/game drives Moxie as an avatar.
- [`moxie-ecosystem.md`](moxie-ecosystem.md) — the full self-hostable stack build plan: brain, voice, ears, liveness.
- [`openmoxie-feature-audit.md`](openmoxie-feature-audit.md) — **measured against the state of the art**: a
  cited, feature-by-feature audit of [OpenMoxie](https://github.com/jbeghtol/openmoxie) (MIT) and its active
  forks, classified HAVE / ADOPT / BEYOND, with a ranked backlog, a per-item **status** column, and an
  honest list of where they're ahead.
- [`backlog/`](backlog/README.md) — **the build briefs**: a ranked audit line turned into something an agent
  can execute — seam, cited vocabularies, design, tests, acceptance criteria, risks. Currently
  [`backlog/expressiveness.md`](backlog/expressiveness.md) (the markup floor + the behavior planner),
  [`backlog/security-broker-auth.md`](backlog/security-broker-auth.md) (broker ACL → device credentials →
  spoof-proofing, phased) and [`backlog/telehealth.md`](backlog/telehealth.md) (puppet mode: the
  `commands/telehealth` path + the "Be Moxie" console panel), [`backlog/voice-picker.md`](backlog/voice-picker.md)
  (Speech + Listening dropdowns in the console, fed by live gateway discovery + installed local engines),
  [`backlog/content-packs.md`](backlog/content-packs.md) (content packs: export, import-with-review,
  and a `source_version` upgrade that never clobbers a locally edited item), and
  [`backlog/live-sim-demo.md`](backlog/live-sim-demo.md) (**the headline goal** — the hosted Sim alive on a
  static edge: same-origin Cloudflare Functions for brain, voice and ears, behind hard caps, degrading to the
  pre-cached scripted Moxie when the gateway is unconfigured, over budget, at capacity or down) and
  [`backlog/sandboxed-extensions.md`](backlog/sandboxed-extensions.md) (sandboxed content extensions —
  a declarative rule list over a total JSON-AST expression language, capability-scoped and reviewable in
  plain English, so a shared pack can *do* something without trusting its author) and
  [`backlog/brain-picker.md`](backlog/brain-picker.md) (any brain, hot-swappable, per child — a closed
  positive registry of brains, per-robot selection layered like every other config value, a live swap
  with no restart, and an explicit `MOXIE_APP` that pins the appliance's own), and
  [`backlog/production-hardening.md`](backlog/production-hardening.md) (production hardening — the
  cross-process store decision made rather than deferred, MQTT reconnection, and a soak that stands in
  for a week in a house), and [`backlog/content-authoring.md`](backlog/content-authoring.md) (content
  authoring — the verb packs did not ship: where authoring lives, what a non-programmer may and may not
  write, the edit → hear it → keep it loop priced in gateway calls, and why an authored item is exactly
  as untrusted as an imported one), and [`backlog/qr-launch-cards.md`](backlog/qr-launch-cards.md)
  (printable launch cards — a `GO<launch:MOD>` card a child holds up to Moxie, re-scoped from the
  audit's **S** to an **M** after the row's premise turned out to be a real mechanism pointed the wrong
  way: arming the robot's *runtime* QR reader, routing the scanned value into a launch against a closed
  allowlist, and only then the sheet a parent prints), and
  [`backlog/insights.md`](backlog/insights.md) (insights that mean something — where a parent-facing
  vocabulary comes from when `Packet.event_name` is a free string, which questions a card can honestly
  answer, and which two charts it refuses to draw), and
  [`backlog/mobile-first-visit.md`](backlog/mobile-first-visit.md) (measured against production and now
  the top live-page item: on a 390 px phone the Talk box is `0 × 0` on load and still at y = 2 095 after
  the `CONTROLS` drawer opens, while Moxie speaks unprompted at ~7 s — she talks and the visitor cannot
  answer; **not broken, buried**, and the turn completes once scrolled to), and
  [`backlog/vendor-the-readme-hero.md`](backlog/vendor-the-readme-hero.md) (**shipped 2026-09-04** —
  the one defect that had stood between the live site and a clean browser console: the README's hero
  image was hosted on `github.com`, which `img-src 'self' data: blob:` correctly refuses. Vendored
  rather than allow-listed, and the guard against the next one — `sim/tests/test_no_offsite_images.py`
  — is worth more than the image). One page there is the odd one out and
  deliberately so:
  [`backlog/community-signals.md`](backlog/community-signals.md) — **the inbound half**, what owners
  holding a real Moxie report on public trackers and forums, cited by URL and date and ranked by how
  strong the evidence is rather than how good the feature sounds.
- [`static-experience.md`](static-experience.md) — the **combined static site**: parent app + simulator +
  cloud UI on Cloudflare Pages, and the roadmap to the real end-to-end system.
- [`sil-and-cicd.md`](sil-and-cicd.md) — the simulator's design + the test/CI layers that guard it.
- [`vision.md`](vision.md) — can Moxie *see*? Camera access reality + a local OpenCV/VLM vision stack (research).

---
📖 [Docs index](../README.md) · [Back to top](../../README.md)
