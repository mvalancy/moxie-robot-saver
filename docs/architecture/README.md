# Architecture

How the replacement backend is put together, and the contracts it is built from. The contracts are
distilled from the [reverse-engineering study](../reverse-engineering/README.md) and each one reads on
its own.

## Start here

- [`overview.md`](overview.md) — the two channels (parent app and robot cloud), the components, and
  privacy.
- [`revival-path.md`](revival-path.md) — how a real robot gets onto this backend, by firmware version.
- [Roadmap](../../ROADMAP.md) — what is built and what is left.
- [`agent-workflow.md`](agent-workflow.md) — how changes are made: hard rules, the agent brief,
  integration rules, session loops.

## Build contracts

| Contract | What it specifies |
|---|---|
| [`rest-api-contract.md`](rest-api-contract.md) | Channel 1: the parent-app REST services (auth, children, pairing, robot settings). |
| [`mqtt-and-conversation.md`](mqtt-and-conversation.md) | Channel 2: the endpoint QR, broker, MQTT topics and the conversation flow. |
| [`ai-seam.md`](ai-seam.md) | The three AI seams a backend fills — speech-to-text, the brain (`RemoteChat`), text-to-speech — with wire shapes and a conformance checklist. |
| [`config-and-telemetry-contract.md`](config-and-telemetry-contract.md) | The robot's managed state: `/config` pushed down, `/state` reported up, telemetry and the `LoggingPolicy` privacy gate. |
| [`content-module-contract.md`](content-module-contract.md) | The content layer: the module JSON format, the per-turn `volley`/`session` API, and execution actions. |
| [`sim-as-a-client.md`](sim-as-a-client.md) | Why the simulator is interchangeable with a real robot, and where it differs. |

## Platform, research and the hosted site

- [`moxie-as-a-platform.md`](moxie-as-a-platform.md) — the SDK: how any AI or game drives Moxie.
- [`openmoxie-feature-audit.md`](openmoxie-feature-audit.md) — OpenMoxie compared feature by feature:
  what we have, what to adopt, where to go beyond.
- [`static-experience.md`](static-experience.md) — the static site on Cloudflare Pages: simulator,
  setup page, example console.
- [`sil-and-cicd.md`](sil-and-cicd.md) — the simulator's design and the CI tiers that guard it.
- [`vision.md`](vision.md) — can Moxie see? Camera reality and a local vision stack (research).

## Backlog briefs

Design briefs for larger items, one per page; [`backlog/README.md`](backlog/README.md) has the status
table.

| Brief | Topic |
|---|---|
| [`live-sim-demo.md`](backlog/live-sim-demo.md) | The hosted simulator with a real brain, voice and ears |
| [`expressiveness.md`](backlog/expressiveness.md) | Behavior markup and the behavior planner |
| [`telehealth.md`](backlog/telehealth.md) | Puppet mode: an operator drives Moxie |
| [`production-hardening.md`](backlog/production-hardening.md) | Reconnection, a shared store, and a soak test |
| [`security-broker-auth.md`](backlog/security-broker-auth.md) | Broker ACL, device credentials, spoof-proofing |
| [`content-packs.md`](backlog/content-packs.md) | Exporting and importing content packs |
| [`content-authoring.md`](backlog/content-authoring.md) | Authoring content without programming |
| [`sandboxed-extensions.md`](backlog/sandboxed-extensions.md) | Content packs that can run rules safely |
| [`voice-picker.md`](backlog/voice-picker.md) | Choosing speech and listening models in the console |
| [`brain-picker.md`](backlog/brain-picker.md) | Any brain, hot-swappable, per child |
| [`qr-launch-cards.md`](backlog/qr-launch-cards.md) | Printable QR cards that start an activity |
| [`insights.md`](backlog/insights.md) | Parent insights built from real activity |
| [`mobile-first-visit.md`](backlog/mobile-first-visit.md) | The first visit on a phone |
| [`turnstile-layout-collision.md`](backlog/turnstile-layout-collision.md) | A mobile layout collision with the bot challenge |
| [`vendor-the-readme-hero.md`](backlog/vendor-the-readme-hero.md) | Hosting the README image in the repo |
| [`gamify-the-public-sim.md`](backlog/gamify-the-public-sim.md) | Evidence on making the public simulator a game |
| [`visemes.md`](backlog/visemes.md) | Lip-sync from phoneme timings |
| [`ota-push.md`](backlog/ota-push.md) | Firmware push: the spec, and why not to build it yet |
| [`live-brain-open-issues.md`](backlog/live-brain-open-issues.md) | Open issues with the hosted brain: `<exit>` tags, grounding, a single provider |
| [`test-timing-under-load.md`](backlog/test-timing-under-load.md) | Tests that fail under load: smoke, head sweep, head travel |
| [`community-signals.md`](backlog/community-signals.md) | What real owners report, ranked by evidence |

---
[Docs index](../README.md) · [Project README](../../README.md)
