# Gamify the public sim — decision and evidence

**Status:** decided 2026-09-09; the first slice shipped — the three openers in `#chat-openers`
([`sim/web/sim.html`](../../../sim/web/sim.html), wired by `wireOpeners` in
[`sim/web/hud.js`](../../../sim/web/hud.js)), tested by `sim/test_mobile_layout.mjs` block 8 and
`sim/test_typed_turn.mjs` block 7. No game mechanics were built, by decision.

## The decision

The owner's steer for [moxie.mattvalancy.com/sim](https://moxie.mattvalancy.com/sim) included
"gamify this for regular people" alongside "more like a chatgpt/claude interface". Those pull apart,
so this page gathered evidence and put two questions to the owner. The answers:

1. **Who is it for?** A **90-second meet-Moxie toy** for someone who may never own a robot — not a
   rescue on-ramp for owners of a dead one.
2. **What does "gamified" mean?** **Effortless chat with delight.** Mission, score and task structure
   is explicitly off.

So the first-turn prompts invite jokes, feelings and surprise without putting the visitor into a task
or score loop.

## What shipped

Three one-tap openers in the chat dock, verbatim:

- **Tell me a silly joke**
- **What makes you happy?**
- **Surprise me!**

A tap sends the visitor's words down exactly the path a typed line takes (`wireOpeners` →
`moxieTypedTurn.send` → `moxieBridge.sendUserTurn`), so an opener spends, queues, is rate-limited, is
refused and is answered like typed text. The button label **is** the message (`textContent`, no
`data-text` copy), so what a visitor reads and what Moxie receives cannot drift. The openers step aside
once the log has a turn in it. They are distinct from the rail's `#speech-chips`, which play pre-cached
shipped audio and never send a turn.

The precondition — a text box reachable on a phone without opening the drawer — shipped first; see
[`mobile-first-visit.md`](mobile-first-visit.md).

## Measurement rule

Use only the Cloudflare layer already serving the page: its injected Web Analytics beacon for
aggregate page views and performance, and Pages Functions request analytics for aggregate `/api/chat`
attempts. Add **no** second tracker, cookie, fingerprint, stable visitor id, session replay, or capture
of message or audio content. That is enough to compare arrivals with attempted turns without learning
who a child is or what they said. Cloudflare documents Web Analytics as cookie-free and not collecting
personal data (<https://developers.cloudflare.com/web-analytics/about/>). The repo source carries no
analytics code; `sim/check_deployed.mjs` verifies the beacon Cloudflare injects on the custom domain.

## Evidence that drove it (scan of 2026-09-05)

Condensed; each finding was cited to a public URL and date in the original scan.

- **Nobody measures first-visit intent.** No public source reports what a visitor to a Moxie web demo
  wants; there is no other Moxie web demo.
- **Audience shape (proxy: forum view counts on robotsaroundthehouse.com's Moxie board).** The largest
  audiences are owners whose robot died (~8K views) and owners setting up the revival (~7K). Third is
  curious strangers asking what the robot is (~3K views, zero replies), driven by **M3GAN 2.0**
  (released 2025-06-27), whose plot uses a real Moxie. Developers are the smallest cohort by far
  (upstream OpenMoxie: ~91 stars, 10 watchers). Owners are a fixed, shrinking pool; strangers are the
  growing one.
- **What the product was.** Press coverage (Axios 2024-05-31; Reviewed 2024-06-18; Stardock) describes
  weekly themes and daily missions with the child cast as Moxie's helper, plus jokes, Simon Says,
  scavenger hunts and guided breathing. Those games are in our recovered `ONBOARD_MODULES`
  ([`mqtt/moxie_sdk/schedule/catalog.py`](../../../mqtt/moxie_sdk/schedule/catalog.py)): `JOKE`,
  `MENTORSAYS`, `SCAVENGERHUNT`, `PASSWORDGAME`, `BREATHINGSHAPES`, `READ`, and others. All of this
  evidence is 2024 or earlier.
- **Against structure.** The forum's most experienced owner spent six weeks trying to set a revived
  Moxie to **chat only** (thread "OpenMoxie: Setting Moxie to chat only", 2025-11-30 to 2026-01-09) and
  got no answer — n = 1, and an adult rather than a child, but it matches the owner's own steer.
- **The revival is seen as too technical.** arXiv:2510.26080 §2.2 calls OpenMoxie a "high-barrier
  solution" needing "significant programming skills". A non-technical owner who succeeded did it via a
  YouTube video, not documentation.
- **Impostor risk.** Upstream now warns about unaffiliated lookalike projects; from outside, this site is
  shaped like one. See [`community-signals.md`](community-signals.md) C6.
- **A working precedent for delight.** The actively maintained fork `Noonster77/openmoxie` ("OpenMoxie
  Family Edition", announced 2026-08-21) ships knock-knock jokes that pause for "Who's there?" and
  interludes that fill long inference with facts or jokes. Behaviour only; nothing copied.

**Could not read:** r/MoxieRobot (Reddit refuses this environment's fetcher and search — only the owner,
with a browser, can close that gap), and two articles (pirg.org, learnwitharobot.com) that returned
HTTP 403. Quotes attributed to them in search summaries are unverified and must not be used on the site.

## Options not taken

Kept briefly so the question is not re-asked from scratch. None is planned.

| Option | Why not now |
|---|---|
| **Missions** from the recovered vocabulary, with a "mission complete" beat | Mission structure is explicitly off (decision 2); evidence is all historical |
| **Knock-knock turn-taking** with a suggested-reply chip, all beats pre-cached | Smallest and cheapest; a candidate for "delight" if the openers prove too thin |
| **Thinking out loud** — a cached aside while the reply loads | Re-enters the ambient-vs-answer race `sim/test_ambient_guard.mjs` guards; a filler line can be mistaken for the answer |
| **Bring your Moxie home** — a progress strip toward running it locally | Serves the on-ramp audience the decision did not pick, and reads as a funnel |

## Open questions

- Whether the creepy-cute ambient persona (`sim/web/ambient.json`'s own `_comment`) suits a stranger
  arriving from a horror film. No evidence either way; it is a creative call, and cheap to change
  because the lines are data.
- Whether the aggregate numbers above show visitors actually taking a first turn — the measurement rule
  exists, but nobody has read it back against a target.

---
📖 [Backlog index](README.md) · [OpenMoxie feature audit](../openmoxie-feature-audit.md) · [Community signals](community-signals.md) · [Mobile first visit](mobile-first-visit.md) · [Live Sim demo](live-sim-demo.md) · [Implementation plan](../../../ROADMAP.md)
