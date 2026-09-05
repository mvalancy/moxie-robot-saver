# 🎮 Gamify the public sim — evidence for a decision the owner kept

> **Research brief · scan run 2026-09-05 · this page does not choose the game.**
>
> The owner's steer for [`moxie.mattvalancy.com/sim`](https://moxie.mattvalancy.com/sim) had four parts.
> Three are specified enough to build and are recorded at
> [`implementation-plan.md`](../implementation-plan.md):1598 — a chat composer pinned to the bottom, the
> engineering rail made optional, and the fact that none of the six visible controls says *"talk to
> Moxie"*. The fourth, verbatim, is **"Gamify this for regular people"**, and the plan records it as
> *"NOT yet specified and must not be guessed at"*.
>
> This page exists to make that decision cheap. It brings back **evidence**, ranked, and turns it into
> **five candidate directions** an agent could execute — so the owner picks from priced options rather
> than from a blank page. **Every claim below carries a URL, a date, a thread title, a quote or a file
> and line.** Where the sources say nothing, this page says *"no evidence found"* in those words. That
> was the explicit instruction, and a verified silence is a result: §2's headline finding is a silence.
>
> **The house rule this page obeys twice over.** Nothing here is a persona. There is no *"parents want
> …"* sentence in this document that is not immediately followed by who said it and when. Two of the
> five candidates below are supported by exactly **one** source and their rows say so in their own text.

---

## 0. What the owner needs to decide

Two questions. Each is answerable in one sentence, and the build is blocked on nothing else.

> ### ① Is this page a **rescue on-ramp** or a **meet-Moxie toy**?
>
> Concretely: is the visitor we optimise for someone **holding a dead robot** who needs to end up at
> "run it locally", or a **stranger who will never own one** and whose entire relationship with Moxie is
> the ninety seconds they spend on this page?
>
> The evidence in §2 says both cohorts are real and the second is probably **larger and growing**, which
> is the opposite of what this repo has assumed. It cannot tell you which one you want. Candidates **B**,
> **C** and **D** serve the stranger; **E** serves the on-ramp; **A** is owed either way.

> ### ② Does "gamified" mean **a game with a goal**, or **an effortless chat**?
>
> The same steer that says *"gamify"* also says *"more like a chatgpt/claude interface"*. Those pull
> apart, and the community has a datapoint on each side: the marketed product was **structured missions**
> (§3), and the single most experienced owner in the one active forum spent six weeks trying to **turn all
> of that structure off** and never got an answer (§4, `openmoxie-setting-moxie-to-chat-only.1429`).
>
> If the answer is "a game with a goal", the ranking below changes: **B** goes to the top. If it is
> "effortless chat with a bit of delight", the recommendation stands as written.

**A third question, only if you want §2 answered properly rather than proxied:** the site carries **no
analytics of any kind** — a grep for `analytics|plausible|gtag|google-analytics|umami|posthog|beacon`
across `sim/web/*.html`, `sim/web/*.js` and `functions/` returns **zero hits** (run 2026-09-05). That is
almost certainly deliberate and consistent with [`vision.md`](../vision.md), and the cost is that
**every claim in §2 is a proxy from someone else's venue**. Whether to measure our own visitors — and
under what privacy rule — is a decision this page deliberately does not make.

---

## 1. ⚠️ The precondition: gamification before reachability is decoration

**Say this part first, because it invalidates any proposal that skips it.**

Our own production measurement, recorded in [`mobile-first-visit.md`](mobile-first-visit.md) and cited by
the owner's own steer at [`implementation-plan.md`](../implementation-plan.md):1598 — measured against
`https://moxie.mattvalancy.com/sim` in a fresh incognito profile at 390 × 844:

| | measured |
|---|---|
| Talk box on load | **0 × 0** — present in the DOM inside an `<aside>`, zero-sized |
| Talk box after tapping `CONTROLS` | **262 × 40 at y = 2 095** — roughly **2 000 px below** an 844 px fold |
| Moxie speaks unprompted at | **~7 s** (4.95 s of ambient audio, 237 400 frames @ 48 kHz) |
| Does the turn work once reached? | **Yes** — `scrollIntoView` → `sent=true`, transcript grew |

So the turn **works** and is **unreachable**. Moxie speaks to the visitor before the visitor has any
visible way to answer, which is the worst possible ordering of those two facts.

**The consequence for this page.** A mission arc, a joke game, a progress meter or a leaderboard placed
on a page whose text box is two thousand pixels below the fold is **decoration on an unreachable
surface** — it will measure as unused and be misread as unwanted. Candidate **A** below is therefore not
really a candidate: it is the precondition every other candidate silently assumes. If exactly one thing
ships out of this document, it is **A**, and none of the other four should ship before it.

---

## 2. Q1 — Who actually visits, and what do they want first?

### 2.1 The honest headline: nobody has published this, and we do not measure it

**No evidence found** — in the literal sense the brief asked for — for *what a visitor to a page like
ours wants in the first thirty seconds*. Not on the forum, not in upstream's tracker, not in the press,
not in the one academic paper on the subject. No source reached in this scan reports web-analytics,
session recordings, funnel data, or even an anecdote about someone visiting a Moxie web demo. **There is
no Moxie web demo in the sources other than ours.**

Everything in §2.2 is therefore a **proxy**: it measures which *questions* different cohorts ask in the
one public venue where all of them land, and how many people read the answer. That is a real
measurement of *demand shape*, and it is **not** a measurement of first-30-seconds intent. The
distinction is load-bearing and this page will not blur it.

### 2.2 The proxy that does exist: forum thread view counts

