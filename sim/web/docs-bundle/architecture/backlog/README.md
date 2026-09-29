# Backlog: design notes and proposals

Each page here covers one feature that was big enough to need its own design. Most have shipped, and
their pages are now **design notes**: what the feature does, how it works (with the key files), the
decisions that matter and why, known limits, and how it is tested. Pages for work that has not shipped
are short **proposals**: the problem, the approach, and the open questions.

What to build next is ranked in the [OpenMoxie feature audit](../openmoxie-feature-audit.md). This
folder holds the design behind a ranked line once it needs more than a line.

## Index

Status was checked against the code on 2026-09-28. Each page names its implementing files and tests
in its first lines. When the page and the code disagree, the code wins; fix the page.

| Page | Status | Summary |
|---|---|---|
| [`live-sim-demo.md`](live-sim-demo.md) | shipped | The hosted Sim on Cloudflare Pages Functions (brain, voice, ears): the security and spend spec for caps, origin pin, Turnstile, HMAC speech tickets, per-colo counters, the TTS cache and the honest fallback to the scripted Sim. |
| [`expressiveness.md`](expressiveness.md) | shipped | The markup floor and the behavior planner behind `MOXIE_EXPRESSIVE`. The learned planner (P2) and the model-path C6 gap are open. |
| [`telehealth.md`](telehealth.md) | shipped | Puppet mode: the command path that lets a parent speak and act as Moxie, and the "Be Moxie" console panel. |
| [`production-hardening.md`](production-hardening.md) | shipped (P0, P1) | The cross-process store (advisory `flock` per record), MQTT reconnection, and a week-long soak that runs without hardware. |
| [`security-broker-auth.md`](security-broker-auth.md) | partial | Broker ACL shipped; device credentials and spoof-proofing need a physical robot. |
| [`content-packs.md`](content-packs.md) | shipped | Versioned, digest-checked content pack files: export from a field allowlist, import with review that never clobbers local edits. |
| [`content-authoring.md`](content-authoring.md) | partial | Composing a conversation in the console without editing JSON; P0 shipped, the paid "try it" loop and P2 are open. |
| [`sandboxed-extensions.md`](sandboxed-extensions.md) | partial | A pack can carry a small JSON program (53 operators, no `exec`, every op total), metered and permission-checked both ways. P0 plus `act` and `subscribe` shipped; the rest of P1 is open. |
| [`voice-picker.md`](voice-picker.md) | shipped | Speech and Listening dropdowns in the console: defaults, env-var pinning, hot swap. |
| [`brain-picker.md`](brain-picker.md) | partial | A closed list of brains, chosen per child through the config layers; `MOXIE_APP` pins. P0 shipped, P1 not started. |
| [`qr-launch-cards.md`](qr-launch-cards.md) | partial | Printable cards that launch a module when the robot sees them. Decoder, route and sheet shipped; wire spelling, card-making UI and a hardware check are open. |
| [`insights.md`](insights.md) | partial | A parent view of what the child did, built on-device from the stored packet history. History, privacy gates and erasure shipped; the event vocabulary and sessions are not. |
| [`mobile-first-visit.md`](mobile-first-visit.md) | shipped | The Sim's composer sits in the bottom dock, reachable on a phone's first screen. |
| [`turnstile-layout-collision.md`](turnstile-layout-collision.md) | shipped | The Turnstile challenge is placed above the chat dock, not centred over it. |
| [`vendor-the-readme-hero.md`](vendor-the-readme-hero.md) | shipped | Docs images are served from this site, never from off-site hosts the CSP refuses. |
| [`gamify-the-public-sim.md`](gamify-the-public-sim.md) | decided, first slice shipped | The public Sim is a 90-second meet-Moxie toy: effortless chat and three one-tap openers, no missions or scores. |
| [`visemes.md`](visemes.md) | proposed | Lip-sync timing marks from the TTS engine. Research done, nothing built. |
| [`ota-push.md`](ota-push.md) | proposed | Pushing a firmware update to the robot: eight refusal gates, then serving, then an owner-armed push. Owner-gated; nothing built. |
| [`live-brain-open-issues.md`](live-brain-open-issues.md) | open | Three questions that need the live gateway: goodbye `<exit>` adherence, the grounding gate, and having no second model provider. |
| [`test-timing-under-load.md`](test-timing-under-load.md) | open | The smoke config wait and the head-sweep check can go red on a busy runner. Both now say starved or broken; two failures are still unexplained. |
| [`community-signals.md`](community-signals.md) | research | Problems reported by owners of real robots (C1 to C9), each with its follow-up status. |

## House rules

- **Clean room.** Derive designs from our own [reverse-engineering](../../reverse-engineering/README.md)
  and [architecture](../README.md) docs. OpenMoxie and its forks may be read and cited by path, never
  copied.
- **Say what you could not establish.** An unverified robot behavior or a missing hardware capture goes
  in the page's limits section, not a footnote.
- **Status lives on the page.** Each page states its status in its first lines, naming the code and the
  tests. When a feature ships, update the page and this table in the same PR.
- **Section numbers are load-bearing.** Code comments and tests cite several of these pages by section
  (`live-sim-demo.md §4.1`). Renumber only together with those citations.

---
📖 [Docs index](../../README.md) · [Architecture index](../README.md) · [OpenMoxie feature audit](../openmoxie-feature-audit.md) · [Implementation plan](../implementation-plan.md)