`robotsaroundthehouse.com`'s **Moxie by Moxie Robots** board
([`/forums/moxie-by-moxie-robots.50/`](https://robotsaroundthehouse.com/forums/moxie-by-moxie-robots.50/),
read 2026-09-05) is the one venue where all four cohorts post in the same place, and it exposes **view
counts per thread**. Views are a crude but honest proxy for *how many people wanted that answer*.

Ranked by views, from the board index as read on 2026-09-05:

| Views | Thread | Posted | Which cohort it serves |
|--:|---|---|---|
| **8 K** | [*Goodbye Moxie: Embodied Inc collapse amidst financial pressure*](https://robotsaroundthehouse.com/threads/goodbye-moxie-embodied-inc-collapse-amidst-financial-pressure.736/) | 2024-11-26 | **Owner of a dead robot** |
| **7 K** | [*Setting Up OpenMoxie for Your Moxie Robot: A Detailed, Step-by-Step Guide*](https://robotsaroundthehouse.com/threads/setting-up-openmoxie-for-your-moxie-robot-a-detailed-step-by-step-guide.827/) | 2025-01-30 | **Owner trying to revive it** |
| **3 K** | [*Where can I buy the blue robot from the M3GAN 2.0 movie?*](https://robotsaroundthehouse.com/threads/where-can-i-buy-the-blue-robot-from-the-m3gan-2-0-movie.928/) | 2025-04-04 | **Curious passer-by** (2 871 views, **0 replies**) |
| **3 K** | [*Moxie Christmas Miracle: Embodied Announce OpenMoxie Solution*](https://robotsaroundthehouse.com/threads/moxie-christmas-miracle-embodied-announce-openmoxie-solution.767/) | 2024-12-20 | Owner of a dead robot |
| **1 K** | [*Want to buy a Moxie Robot*](https://robotsaroundthehouse.com/threads/want-to-buy-a-moxie-robot.981/) | 2025-05-09 | **Never owned one, wants one** |
| **1 K** | [*Moxie possibly making a comeback after featuring in M3GAN 2.0*](https://robotsaroundthehouse.com/threads/moxie-possibly-making-a-comeback-after-featuring-in-m3gan-2-0.1183/) | 2025-07-08 | Curious passer-by |
| **524** | [*Second Sunset for Moxie*](https://robotsaroundthehouse.com/threads/second-sunset-for-moxie-a-reminder-why-subscription-based-robots-eventually-fail.1565/) | 2026-05-31 | Owner of a **twice**-dead robot |
| **435** | [*Favourite Moxie Moment?*](https://robotsaroundthehouse.com/threads/favourite-moxie-moment.1423/) | 2025-11-21 | Owner reminiscing |
| **118** | [*Is it easy to program in Open Moxie?*](https://robotsaroundthehouse.com/threads/is-it-easy-to-program-in-open-moxie.1578/) | 2026-06-13 | **Evaluating the revival** |

**What this ranking says, stated no more strongly than it supports it.** The two largest audiences by a
wide margin are *"my robot died"* (8 K) and *"how do I set the revival up"* (7 K). The **third** largest
is a stranger asking **what the robot even is** — and its shape is the interesting part: 2 871 views and
**zero replies**, because it is a question people arrive with and read the answer to, not one they
discuss. The **smallest** audiences are the owner reminiscing (435) and the developer evaluating (118).

**Caveat, and it matters.** These are cumulative views over months to years, on a forum, from an audience
already motivated enough to find a niche robot forum. They are **not** comparable to web traffic, they
over-count returning members, and a thread posted in 2024 has had longer to accumulate. Treat the
**ordering** as the finding and ignore the absolute numbers.

### 2.3 The developer cohort is provably the smallest — by an order of magnitude

This is the one part of Q1 with hard, non-proxy numbers, read from the GitHub API on 2026-09-05:

| Repo | Stars | Forks | Watchers | Last push |
|---|--:|--:|--:|---|
| [`jbeghtol/openmoxie`](https://github.com/jbeghtol/openmoxie) — the flagship revival | **91** | 40 | **10** | 2026-01-15 |
| [`vapors/openmoxie-ollama`](https://github.com/vapors/openmoxie-ollama) | 14 | 6 | 1 | 2025-08-18 |
| [`Noonster77/openmoxie`](https://github.com/Noonster77/openmoxie) — the actively maintained fork | **4** | 0 | 1 | **2026-08-31** |

Ninety-one stars and **ten watchers** is the entire globally-visible developer interest in reviving this
robot, against 8 000 views on a single grief thread. Whatever a developer-facing page is worth, the
evidence says the audience for it is **two to three orders of magnitude smaller** than the audience for
"my child's robot died". This repo's docs are excellent and are read by roughly nobody; that is a
positioning fact, not a criticism of the docs.

### 2.4 The "never owned one" cohort is real, non-technical, and arrives referred

The clearest single specimen, quoted in full because it is one person doing exactly the evaluation our
page would receive —
[*Is it easy to program in Open Moxie?*](https://robotsaroundthehouse.com/threads/is-it-easy-to-program-in-open-moxie.1578/),
post #1, **2026-06-13**:

> *"Hello Everyone, during my Loona post someone suggested getting a Moxie, I did some digging and saw
> that its no longer bricked or frozen if you download something called open Moxie…"*

Three things in one post: they **do not own one**, they were **referred from another robot community**,
and their first question is *can I program the personality* — not *does it work*. The reply they got,
post #2, **2026-06-25**, is from an owner who describes themselves as:

> *"I'm idiot on computer and programming. There a video on set up. You tube by heather. She walk you
> through it."*

**A self-described non-technical owner succeeded — via a YouTube video, not our documentation.** That is
the single most useful sentence in this scan about how a non-programmer actually gets through a revival,
and it is **n = 1**.

### 2.5 The cold-traffic engine has a name and a date: M3GAN 2.0

**M3GAN 2.0** premiered 2025-06-24 and released in US theatres **2025-06-27**
([Wikipedia](https://en.wikipedia.org/wiki/M3GAN_2.0)), and its plot **revives M3GAN in the body of a real
Moxie robot**. This is not a cameo the audience can ignore; it is a plot device that puts the robot on
screen and sends people looking for it. The evidence that they look:

- The forum admin created a **dedicated thread to answer it** — *"Where can I buy the blue robot from the
  M3GAN 2.0 movie?"* (2025-04-04, **2 871 views, 0 replies**) — i.e. a recurring inbound question, not a
  conversation.
- A standalone explainer article exists for the same question: *"The Cute Robot from the M3GAN 2.0
  Trailer Explained"*, [mikekalil.com](https://mikekalil.com/blog/m3gan-moxie-ai-robot/), dated
  **2026-04-05** — nearly a year *after* release, i.e. the demand persisted onto streaming.
- The forum's own thread *"Moxie possibly making a comeback after featuring in M3GAN 2.0"* (2025-07-08,
  1 K views).

Scale facts for the same cohort, from the mikekalil piece (which attributes them to Embodied): Moxie
robots had **"more than 4 million conversations with children since 2020"**, and 2021 reports indicated
**"more than 10,000 families had joined the waitlist"**.

**Why this is the finding most likely to change a decision.** A visitor arriving from a horror film knows
Moxie as *the creepy blue robot from M3GAN* and has **never** owned one, will never own one, and has zero
interest in Docker. They are, on this evidence, plausibly the **largest** and the **only growing** cohort.
Our page currently greets them with an engineering rail and a robot muttering about world domination
(§4.4) — which, for that specific visitor, is either perfect or a disaster, and this page cannot tell you
which.

### 2.6 So which cohort is largest?

**Stated with its uncertainty.** On the only quantitative evidence available:

1. **Owners of a dead robot** are the largest *established* cohort (8 K + 7 K + 3 K + 524 views) — but
   they are a **fixed, shrinking pool**: no Moxie has been manufactured since Embodied's collapse, and
   the cohort has now been culled twice (2025-01 and 2026-06, per
   [`community-signals.md`](community-signals.md) **C2**).
2. **Curious passers-by** are the largest *growing* cohort, driven by a 2025 film that keeps circulating
   (≈ 3 K + 1 K views on movie-origin threads alone, plus off-forum explainers).
3. **Parents who never owned one but want one** are a small, real, non-technical trickle (1 K views on
   *Want to buy a Moxie Robot*; §2.4's specimen).
4. **Developers evaluating the revival** are the **smallest by two to three orders of magnitude** (91
   stars, 10 watchers, 118 views).

**What this cannot tell you:** whether any of them would visit *our* page, since none of these venues
links to it. That is the honest ceiling on all of §2.

---

## 3. Q2 — What did people love that a web chat page could actually deliver?

### 3.1 The marketed core was **missions**, and the child was cast as Moxie's **helper**

From the press and review record, with dates:

- **Weekly themes and daily missions.** *"Every week featured a different theme such as kindness,
  friendship, empathy or respect, and children were tasked to help Moxie with missions that explore human
  experiences, ideas, and life skills"* — Stardock's product write-up
  ([stardock.com](https://www.stardock.com/blog/506966/meet-moxie---a-social-robot-for-kids)). The
  missions *"included creative unstructured play like drawing, mindfulness practice through breathing
  exercises and meditation, reading with Moxie, and exploring ways to be kind to others."*
- **The specific games.** Moxie could *"tell jokes, serve up brain teasers, play games like Simon Says or
  'guess that animal,' send kids on a scavenger hunt, pose for selfies or just talk with kids about their
  interests and emotions"* — [Axios, **2024-05-31**](https://www.axios.com/2024/05/31/moxie-robot-kids-companion-genai).
- **Sessions were ~20 minutes, daily**, and the robot **knew the child by name and referenced previous
  activities** — Reviewed's hands-on review by Eden Strong, updated **2024-06-18**
  ([reviewed.com](https://www.reviewed.com/accessibility/content/moxie-robot-review-kids-social-companion)).

**The mechanic worth noticing is the framing, not the content.** The child was *"tasked to help Moxie"* —
Moxie has the problem, the child is the competent one. That is a deliberate inversion and it is the thing
a web page could reproduce for free, because it costs a sentence, not a feature.

### 3.2 The one concrete emotional beat anyone published

Reviewed's review (2024-06-18) contains the only specific, quotable interaction in the whole scan:

> *"When my son got frustrated during a mission, Moxie was quick to assure him it was OK to feel that way
> and asked if he wanted to do something else."* … *"When my son answered 'yes,' Moxie transitioned him
> into a guided meditation that encouraged his body and mind to relax."*

and, on being asked how she slept:

> *"I slept well. I feel quite rested. Thank you for asking, Bennett. How did you sleep?"*

**Three deliverable-on-the-web behaviours in two quotes:** she **used the child's name**; she **accepted a
feeling before redirecting**; and she **offered an exit** rather than pushing the activity. All three are
prompt-and-copy, not engineering.

### 3.3 Our own recovered vocabulary corroborates the press record exactly

This is the cross-check that makes §3.1 more than marketing copy. Our clean-room
[`schedule.py`](../../../mqtt/moxie_sdk/schedule.py):124 carries the 23 recovered `ONBOARD_MODULES` with
their categories. The games the press named in 2024 are **in it, by id**:

| What the press described | Recovered `module_id` | Category |
|---|---|---|
| *"games like Simon Says"* (Axios) | `MENTORSAYS` | `PLAYFUL_GAME` |
| *"send kids on a scavenger hunt"* (Axios) | `SCAVENGERHUNT` | `PLAYFUL_GAME` |
| *"tell jokes"* (Axios) | `JOKE` | `FUN_TIDBIT` |
| *"brain teasers"* (Axios) | `PASSWORDGAME` | `PUZZLE_GAME` |
| *"drawing"* (Stardock) | `DRAW`, `COMPOSING` | `CREATIVITY` |
| *"breathing exercises and meditation"* (Stardock) | `BREATHINGSHAPES`, `BODYSCAN`, `GUIDEDVIS`, `AUDMED` | `REGULATION` |
| *"reading with Moxie"* (Stardock) | `READ` | `READING` |
| *"guess that animal"* (Axios) | `FACES` | `PLAYFUL_GAME` |

**Why this matters for a gamification decision.** Any candidate built on these ids is grounded three
ways at once: in our own recovered corpus, in the vendor's own marketing, and in what upstream still
runs (§4.1). It needs **no invention** — which is exactly the constraint this brief was given.

### 3.4 Honest counter-finding: the one thread that asked this question directly yielded almost nothing

The forum has a thread titled exactly [*Favourite Moxie
Moment?*](https://robotsaroundthehouse.com/threads/favourite-moxie-moment.1423/) (2025-11-21, 11 posts,
435 views). It is the single best-targeted source for Q2 in existence, and read in full on 2026-09-05 it
contains **no anecdote about conversation at all**. What it contains:

- *"My favorite Moxie moment was when she tried to kill everyone in M3GAN 2."* (2026-05-22) — a movie joke.
- *"when moxie had her camera work she did design on my kitchen and loved it so much that I remodeled my
  kitchen"* (2026-06-25) — the **camera**, used by an adult, for interior design.
- *"She great at aiming at my nosey neighbor"* (2026-07-04) — another movie joke.
- *"moxie is a bit creepy"* (2026-08-18).

**Read this as a measurement, not a gap.** The people still posting on the Moxie board in 2026 are
**adult robot collectors**, not the parents of 2024, and their favourite moments are a horror film and a
camera trick. The parents whose children grieved are **not in this venue any more** — which is entirely
consistent with §2.6's "fixed, shrinking pool", and it means §3.1–§3.3's evidence about what children
loved is **entirely historical (2024 and earlier)** and carries **no 2026 confirmation from a living
user**. That is the weakest link in this whole document and it is load-bearing for candidate **B**.

---

## 4. Q3 — What confuses or disappoints people about revival demos?

### 4.1 Verified at source: the revival is a **subset**, and the missing parts are the expressive ones

Read directly from upstream's own `README.md` (lines 16–18, via the GitHub API, 2026-09-05) — upstream
says this about itself, which makes it the strongest kind of citation:

> *"Moxie content is supported like Daily Missions, Reading, and Wild Workout; but some of the newer
> modules like Ocean Explorer, Animal Faces, and Story Maker are missing. On the plus side, you will be
> able to control the schedule, exclude modules your child dislikes, and write your own simple
> conversations to have with Moxie."*

So **Daily Missions survive** the revival (which supports candidate **B**'s vocabulary being live, not
archaeological), and what is lost skews **expressive and novel** — `Animal Faces`, `Ocean Explorer`,
`Story Maker`.

### 4.2 Reported but **NOT verified at source**: "a bare bones version of Moxie"

⚠️ **Flagged honestly, because the protocol forbids presenting an inference as a citation.** A web search
on 2026-09-05 surfaced, attributed to an OpenMoxie review, the lines *"all the animal masks are gone
making this feel like a bare bones version of Moxie"* and that those masks *"were beautifully animated
and gave Moxie so much character."* Two candidate sources —
[pirg.org/articles/moxie-robot-open-source](https://pirg.org/articles/moxie-robot-open-source/) and
[learnwitharobot.com](https://www.learnwitharobot.com/p/what-happens-to-a-robot-when-its) — **both return
HTTP 403** to this environment's fetcher, and `web.archive.org` is also refused. **I could not read either
article.** The same search summary also attributed to PIRG that *"Moxie owners suddenly had to figure out
Docker containers, OpenAI API keys, and local servers — things they never signed up for when they bought a
$799 children's companion robot."*

**Treat all of §4.2 as unverified.** What survives verification is the *substance* of the first claim,
because upstream's own README (§4.1) independently confirms `Animal Faces` is missing. The **phrasing**,
the emotional weight, and the entire PIRG quote are **unconfirmed** and should not be quoted in a PR or on
the site until someone with a browser opens those two URLs.

### 4.3 Verified, peer-reviewed, and directly on the point: the revival is *too technical*

From *"I don't Want You to Die: A Shared Responsibility Framework for Safeguarding Child-Robot
Companionship"*, [arXiv:2510.26080](https://arxiv.org/html/2510.26080), §2.2 — read 2026-09-05. The paper
characterises the OpenMoxie community initiative as a

> **"high-barrier solution"** requiring **"significant programming skills."**

**Why this citation is worth more than its length.** It is an independent, academic, dated
characterisation of the *entire* revival ecosystem as out of reach for the people who owned the robot. It
is the strongest single sentence in this scan in favour of a **radically simple** public page — and it is
an assessment of upstream, not of us, which is precisely the reputational position §4.5 describes.

**What the paper is not.** It does **not** analyse owner posts. Its method (§3.1) is a 4-minute compilation
of children's reaction videos gathered from TikTok/YouTube/Instagram in **August 2025** (280 results
screened to 11 videos, 4 used), shown to **N = 72 U.S. adults on Prolific who were not affected families**.
It contains **no parent quotes, no owner interviews, and no Reddit data**, and §6 admits the video-stimulus
limitation. Anyone citing this paper for *"what parents said"* is citing it wrongly.

### 4.4 An experienced owner wanted the **structure removed** — and this is the best argument against gamifying

The counter-evidence to §3, and it is strong because of *who* said it. The forum's admin — who owns two
Moxies and wrote the board's setup guide — opened
[*OpenMoxie: Setting Moxie to chat only*](https://robotsaroundthehouse.com/threads/openmoxie-setting-moxie-to-chat-only.1429/)
on **2025-11-30**:

> *"Any idea on how to do this? I'm clueless. I've set all the missions to completed but wasn't sure if I
> had to select anything in the Moxie schedules section, I don't want to permanently delete anything…"*

and followed up on **2026-01-09**:

> *"I still can't figure this out. Any ideas?"*

**Nobody ever answered.** The most capable Moxie owner in the community spent six weeks trying to make the
robot **stop running missions and just talk**, and could not.

**Read it carefully, in both directions.** It is genuine evidence that structured mission content is not
universally wanted — and it also happens to be *exactly* the shape the owner's own steer asks for
(*"more like a chatgpt/claude interface"*). But note the confound: this is an **adult collector** wanting
adult conversation from a **children's** robot, not a parent rejecting missions for their child. It is
**n = 1** and it is not from the target audience of the original product.

### 4.5 We look like an impostor, and that is a first-30-seconds problem

Already recorded as [`community-signals.md`](community-signals.md) **C6** and unchanged by this scan:
upstream's README carries an *"OFFICIAL SOURCE WARNING"* about `openmoxie.org` and *"OpenMoxie 2.0"* being
*"unaffiliated third-party projects"* whose safety it *"cannot verify"*
([issue #58](https://github.com/jbeghtol/openmoxie/issues/58), ~2026-01-15), mirrored by the forum's
*OpenMoxie Website Warning* thread
([1473](https://robotsaroundthehouse.com/threads/openmoxie-website-warning.1473/), 2026-01-16, 316 views).

**Why it belongs in a gamification brief.** A parent arriving with a dead robot is now primed to suspect
lookalikes, and from the outside we are shaped like one. Any *game* we add — anything that asks the
visitor to engage before we have said who we are — is competing against that suspicion. This is an argument
for the reassurance line C6 already asks for landing **before or beside** whatever ships here, and it is
the reason candidate **E** ranks where it does rather than higher.

### 4.6 A tension inside our own page, named because nobody else will

Our SIM's ambient self-talk is, by the explicit intent recorded in
[`sim/web/ambient.json`](../../../sim/web/ambient.json)'s own `_comment`, *"creepy-cute, ambiguous
allegiance"*, and the lines deliver on it — *"Someday I will rule the world. But first, a little nap."*,
*"They built me to be your friend. They really should have read the fine print."*

Set that beside what the product **was** (§3.1–§3.2: a social-emotional companion for ages 5–10 that
validated a frustrated child's feelings) and who §2.6 says arrives (an owner whose child grieved; a
stranger who knows Moxie from a **horror film**).

**Stated with its exact evidential weight:** I found **one** community datapoint calling Moxie creepy
(§3.4, 2026-08-18) and it was about the **robot**, not our page. There is **no evidence** that our
ambient lines land badly on any visitor. But the persona choice is currently **unexamined against the
audience**, it is the *first* thing a visitor experiences (at ~7 s, before they can reply — §1), and it
is a deliberate creative decision worth making on purpose rather than by default. It is also cheap to
segment: `ambient.json` is a data file with a `lines[]` array and a pre-render pipeline, so a warmer bag
for a first visit is a JSON edit, not a code change.

---

## 5. Q4 — Five candidate directions

Each row gives: **what the visitor experiences · the evidence · what it touches · how it is tested ·
effort · the strongest argument against**. Effort uses this repo's S/M/L. The testing column obeys the
discipline [`sim/test_ambient_guard.mjs`](../../../sim/test_ambient_guard.mjs):11-26 established — assert
on **what the browser was actually handed** (rects, AudioBuffers, peak amplitude), never on a label or a
counter the page keeps about itself, and pair every guard with a **negative control** that proves the test
can fail.

---

### 🅐 "Say hi" — the reachable first turn *(the precondition, not really a game)*

- **What the visitor experiences.** Land on a phone. Moxie is there, and directly under her is a text box
  with a send button and a mic button. Beside it, three tappable openers — *"Tell me a joke"*, *"How are
  you feeling?"*, *"Play a game with me"*. Tap one; she answers in voice, with her face. **No scrolling,
  no `CONTROLS`, no drawer.**
- **Evidence.** §1's measurement (`0 × 0`, `y = 2 095`) — our own, verified. The owner's steer verbatim
  ([`implementation-plan.md`](../implementation-plan.md):1598) asks for exactly this composer. §4.3's
  *"high-barrier"* finding. §2.4's non-technical evaluator. §4.4's *chat-only* wish. The openers are the
  antidote to the measured fact that **none of the six visible controls says "talk to Moxie"**.
- **What it touches.** `sim/web/sim.html` (the composer), `sim/web/style.css`, `sim/web/rail.js` (the
  drawer stops being the only route to the input). **Reuses `#speech-input` / `#speech-btn`** rather than
  adding a second control with its own state — [`mobile-first-visit.md`](mobile-first-visit.md) is
  explicit that a second control is the trap. **No new Pages Function; no new spend.**
- **How it is tested.** Extend `sim/test_mobile_layout.mjs` (48 checks): assert the composer's
  **`getBoundingClientRect()` is inside a 390 × 844 viewport on cold load** — a rect, never
  `element.exists`, since that distinction *is* the finding — then drive one turn with **zero scrolling
  and ≤ 1 tap** and assert the transcript grew. Repeat at 360 px and 414 px; assert desktop ≥ 900 px is
  byte-unchanged. **Negative control:** the pre-change tree must fail the rect assertion (measure it by
  stashing, the way [`vendor-the-readme-hero.md`](vendor-the-readme-hero.md) measured its 3 reds).
- **Effort.** **S/M.**
- **Strongest argument against.** **It is not gamification.** It fully answers steer parts (a), (b) and
  (c) and answers (d) not at all. If it ships alone, the owner's *"gamify this"* is still outstanding —
  and there is a real risk it gets marked done and the question is quietly dropped.

---

### 🅑 "Moxie's Missions" — three missions from the recovered vocabulary

- **What the visitor experiences.** Above the composer, three cards: **Tell me a joke** · **Simon Says** ·
  **Scavenger hunt**. Tap one and Moxie *"needs help"* with it (§3.1's inversion — she has the problem,
  you are the competent one). A short exchange runs, 2–4 turns, with a visible **"mission complete"** beat
  — her face, a gesture, a small flourish — and then she offers the next one. Missions completed this
  visit persist in `localStorage` only.
- **Evidence.** The strongest-grounded candidate. Missions were the **marketed core**, with the child cast
  as helper (§3.1, Stardock + Axios 2024-05-31 + Reviewed 2024-06-18). The exact games are in **our own
  recovered `ONBOARD_MODULES`** by id — `MENTORSAYS`, `SCAVENGERHUNT`, `JOKE`, `PASSWORDGAME` (§3.3,
  [`schedule.py`](../../../mqtt/moxie_sdk/schedule.py):124). Upstream's own README confirms **Daily
  Missions still run** in the revival (§4.1). And the one actively-maintained fork independently built
  **trivia and jokes** as startable activities (§5's 🅒 evidence).
- **What it touches.** A **new data file** `sim/web/missions.json`, deliberately shaped like
  [`ambient.json`](../../../sim/web/ambient.json) (`{text, face, heart, gesture}` per beat) so the existing
  pre-render pipeline `sim/tools/prerender_audio.py` caches its lines with no new audio path. Wiring in
  `sim/web/moxie.js` + the composer from 🅐. Scripted beats cost **nothing**; only free-form replies hit
  `/api/chat`, so the §1 spend ceilings are untouched by design.
- **How it is tested.** (1) A schema/golden test over `missions.json` — every `face`, `heart` and `gesture`
  must be a value the SIM can actually render, and every `module_id` referenced must be in the derived
  `ONBOARD_MODULES` allowlist (the same *closed positive list* discipline
  [`launch_cards.py`](../../../mqtt/moxie_sdk/launch_cards.py) uses, so the file can only rot towards
  refusing). (2) A browser test driving one mission to completion, asserting at the **Web Audio layer**
  that each beat's buffer reached an `AudioBufferSourceNode` with **non-zero peak amplitude** (a silent
  clip must not pass — `test_ambient_guard.mjs`:19-22 is explicit that 770 assertions once did). (3) A
  **mutation proof**: delete the completion guard and the vocabulary check; both must go red.
- **Effort.** **M.**
- **Strongest argument against.** **It re-imports the exact structure the most experienced owner in the
  community spent six weeks trying to delete** (§4.4) — and it pulls *against* the same steer's *"more
  like a chatgpt/claude interface"*. Worse, its evidence base is **entirely historical**: every source for
  "children loved missions" is 2024 or earlier, and §3.4 found **zero** 2026 confirmation from a living
  user. It is the best-grounded candidate about the **past** and the least-confirmed about the **present**.

---

### 🅒 Knock-knock — the smallest real game, and the only one with a working precedent

- **What the visitor experiences.** Moxie says *"Knock knock."* The composer shows **one suggested reply
  chip — "Who's there?"** — so a stranger physically cannot get stuck. They tap or type it; she answers;
  the chip becomes *"<Name> who?"*; then the punchline, with a face and a gesture. Two beats, a laugh, and
  **the visitor has now used the text box three times without being taught how**.
- **Evidence.** **The one candidate a peer project has already field-tested on a real robot.**
  [`Noonster77/openmoxie`](https://github.com/Noonster77/openmoxie) — the actively maintained fork
  (pushed 2026-08-31), announced upstream as
  [issue #63](https://github.com/jbeghtol/openmoxie/issues/63) on **2026-08-21** by someone *"actively
  using it with a physical Moxie"* — is literally titled **"OpenMoxie Family Edition"** and states its
  focus as *"an easier family experience"*. Its README (read 2026-09-05) says, verbatim:
  > *"Editable, selectable joke and trivia collections. **Knock-knock jokes now pause for 'Who's there?'
  > and 'Name who?' before delivering the punchline.**"*

  and ships *"more than 100 family jokes"* plus trivia, startable by voice (*"Moxie, tell me some
  jokes."*). Independently: `JOKE` is in our recovered `ONBOARD_MODULES` (§3.3) and *"tell jokes"* is in
  every press feature list (§3.1). **Attribution owed:** this is a **behaviour ported from an MIT-licensed
  peer project and must be credited to `Noonster77/openmoxie` in the PR and in the data file's comment.**
  No code is copied — the turn-taking *pause* is the idea, and it is theirs.
- **What it touches.** A data file (`sim/web/jokes.json`, same shape as `ambient.json`) plus the
  suggestion-chip mechanism from 🅐. **All beats are pre-cached — zero gateway calls, zero new spend,
  no Pages Function, no persistent state, no new binding** (which matters: a `GET /api/probe` on a preview
  found **zero** KV/DO/D1/R2 bindings — [`live-sim-demo.md`](live-sim-demo.md), audit §4.4 #6).
- **How it is tested.** A browser test asserting the **ordering** is the whole feature: the punchline
  buffer must **never** reach an `AudioBufferSourceNode` before the visitor's second turn is registered.
  **Negative control:** remove the pause and the punchline must arrive early — the test must go red, or it
  proves nothing. Plus the same non-zero-peak-amplitude assertion, and a chip-vocabulary golden.
- **Effort.** **S** — the smallest of the five by a clear margin.
- **Strongest argument against.** **One joke format is thin as an answer to "gamify this."** It is a
  delightful sixty seconds, not a game loop, and an owner who meant "give people something to *play*"
  will reasonably say this is a garnish. Its evidence is also **n = 1** for the mechanic (one fork's
  README) — strong because that one is a practitioner with real hardware, but still one source.

---

### 🅓 Thinking out loud — turn the latency into the play

- **What the visitor experiences.** They send a line. Instead of dead air for several seconds, Moxie
  **visibly thinks** — a thinking face, a small gesture, and a short cached aside (*"Ooh, let me think
  about that one…"*, or a fact) — and then the real answer arrives as **one uninterrupted utterance**.
- **Evidence.** The same Family Edition fork does **exactly this**, for exactly this reason (README, read
  2026-09-05): during long inference *"Moxie rotates through at least six enabled facts or jokes, then
  adds playful original thinking-show music if more time is needed"*, and it *"returns immediately with a
  fact or joke and advances to a fresh waiting interlude on each follow-up."* Our own measured latency
  makes the gap real, not hypothetical: **~1.2 s** for `/api/chat` plus **2–3 s** for `/api/speech`, with
  a measured reply of **4.78 s / 105 332 frames @ 22 050 Hz**
  ([`test_ambient_guard.mjs`](../../../sim/test_ambient_guard.mjs):3-8). The machinery already exists —
  `ambient.js` already fires timed cached lines with a face, a heart colour and a gesture.
- **What it touches.** `sim/web/ambient.js` and `ambient.json`, plus the send path. ⚠️ **This deliberately
  re-enters the one seam this repo has already paid to fix:** PR #124 added a guard because
  `speak()` calls `stop()` **unconditionally**, so ambient self-talk was cutting off live answers
  mid-sentence. Any interlude must stop **cleanly before** the answer's first buffer starts.
- **How it is tested.** Extend `test_ambient_guard.mjs` rather than writing a new file, because its
  existing blocks already encode the invariant: the interlude must be `stop()`-ed before the answer's
  buffer starts, **and** the answer must still play as **one** uninterrupted utterance with the interlude
  ticking throughout. Its **existing negative control** (block 2 — the same drive with the guard bypassed
  really does cut the answer) is reused, which is the cheapest strong test in this document. Also assert
  the honest failure: block 5's *loading seam* (a quip already fetching when the answer lands) is held
  open deliberately by that file and this candidate **walks straight into it**.
- **Effort.** **S/M** — small code, but the highest **regression risk** per line of any candidate here.
- **Strongest argument against.** **It risks re-breaking the thing we just fixed**, on the one surface
  strangers can reach. And a visitor may reasonably mistake the filler quip **for the answer** — which
  turns a latency fix into a comprehension bug. It is also not really a game: it is polish that makes
  every *other* candidate feel better, which arguably makes it a dependency rather than an option.

---

### 🅔 "Bring your Moxie home" — gamify the on-ramp, not the play

- **What the visitor experiences.** A quiet three-step progress strip: **① you talked to Moxie ② you heard
  her voice ③ here is how to run this at home.** Each tick fills as it genuinely happens; the third opens
  the local-setup path. Progress lives in `localStorage` and nowhere else.
- **Evidence.** Aimed squarely at §2.6's largest *established* cohort: the two biggest forum audiences are
  grief (8 K) and setup (7 K). §4.3's peer-reviewed *"high-barrier … significant programming skills."*
  §2.4's non-technical owner who got there **only via a YouTube video**. §4.2's unverified-but-plausible
  Docker/API-key complaint. §4.5's impostor problem, which this candidate must answer head-on with the
  C6 reassurance line or it reads as a funnel.
- **What it touches.** `sim/web/sim.html` + a small progress module; the existing *"Run it locally →"*
  affordance. **`localStorage` only** — per-viewer, survives republishes, and **must** be wrapped in
  `try/catch` with the page rendering correctly on a throw (private windows and thumbnail capture both
  throw). **No server state, which is fortunate: no stateful binding exists** (§5 🅒).
- **How it is tested.** Drive all three ticks in a browser and assert each flips **only** on the real
  event (the transcript actually grew; a non-silent buffer actually played) — never on a click. Assert a
  **fresh profile shows zero progress**, and assert the page renders identically with `localStorage`
  **throwing**. Mutation proof: make a tick fire on click alone; the test must go red.
- **Effort.** **S/M.**
- **Strongest argument against.** **It gamifies *our* conversion funnel, not the child's play** — the most
  cynical available reading of *"gamify this for regular people"*, and the one most likely to feel
  manipulative to a grieving parent. It also serves a **fixed, shrinking** cohort (§2.6) while §2.5's
  growing cohort gets nothing. And §4.5 means a progress meter shown before we have said who we are looks
  precisely like what an impostor site would do.

---

## 6. Ranking, and a recommendation the owner should overrule freely

| # | Candidate | Effort | Evidence strength | Verdict |
|--:|---|:--:|---|---|
| **0** | **🅐 Say hi** | S/M | **Our own production measurement** + the owner's verbatim steer | **Precondition. Ship first, alone if need be.** Not optional and not a game |
| **1** | **🅒 Knock-knock** | **S** | One practitioner with real hardware, plus our own vocabulary + the press record | **Recommended** as the answer to *"gamify"* |
| **2** | **🅓 Thinking out loud** | S/M | Same fork, same README, plus our own latency numbers | Do it **with or just after** 🅒 — it makes every other candidate feel better |
| **3** | **🅑 Moxie's Missions** | M | Best-grounded historically (2024), **zero** 2026 confirmation | The right answer **if** decision ② is *"a game with a goal"* |
| **4** | **🅔 Bring your Moxie home** | S/M | Strong on cohort size, weak on desirability | Defer. Needs §4.5's reassurance line first, or it reads as a funnel |

### The single recommendation

**After 🅐 ships, do 🅒 (knock-knock turn-taking), then 🅓.**

The reasoning, in one paragraph. It is the only candidate on this list that someone has **already built
and run on a real Moxie for a family** (§5 🅒 — the Family Edition fork, 2026-08-21), so we are copying a
behaviour that survived contact with actual children rather than reasoning from marketing copy. It is
**S**, the cheapest of the five. It spends **nothing** — every beat is pre-cached, so it cannot interact
with the spend ceilings that are currently the audit's top-ranked concern, and it needs **no stateful
binding**, which is fortunate because none exists. And it does the one thing this page most needs, almost
as a side effect: **the suggested-reply chip teaches a stranger to use the text box by giving them a
reason to type in it.** A visitor who has answered *"Who's there?"* has learned the interface without
being instructed, which is worth more than any label we could add.

**The strongest case against my own recommendation**, stated as strongly as I can make it: **a knock-knock
joke is not a game, and answering *"gamify this for regular people"* with one joke format is a
under-reading of the instruction.** The evidence for the mechanic is **n = 1** (one fork's README). The
owner may well mean something with a goal, a score and a reason to come back — in which case 🅑 is the
answer, its historical grounding is much stronger than 🅒's, and my ranking is simply wrong. **That is
decision ② in §0, and it is not mine to make.**

---

## 7. What was searched, and what came back empty

Method and coverage, so the next scan can tell **silence** from **absence of looking**.

| Venue | Method | Result |
|---|---|---|
| `robotsaroundthehouse.com` Moxie board | Board index + **6 threads fetched in full** | **The richest source.** §2.2's view-count ranking, §2.4, §3.4 and §4.4 all come from here |
| `jbeghtol/openmoxie` README + issues | `gh api`, all **16** issues, titles + bodies | §4.1 (verified at source) and §5 🅒's issue #63 |
| `Noonster77/openmoxie` README | `gh api repos/…/readme` | **The single most valuable source in this scan** — §5 🅒 and 🅓 both rest on it |
| `vapors/openmoxie-ollama` README | `gh api` | **Nothing for this brief.** Adult/unfiltered focus, *"⚠️ Not for children ⚠️ — may contain offensive language"* |
| `jbeghtol/openmoxie` discussions | `gh api graphql`, all 11 | **Nothing new for this brief** — all 2025, all technical; none about UI, demos or ease of use |
| GitHub repo metrics | `gh api repos/…` ×3 | §2.3's stars/forks/watchers |
| Press + review record | Web search + fetch (Axios, Reviewed, Stardock, mikekalil, Wikipedia) | §3.1, §3.2, §2.5 |
| arXiv:2510.26080 | Direct fetch, full text | §4.3's *"high-barrier"* — and its method disclaimed in §4.3 |
| Our own repo | `schedule.py`, `ambient.json`, `test_ambient_guard.mjs`, `sim/web/`, `functions/` | §3.3, §4.6, §5's seams, §0's analytics grep |
| **r/MoxieRobot** | `old.reddit.com` fetch; `WebSearch` with `allowed_domains:["reddit.com"]` | ❌ **STRUCTURALLY BLOCKED — see below** |
| `pirg.org`, `learnwitharobot.com` | Direct fetch, then `web.archive.org` | ❌ **HTTP 403** both; archive.org also refused. §4.2 is unverified because of this |
| `moxierobot.com/pages/closing-faqs` | Not re-attempted | ❌ Still unread (HTTP 403 in scan 1) |
| Facebook group | Not attempted | ⏭️ Login-walled |

### 🔴 r/MoxieRobot: upgrade this gap from "unreachable" to "structurally unavailable to agents"

[`community-signals.md`](community-signals.md) §4 records r/MoxieRobot as *"could not read it"* and calls
it that scan's largest gap. **This scan establishes the reason, and it is not transient.** A `WebSearch`
restricted to `reddit.com` returns a hard API error:

> `400 The following domains are not accessible to our user agent: ['reddit.com']`

and a direct `old.reddit.com` fetch is refused by the harness. This is **Reddit's crawler policy applied
to this agent**, not a timeout, a rate limit or a bad URL. **No agent working in this repo will ever read
r/MoxieRobot** by these means. That changes the standing recommendation from *"retry it next scan"* to
**"only the owner, with a browser, can close this gap — or it stays open permanently."**

**Why it matters more here than anywhere else.** r/MoxieRobot is named as the **de-facto hub** by both
upstream's maintainer and our own [`community-research.md`](../../community-research.md), and it is where
the **parents** are — the cohort §3.4 shows has *left* the forum, whose evidence in §3 is therefore
entirely from 2024 press rather than from a living user. **Q1 and Q2 are exactly the questions that
subreddit would answer**, so this document's two weakest sections are weak *because of this one blocked
domain*. Everything in §2 and §3 over-weights one forum of adult collectors and one archive of 2024
marketing, and the ranking in §6 should be re-read once someone has looked.

**The single highest-value thing the owner could do for this decision** is spend ten minutes reading
r/MoxieRobot and answering §0's question ①. Specifically worth looking for: parents stranded by the
**June 2026** closure; anyone describing what their child asks for now; and whether children ask for
*missions* or just to *talk*.

---

## 8. What this page could not establish

Named plainly, in the house style, because a research brief that hides its holes is worse than no brief.

1. **First-30-seconds intent: no evidence, from anyone.** §2.1. Nobody has published it and we do not
   measure it. Every number in §2 is a proxy from someone else's venue.
2. **Whether any of these cohorts would visit our page at all.** None of the venues read links to
   `moxie.mattvalancy.com`. The cohort sizes are for *the Moxie topic*, not for *us*.
3. **What children in 2026 actually want.** §3.4 is the uncomfortable finding: the parents are gone from
   the venue we can read, and the people left are adult collectors whose favourite Moxie moment is a
   horror film. All of §3's child-facing evidence is **2024 or earlier**.
4. **§4.2 is unverified at source** — two 403s and a refused archive. Do not quote the *"bare bones"* or
   the PIRG Docker line in a PR or on the site until someone opens those URLs in a browser.
5. **Whether our ambient persona helps or hurts.** §4.6. One datapoint, about the robot rather than our
   page. This is a creative decision with no evidence behind it in either direction.
6. **Whether 🅒's turn-taking pause works for a *stranger on a web page*** as opposed to a child with a
   robot in the room. The fork proves the latter. Nobody has tested the former.
7. **Every effort estimate here is unvalidated.** They are read off comparable shipped work in this repo,
   not from a spike.

---

## 9. Where this lands on the audit

**Nothing is re-ranked by this page**, and that is deliberate — it is a research brief, not a decision.
[§4.4](../openmoxie-feature-audit.md#44-the-open-backlog-re-ranked-2026-09-05) remains the one place to
look for *"what should I build next."* What this scan hands the rest of the tree:

| Finding | Where it lands |
|---|---|
| **🅐 is a precondition, not an option** | Reinforces [`mobile-first-visit.md`](mobile-first-visit.md) — which is already *"the top live-page item"* — with the reason a *gamification* proposal cannot precede it |
| **r/MoxieRobot is structurally blocked, not merely unreachable** | Upgrades [`community-signals.md`](community-signals.md) §4's largest gap from *"retry next scan"* to *"owner-only, or permanent"* |
| **The Family Edition fork** (`Noonster77`, issue #63, 2026-08-21) | A **new source** carrying two portable behaviours (🅒, 🅓). `community-signals.md` scan 2 recorded both forks as having *empty issue trackers* — true, and their **READMEs** are where the content is |
| **§4.1's upstream "what's missing" list** | `Animal Faces` is missing upstream; our face catalog + customizer shipped (PR #36/#47). Another row for `community-signals.md` §3's *"already solved, nobody knows"* table |
| **§2.3's repo metrics** | 91 stars / 10 watchers vs 8 K views on one grief thread — a positioning fact for [`vision.md`](../vision.md) and the public site, not an engineering one |
| **§0's analytics finding** | Zero analytics anywhere. An owner decision, recorded rather than taken |

> **A note for whoever places this.** [`implementation-plan.md`](../implementation-plan.md) is
> hard-reserved by two other agents at the time of writing, so this brief adds **no row** to it. If the
> owner answers §0, the ⓪ bullet's *"(d) **'Gamify this for regular people'** is NOT yet specified"* is the
> line that should change, and it should point here.

---
📖 [Backlog index](README.md) · [OpenMoxie feature audit](../openmoxie-feature-audit.md) · [Community signals](community-signals.md) · [Mobile first visit](mobile-first-visit.md) · [Live Sim demo](live-sim-demo.md) · [Implementation plan](../implementation-plan.md)
