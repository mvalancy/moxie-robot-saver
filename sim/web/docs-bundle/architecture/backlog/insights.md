# 📈 Insights that mean something — the vocabulary this history does not have (BEYOND #5)

> **Backlog brief v1 · 2026-09-04.** The build document for
> [OpenMoxie feature audit](../openmoxie-feature-audit.md) **§4.2 BEYOND #5** — *"We already ingest
> `Packet` telemetry and gate it on `LoggingPolicy`. Turn it into a local parent console: sessions,
> activity mix, mood trend over time, time-of-day patterns, 'what did we talk about this week' — all
> on-device, nothing uploaded."* — effort **M**, status 🟡 *partial: the durable half shipped
> (PR #55), the insight half is open.*
>
> The audit states the blocker in its own words: those five things *"need a vocabulary this history
> does not have — `Packet.event_name` is a free string and our corpus recovers no module-scoped
> events"*. **§3 removes that decision**; §4 says which parent questions the choice can answer and
> which it refuses; §5 is the vocabulary itself; §6 is how aggregating stays inside the
> `LoggingPolicy` contract.
>
> **Clean-room.** Every claim about our code below was read on `origin/dev` at **`ff2059a`** and cites
> a file and a symbol. **OpenMoxie** (MIT, © Justin Beghtol) is read as prior art and cited by path
> and commit; no upstream code enters this tree. See [`ATTRIBUTION.md`](../../../ATTRIBUTION.md).

---

## 0. The ceiling, stated first

**No physical Moxie has ever sent this server a telemetry `Packet`, and nothing in this brief can
change that.** Every number an insights card shows would come from a simulator — the headless
[`sim/virtual_moxie.py`](../../../sim/virtual_moxie.py) or the browser bridge — or, after this brief,
from turns that our own supervisor answered. That is not a small caveat: it means the *shape* of a
real household's week is unknown to us, and every cap, bucket boundary and idle window below is
**chosen, not measured**. They are all environment variables for that reason, and §10 says so row by
row.

Three more ceilings, up front, so nobody has to find them in a risk table:

- **Nothing on this appliance has ever *produced* a `Packet` either.** `build_packet`
  ([`telemetry.py`:45](../../../mqtt/moxie_sdk/telemetry.py)) is called by four test files and by no
  runtime path; `ingest_telemetry` is reached only from `_on_event`'s `telemetry` / `analytics` /
  `packet*` subtopics ([`moxie_runtime.py`:2850](../../../mqtt/supervisor/moxie_runtime.py)), and our
  SIM publishes on none of them. So the durable store that shipped in PR #55 is, on every appliance
  running today, **empty**. It is correct, tested and unused.
- **We have never scored a child.** The scored fields the audit hopes a mood trend could rest on
  describe *Moxie's outgoing line*, not the child's — see §2.3. `RemoteChatInput`, the message that
  carries a child-side `emotion` / `dialog_act` / `sentiment`, is populated by exactly one field in
  this codebase and that field is `safety` ([`wire.py`:166](../../../mqtt/moxie_sdk/wire.py)).
- **This is a card, not a product.** Nothing here is uploaded, mailed, exported to a third party, or
  turned into a score of a child. The audit's own phrase — *"all on-device, nothing uploaded"* — is
  the boundary, and §6.5 says what would break it.

---

## 1. Why this is 🟡 today, and exactly what a spec has to remove

PR #55 (2026-09-02) shipped the **durable half**, and it is real:

| Shipped | Where | Proof |
|---|---|---|
| Two per-robot collections — a 500-envelope ring and 35 days of daily roll-ups | [`moxie_sdk/telemetry.py`](../../../mqtt/moxie_sdk/telemetry.py) `PACKETS_COLLECTION` / `DAILY_COLLECTION` | [`test_telemetry.py`](../../../sim/tests/test_telemetry.py) — 29 collected |
| The `LoggingPolicy` gate applied **on the way to disk**, not only on the way off the robot | `telemetry.py::storable_packet` → `moxie_runtime.py::_persist_telemetry` | [`test_telemetry_runtime.py`](../../../sim/tests/test_telemetry_runtime.py) — 21 test functions |
| Hydration on first touch, so a restart does not erase the answer | `moxie_runtime.py::_telemetry_buffer` | [`test_sil_durable_telemetry.py`](../../../sim/tests/test_sil_durable_telemetry.py) — 8 functions, 10 collected (one 3-way `parametrize`) |
| A 📈 card rendering a zero-filled week, its real retention window and the lifetime total | [`server/static/app.js`](../../../server/static/app.js) `weekBars` / `refreshInsights`; [`fleet.py::normalize_telemetry`](../../../server/moxie_server/fleet.py) | `test_console_roundtrip.py` |

What is **not** shipped is everything the row was actually about: *sessions, activity mix, mood trend,
time-of-day patterns, "what did we talk about this week"*. And the reason is not effort. It is that
the only thing the shipped store can group by is `Packet.event_name`, which the recovered proto
declares a **free string** and for which our corpus establishes **no vocabulary at all** — the point
[`schedule.py::telemetry_signals`](../../../mqtt/moxie_sdk/schedule.py) already makes in the code, in
a docstring, with `carries_module_signal: False` returned as a fact:

> `event_name` is a free string and `event_data` opaque bytes: **our RE corpus recovers no
> module-scoped event vocabulary**, so nothing here says "the child launched STORY and quit after
> 40 s".

**A parent-facing "mood trend" built on a free-string event name is a chart of noise.** So this brief
does not start with a chart. It starts with §3: where the words come from.

---

## 2. What is actually true today — verified on `origin/dev` at `ff2059a`

### 2.1 The store, the caps, and the gate

`robots/<device>/telemetry_packets.json` is a ring of at most `MAX_PACKETS` (500) envelopes;
`robots/<device>/telemetry_daily.json` is one row per calendar day, at most `MAX_ROLLUP_DAYS` (35),
each row `{count, by_event, first, last}` with at most `MAX_DAY_EVENTS` (24) distinct names and the
overflow counted under `(other)` ([`telemetry.py`:142-175, `roll_up_packet`:320](../../../mqtt/moxie_sdk/telemetry.py)).
Both are written by `_persist_telemetry` and by nothing else.

`storable_packet(pkt, policy)` is the gate, and it fails closed:

| Policy | On disk |
|---|---|
| `NO_DATA` (0) | **`None`** — no packet, no count, no day row |
| `NO_MEDIA` (1, the effective default via `TELEMETRY_POLICY`) | the envelope **without** `event_data`, plus `event_data_withheld: "NO_MEDIA"` |
| `FULL` (2) | the whole envelope, `event_data` truncated at 2 048 base64 chars |

The `NO_MEDIA` row is the one that shapes this entire design, and §5.2 explains why: **under the
default policy an event's payload does not exist**, so any meaning an insights layer needs must live
in the `event_name` — the only field that survives the gate and the only field the roll-up counts.

### 2.2 The card

[`app.js::refreshInsights`](../../../server/static/app.js) already renders a zero-filled week
(`weekBars`), the by-event table, the newest envelopes, and a footer stating the true retention
window. Under `persisted:false` it says *"nothing is being saved"* rather than drawing an empty week
as if it were a quiet one. That honesty is load-bearing and §4.2 extends it rather than replacing it.

One detail matters for §6: in the `NO_DATA` branch the card still lists whatever is in the
supervisor's RAM, and **says so** — *"this card can only show what has arrived since the supervisor
started, and a restart clears it"*. That is defensible for a raw event list. It would **not** be
defensible for an aggregate, which is the rule §6.1 makes explicit.

### 2.3 The scored fields are on every published turn — and are written down nowhere

This is the load-bearing fact of the whole brief, so it is stated as a measurement.

`MoxieRuntime._stage` ([`moxie_runtime.py`:3189](../../../mqtt/supervisor/moxie_runtime.py)) is *"the
single place a published turn becomes a scored turn"*. It runs on every path that says words — the
model reply (`:3018`), each streamed chunk (`:3251`), greetings (`:2697`, `:2766`), the safety
redirect (`:2427`), the not-paired line (`:2158`) — and returns `(markup, scored)` where `scored`
carries `mood` (11 values), `mood_intensity` (0-2), `dialog_act` (22), `emotion` (7) and `signal`
(9), each validated against a frozen catalogue in [`vocab.py`](../../../mqtt/moxie_sdk/vocab.py)
before it may reach the wire. `_publish_chat` (`:4378`) puts them on `RemoteChatOutput`.

**Every store write in `moxie_runtime.py`, enumerated:**

```
grep -n "self.store.append\|self.store.write" mqtt/supervisor/moxie_runtime.py
```

returns seventeen call sites across thirteen collections — `conn_events`, `roster`, fleet `config`, `permits`,
`safety_counts`, `safety_events`, `telemetry_packets`, `telemetry_daily`, `content_items`,
`content_backup`, `content_packs`, `mentor_behaviors` and `schedule_explain`. **`scored` appears in
none of them.** Nor does it reach `_save_memory` (which stores `{role, content}` pairs only, `:3269`)
or `MemoryStore`. It is computed, put on the wire, used to drive the face, and dropped.

So the audit's most attractive option — *"the scored fields, they are already on the wire"* — is, as
a history, **empty**. A field that is published and dropped is not a history. §3 takes that as its
first finding and the second one, below, as its second.

**The second finding is worse for the mood idea than the first.** `_stage(text, obj, …)` is always
called on **Moxie's own outgoing line**. `dialog_act` comes from `performance.py::classify`, a
regex cascade over that line (`_Q_OPINION`, `_Q_YES_NO`, `_COMMAND`, …,
[`performance.py`:346](../../../mqtt/moxie_sdk/performance.py)); `mood` comes from the same rule pass
unless the app supplied one. The child's side is scored **nowhere**: `build_chat_response` sets
`resp["input"]` to `{"safety": …}` and nothing else ([`wire.py`:166](../../../mqtt/moxie_sdk/wire.py)),
so `RemoteChatInput.emotion`, `.dialog_act`, `.sentiment` — the recovered fields that would carry a
child-side affect signal — are never populated.

> A "mood trend" drawn from `scored` would be a chart of **our own regex classifier's opinion of our
> own LLM's word choice**, labelled with a child's name. It is not a measurement of a child. It is
> barely a measurement of Moxie.

### 2.4 `mentor_behaviors` — recovered, durable, and today ungated

`ingest_mentor_behavior` ([`:3870`](../../../mqtt/supervisor/moxie_runtime.py)) parses an
`ActivityUpdate.mentor_behavior` (Cloud.proto:241) down to
`MENTOR_BEHAVIOR_FIELDS = ("module_id", "content_id", "content_day", "timestamp", "action",
"instance_id", "ended_reason")` ([`wire.py`:231](../../../mqtt/moxie_sdk/wire.py)) and appends it to
`robots/<id>/mentor_behaviors.json`, capped at `MAX_MENTOR_BEHAVIORS` (500). Every record carries a
`timestamp`, so a week's activity mix is a straight filter over the ring — no roll-up needed.

Two things about it a build agent must not blur:

1. **`action` and `ended_reason` are not recovered enums.** The code says so in as many words:
   *"the docs give the enums but not their JSON spelling, so we keep whatever the robot sent
   verbatim"* (`wire.py`:227-229). They are narrower free strings, not a vocabulary.
2. **The append is not `LoggingPolicy`-gated.** `:3877` is a bare `self.store.append(...)`; there is
   no `telemetry_policy` / `memory_policy` check on the path. A `NO_DATA` household's robot writes a
   durable, per-child record of every activity it finished or quit. That is a **precondition defect**
   for this brief (§6.4), not a design choice to inherit.

### 2.5 Two live findings, made while writing this page

Both were read, not inferred. Neither is caused by this brief; both would bite the agent that builds it.

- **The rolling transcript is not `LoggingPolicy`-gated.** `_save_memory`
  ([`:324`](../../../mqtt/supervisor/moxie_runtime.py)) writes `self.history[device_id]` — the child's
  and Moxie's actual words — to `$MOXIE_MEMORY_DIR/<device>.json` whenever `MOXIE_MEMORY_DIR` is set,
  with no policy check anywhere on the path (`_remember`:3269 and `_ingest_notify`:3857 are its only
  callers, and neither checks either). `docker-compose.yml`:103 sets it to `/data/memory` by default.
  Long-term *memory* is properly gated (`MemoryStore.writes_allowed` ← `memory_policy`); the
  transcript underneath it is not. **This is the single largest reason §4.3 refuses "what did we talk
  about this week": the store that question would read is the one store whose privacy gate is
  missing.**
- **Telemetry has no erasure.** `do_DELETE` accepts exactly one path, `/memory`
  ([`:987-995`](../../../mqtt/supervisor/moxie_runtime.py)); `POST /memory {"erase": …}` is its twin.
  There is no way for a parent to delete `telemetry_packets`, `telemetry_daily` or
  `mentor_behaviors`. The contract's third leg — *"never policy-gated: a parent must always be able to
  delete"* (`erase_memory`, `:407`) — has no telemetry counterpart. §6.3 adds one.

### 2.6 Prior art, verified

- **Upstream OpenMoxie has no telemetry handling at all.** At `c8c2d380efd37d2e83761957587f5d08f73b3a63`,
  a case-insensitive `grep -rniE "telemetry|analytics|Packet\b"` across `site/` (excluding the
  generated `protos/`) returns **nothing**. There is no `Packet` ingest, no analytics model, no
  insights view. Its `MentorBehavior` model (`site/hive/models.py`:80-99) carries exactly the seven
  fields our `MENTOR_BEHAVIOR_FIELDS` carries, and `robot_data.py::get_mbh` orders by `-timestamp` —
  which is where our own newest-first ordering came from and is credited in
  `moxie_runtime.mentor_behaviors`'s docstring. **There is no upstream insights layer to port.**
- **Fork A (`Noonster77/openmoxie`, `a97c85c`) is the nearest prior art, and it is a transcript
  logger, not an insights layer.** `site/hive/mqtt/conversation_log.py` (86 lines) does a dual write —
  a `ConversationEvent` DB row *and* an append to a per-day `.txt` under the data dir, device id
  path-sanitized, `threading.Lock`-guarded, with an 8-second dedup window because notify messages
  repeat a just-recorded turn. Two behaviours in it are worth taking as **ideas** and are credited as
  such: the **8-second dedup** (a report can arrive twice; a counter must not double-count it — §5.3
  applies the same rule to `moxie.turn`) and **`rewrite_daily_transcript`**, which regenerates the
  day's file from the database after a parent deletes, *unlinking it when empty* — deletion that
  actually deletes, which is the shape §6.3's `DELETE /telemetry` copies. Its safety half
  (five regex categories) was already credited and superseded by
  [`moxie_sdk/safety.py`](../../../mqtt/moxie_sdk/safety.py):33. No code is copied.

---

## 3. The vocabulary — four options, compared, then one chosen

### 3.1 The comparison

The audit names three sources. A fourth belongs in the table because it is the honest baseline: build
nothing and say the row cannot be done.

| | (a) `mentor_behaviors` | (b) Events we emit ourselves | (c) The scored fields | (d) Refuse the row |
|---|---|---|---|---|
| **Recovered or invented?** | **Recovered** — `ActivityUpdate.mentor_behavior`, Cloud.proto:241; seven named fields | **Invented**, but *closed by construction* and server-owned | **Recovered** — `RemoteChatOutput` / `RemoteDialog.DialogAct` / `EmotionState` / `RemoteSignals.Signal`, four frozen catalogues | n/a |
| **…but is the *value space* recovered?** | Partly. `action` / `ended_reason` spellings are **not** (`wire.py`:227) | Yes — we define it, so it is closed and total | Yes — 11 / 3 / 22 / 7 / 9, validated in `_stage` before the wire | n/a |
| **Survives a real robot?** | **Yes** — it *is* the robot's own report, and upstream's field-proven server stores the same seven fields | **Yes, trivially** — minted server-side from turns that already reached us; the robot need not know | **Yes** — already on every published turn | n/a |
| **Can a parent read it?** | Yes: *"finished Bedtime Story, quit Trivia"* | Yes, if we name them for parents rather than for logs | **No, not as claimed.** It describes Moxie's line, not the child (§2.3) | n/a |
| **Does it exist today?** | **Yes** — durable, capped, ungated (§2.4) | **No** — zero call sites of `build_packet` outside tests | **On the wire only. Persisted nowhere** (§2.3) | Yes |
| **Covers free conversation?** | **No** — silent unless a content module runs | Yes | Yes | n/a |
| **Covers a robot-less appliance (SIM only)?** | Yes (the SIM reports them) | Yes | Yes | n/a |

### 3.2 The choice

**Chosen: (b) — a small, closed, server-owned event vocabulary minted from the turn loop into the
`telemetry_packets` / `telemetry_daily` store that already exists — with (a) `mentor_behaviors` kept,
unchanged and un-replaced, as the *activity* dimension it already serves.**

The three candidates are not parallel and the comparison above is what shows it: **(c) is a vocabulary
with no history and (b) is a history with no vocabulary.** The interesting question is which vocabulary
to give (b), and the answer is: the smallest closed set that answers a real parent question, with every
value either a count or a member of a recovered catalogue.

Why (b), against each alternative:

- **Against (c).** Two independent disqualifiers, both measured in §2.3. It is persisted nowhere, so
  choosing it means building (b)'s machinery anyway — and once you have built (b), the scored fields
  add nothing a parent can act on, because they are Moxie's, not the child's. Persisting them would
  cost the same disk and produce a chart we would have to caption *"this is our regex's opinion of our
  LLM's phrasing"*. **The strongest-looking option on paper is the one this brief refuses hardest.**
- **Against (a) as the base.** `mentor_behaviors` is genuinely good and is *kept* — but it is silent
  on the appliance most people will run. Our default brain is a conversational LLM; a household with
  no content schedule produces zero `mentor_behavior` reports, and the card would read "no data"
  forever while the child talked to Moxie every day. It answers *what she did*; it cannot answer
  *whether she used it at all*.
- **Against (d).** The row is not un-doable. Three of the five parent questions in §4.1 are answerable
  from four counted events and a clock we control. Refusing all five would be a different kind of
  dishonesty.

**What keeps (b) from being "invented" in the pejorative sense — four rules, all checkable:**

1. **Closed.** The vocabulary is exactly four names (§5.1), frozen as a literal in the test file, so
   adding one requires a test edit and a reviewer.
2. **Server-owned and reserved.** Every name begins `moxie.`; a *robot* packet arriving with that
   prefix is re-filed under `robot:<name>` on ingest, so a device cannot forge our counters.
3. **No free text, ever.** An event carries no `event_data`. There is nothing to withhold, so the
   `NO_MEDIA` default and `FULL` produce byte-identical rows — see §5.2.
4. **Derived from state we already have**, never from a new inference over a child's words. No model
   call, no classifier, no topic labeller anywhere in P0.

### 3.3 What we are giving up, said plainly

- **No mood trend.** Not in P0, not in P1, not in P2 as specified. §4.3.
- **No topics.** *"What did we talk about this week"* is refused; §4.3 says on what condition it could
  be reconsidered, and §2.5 shows that condition is not met today.
- **Sessions are ours, not the robot's.** The recovered envelope has a `moxie_session_id` field and a
  real robot would supply it. We derive a session from *our* turn stream instead, with an idle window.
  When a real robot does start sending packets, the two definitions will disagree, and the reconciler
  is a P1 line item (A5), not a pretence that they are the same thing.
- **Nothing is per-child.** The appliance builds one `ChildProfile` from an environment variable
  (BEYOND #10's live gap; `mqtt/run.py`:35). Every number here is **per robot**, and the card must say
  "this robot", never a child's name.

---

## 4. What a parent actually sees

### 4.1 Five questions; three answered, two refused

Not a dashboard of everything measurable. These are the questions the audit's own one-liner names,
plus the one it implies.

| # | The question a parent asks | Answerable? | From what |
|--:|---|:--:|---|
| Q1 | *"When does she use it?"* | ✅ **Yes** | `moxie.turn` events bucketed into `morning / afternoon / evening / night` per day (§5.3). Our clock, not the robot's |
| Q2 | *"How much — is it every day, or was that one Saturday?"* | ✅ **Yes** | The zero-filled week that already ships, plus `moxie.session.start` counts and `moxie.turn` counts |
| Q3 | *"What did she do?"* | ⚠️ **Partly** | `mentor_behaviors` — module, and finished-vs-quit **as the robot spelled it**. Covers scheduled activities only, and the card must say so when the list is empty |
| Q4 | *"Is she enjoying it?"* | ❌ **Refused** | §4.3 |
| Q5 | *"What did we talk about this week?"* | ❌ **Refused** | §4.3 |

### 4.2 The card

The 📈 card keeps everything it has and gains three rows plus one sentence. Nothing is added that
cannot be sourced from the table above.

```
📈 Insights · 214 events kept · 1 903 all time
[ the zero-filled week bar row — already ships ]

When            🌅 12   ☀️ 41   🌆 63   🌙 2       ← Q1, over the retained window
Conversations   9 this week · 118 turns           ← Q2
What she did    Bedtime Story ×3 (finished)       ← Q3, from mentor_behaviors
                Trivia ×2 (1 finished, 1 quit)
                — or: "No scheduled activities this week. Free conversation
                   isn't listed here, because the robot only reports an
                   activity when a content module runs."

History since 2026-08-21. Kept on this box: the newest 500 events and 35 days of
daily counts. Data sharing is NO_MEDIA, so event payloads are never written —
only what happened and when.
This card does not measure how your child feels. Nothing on this appliance does.
```

That last line is not decoration. It is the one sentence that stops a parent reading Q1/Q2 as a
wellbeing signal, and it is an acceptance criterion (§8.7).

### 4.3 The refusals, and why refusing is the design

**Q4 — *"is she enjoying it"* — refused.** There is no child-side affect signal anywhere in this
system. `RemoteChatInput.emotion` / `.dialog_act` / `.sentiment` exist in the recovered proto and are
populated by nothing (§2.3); the `scored` fields describe Moxie's own line and come mostly from
regexes over it. The nearest honest proxies are **behavioural**, and they are already in Q2 and Q3:
did she come back, and did she finish. The card may show those. It may not label them *enjoyment*,
and it may not average them into a number.

**Q5 — *"what did we talk about this week"* — refused, and the refusal is not permanent.** Answering
it needs either (i) module ids, which free conversation does not produce, or (ii) a topic label
derived from the child's actual words. (ii) is a new inference over a child's speech, in a house where
§2.5 has just established that the store holding those words **is not gated by `LoggingPolicy` at
all**. Building a parent-facing feature on top of an ungated child-speech store is exactly the mistake
this brief exists to avoid. The precondition for reconsidering Q5 is written into P2 (§9): gate
`_save_memory`, give the transcript its own erasure, and give topic labelling its own explicit consent
surface separate from `logging_policy`. Until all three, no.

**A "mood trend over time" — refused as named.** It could be *drawn* today from `scored`, cheaply, and
it would look convincing. That is precisely why it is the most dangerous chart in the row.

> **The rule this brief asks a build agent to hold:** if the honest caption for a chart is *"this is
> our rule engine's opinion of our language model's phrasing"*, the chart does not ship. Refusing to
> draw a chart you cannot honestly source is part of the design, not a gap in it.

---

## 5. The event vocabulary, exactly

### 5.1 Four names, and nothing else

| `event_name` | Minted where | Answers |
|---|---|---|
| `moxie.robot.connect` | `_device_connect` | *Was the robot even on?* — separates "she didn't talk to it" from "it was unplugged" |
| `moxie.session.start` | first `moxie.turn` after ≥ `MOXIE_SESSION_IDLE_S` of no turns | Q2 (conversations) |
| `moxie.session.end` | `_end_conversation` ([`:462`](../../../mqtt/supervisor/moxie_runtime.py) — `exit` / `disconnect` / `module_switch`), or lazily at the next turn when the idle window elapsed | Q2, and P1's durations |
| `moxie.turn` | once per published **answer** in `_publish_chat`, not once per streamed chunk | Q1, Q2 |

Four, deliberately. `MAX_DAY_EVENTS` is 24 and overflow is first-come (`roll_up_packet`:345), so a
small reserved vocabulary leaves twenty slots for whatever a real robot turns out to send.

**No activity events.** `mentor_behaviors` already stores the activity dimension durably, with a
`timestamp` on every record, and it is what the schedule planner and the 📅 card already read. Minting
`activity.done` / `activity.quit` would require mapping `action` and `ended_reason` — the two fields
our own code says we have **not** recovered the spellings of (§2.4). The card joins the two stores;
it does not invent an enum.

**`moxie.session.end` is stamped with the moment it describes, not the moment it is written.** A
session that ended because the child walked away is only noticed at the next turn, possibly days
later. `packet_day` uses `recorded_at` when it is plausible ([`telemetry.py`:266](../../../mqtt/moxie_sdk/telemetry.py)),
and our own stamps always are — so the row lands on the correct day even though the write is late.

### 5.2 Why the meaning lives in the name and never in the payload

Under `NO_MEDIA` — the effective default — `storable_packet` **removes `event_data` entirely** and
replaces it with `event_data_withheld: "NO_MEDIA"`, because `event_data` is declared `bytes` and our
corpus types none of them, so nothing lets us prove a blob is not audio. That is not a limitation to
work around; it is the contract.

The consequence is a hard design rule: **an insights layer that puts a number in `event_data` is a
layer that does not work under the default policy.** So a minted event carries no payload at all —
`build_packet(name, b"", …)` — and every quantity is either a *count of a named event* or a
*difference between two `recorded_at` stamps*. Under `NO_MEDIA` and under `FULL`, a minted row is
byte-identical apart from the withheld marker, which is an assertion (§7, T4).

This also makes the vocabulary's size the whole information budget, which is why §5.1 is four rows and
not fourteen.

### 5.3 What the day row gains — one key

`telemetry_daily`'s row is `{count, by_event, first, last}`. Q1 needs a time-of-day distribution and
`first`/`last` cannot give one. So `roll_up_packet` gains one optional key:

```
"buckets": {"morning": 3, "afternoon": 11, "evening": 6, "night": 0}
```

Four fixed keys, ~40 bytes a day, computed with the **existing** `schedule.time_bucket`
([`schedule.py`:381](../../../mqtt/moxie_sdk/schedule.py) — `morning` 05:00-11:59, `afternoon`
12:00-16:59, `evening` 17:00-20:59, `night` 21:00-04:59), from the same stamp `packet_day` already
resolves. Signature: `roll_up_packet(rollup, pkt, *, now=None, max_days=None, bucket_events=(TURN,))`.

**`bucket_events` defaults to the turn event alone, and that matters.** Bucketing *every* packet would
mean a robot's heartbeats moved the "when does she use it" chart. The bucket counts turns, so the
chart is *when she talked*, not *when the robot phoned home*. `_clean_rollup` normalises the key
defensively like every other, so an old file without it reads as zeros.

**Dedup, borrowed from Fork A.** A streamed answer publishes many chunks; `_publish_chat` is reached
once per chunk. `moxie.turn` is minted once per **answer**, keyed on `event_id`, with a short
suppression window — the same defect Fork A's 8-second dedup exists for
(`conversation_log.py::record_conversation`, credited in §2.6). Getting this wrong inflates every
number on the card by the streaming chunk count, so it has its own test (T2).

### 5.4 The reserved-prefix guard

Two guards, both one-liners, both tested:

1. **On ingest** — a robot packet whose `event_name` starts with `moxie.` is re-filed as
   `robot:<name>` in `ingest_telemetry`, before `_persist_telemetry`. A device cannot forge a counter
   the card reads.
2. **In the roll-up** — `roll_up_packet` never overflows a reserved name into `(other)`. Today the
   24-name cap is first-come, so a robot spraying 24 names at breakfast would push the afternoon's
   `moxie.turn` into the overflow bucket and silently zero the chart. `reserved=RESERVED_EVENTS` is
   checked before the cap.

---

## 6. The privacy contract — how aggregation stays inside it

Three things were verified by execution against this tree before this page was written, and an
insights layer must not weaken any of them: `NO_DATA` writes nothing, `NO_MEDIA` withholds
`event_data`, and erasure is never policy-gated.

### 6.1 The four rules

| # | Rule | Why, and how it is checked |
|--:|---|---|
| **M1** | **Minted events go through `_persist_telemetry`, never `store.append`.** | `_persist_telemetry` calls `storable_packet`, which returns `None` under `NO_DATA`. One gate, the existing one, on one path. T5 asserts both files are byte-unchanged after 50 turns under `NO_DATA` |
| **M2** | **Every aggregate is computed from `telemetry_daily` (post-gate), never from `_telemetry_buffer`.** | `ingest_telemetry` appends to the RAM buffer *before* `_persist_telemetry` (`:2546-2551`), so under `NO_DATA` the buffer holds packets the disk correctly refused. A card that aggregated the buffer would have aggregated around the gate. T6 |
| **M3** | **A minted event carries no payload.** | §5.2. There is nothing for `NO_MEDIA` to withhold, so aggregation cannot smuggle content past it. T4 |
| **M4** | **No new inference over a child's words.** | No classifier, no topic model, no model call anywhere in P0. The only new computation is counting and subtracting timestamps |

M2 is the one an implementer is most likely to get wrong, because `_telemetry_buffer` is right there,
already hydrated, and returns a list. It is the correct source for the *raw event list* the card
already shows (which is labelled) and the wrong source for **every number**.

### 6.2 What a `NO_DATA` household sees

Not a blank card. Three things:

1. **The connection strip, in full.** [`conn_telemetry.py`](../../../mqtt/moxie_sdk/conn_telemetry.py):30-35
   states the reasoning and it is correct: *"Every field is a topic, a device id, a reason code or a
   duration. No transcript, no packet payload, no `event_data` — so `LoggingPolicy` does not gate this
   the way it gates `telemetry.py`, because there is nothing about the child in it to gate. […] a
   parent who has turned off data sharing has not asked to be blinded to their own appliance's
   health."* Outages, refusals, drops, lock timeouts, gap stats — all of it.
2. **The live event list, labelled**, exactly as it is today: *"Data sharing is NO_DATA, so nothing is
   being saved — this card can only show what has arrived since the supervisor started, and a restart
   clears it."*
3. **No week, no buckets, no session count, no activity mix.** Those are the aggregates, and under
   `NO_DATA` there is nothing behind them. The card says the sentence in (2) instead of drawing empty
   axes.

### 6.3 Erasure — the leg that does not exist yet

`DELETE /telemetry?device_id=…` — deletes `telemetry_packets`, `telemetry_daily` **and**
`mentor_behaviors` for that robot. Modelled line for line on `erase_memory`
([`:407`](../../../mqtt/supervisor/moxie_runtime.py)) including its docstring rule — *"Never
policy-gated: a parent must always be able to delete"* — and on Fork A's `rewrite_daily_transcript`,
which unlinks the day's file when it becomes empty rather than leaving a husk (§2.6). `JsonStore`
already has `delete()`. Proxied as `DELETE /local/robots/{id}/telemetry`; a ✕ on the card with one
confirmation.

**Its cost, stated:** erasing `mentor_behaviors` degrades the schedule planner, which reads that
history to stop re-offering finished activities and to end FTUE
(`plan_schedule_for`, [`:3884`](../../../mqtt/supervisor/moxie_runtime.py)). A parent who erases will
see repeats. The confirmation says so in one sentence. Deletion wins anyway.

### 6.4 Two preconditions, both small, both required before the card ships

- **P-1 — gate `ingest_mentor_behavior` on `telemetry_policy`.** One `if` (§2.4). Without it the card
  renders, to a `NO_DATA` parent, a durable per-child activity history their setting said not to keep.
  **Cost, stated:** under `NO_DATA` the planner loses completion affinity and FTUE cannot end, so
  activities repeat. That is the correct trade — a parent who turned data sharing off chose it — and
  the ⚙️ settings card should say it beside the toggle.
- **P-2 — do not read the transcript.** §2.5's ungated `_save_memory` is out of scope to *fix* here,
  and firmly in scope to **not build on**. No P0 or P1 code path may read `self.history` or
  `$MOXIE_MEMORY_DIR`. Filed as a separate defect for a separate PR; it is named in §9's risks so it
  cannot be lost.

### 6.5 What an insights layer must never gain

No upload. No export to any host. No cross-robot or cross-household comparison ("children like yours
talk 12 % more"), which is a benchmark of a child and is the exact thing "all on-device, nothing
uploaded" was written to forbid. No retention beyond the caps a parent can read on the card. No number
that survives `DELETE /telemetry`.

---

## 7. Tests

Hermetic first: pure Python, no broker, no robot, no network. The pure half extends
`test_telemetry.py`; the runtime half gets its own file so the seam is read by the eyes that care
about it.

### 7.1 Pure — `sim/tests/test_telemetry.py` (extended)

| # | Name | Asserts |
|--:|---|---|
| **T1** | `test_the_day_row_buckets_only_the_events_it_was_told_to` | Three `moxie.turn` stamps across two buckets plus five robot heartbeats in a third → `buckets` counts three, in two buckets. The heartbeat bucket is absent, not zero-with-a-count |
| **T3** | `test_a_reserved_name_never_overflows_into_other` | Fill a day with 24 robot names, then a `moxie.turn`: the turn is counted under its own name and something else takes `(other)`. Fails today (`roll_up_packet`:345 is first-come) — write it red first |
| **T4** | `test_a_minted_row_is_identical_under_no_media_and_full` | `storable_packet(mint(...), NO_MEDIA)` equals the `FULL` row except for the withheld marker; assert `event_data` is absent from the minted packet before the gate ever sees it |
| **T7** | `test_the_vocabulary_is_exactly_four_names` | `RESERVED_EVENTS` equals a frozen literal in the test. Adding a fifth event requires a test edit |
| **T8** | `test_an_old_rollup_without_buckets_reads_as_zeros` | `_clean_rollup` over a pre-upgrade file: no `KeyError`, no crash, `buckets` all zero. The store is another process's JSON and is never trusted to be well-typed |

Any new test that reads a clock must be registered in
[`test_clock_dependence.py`](../../../sim/tests/test_clock_dependence.py) with its construct list and
a `RELATIVE` / `DETERMINISTIC` justification — that file already carries four telemetry rows and the
guard is asserted from both directions.

### 7.2 Runtime — `sim/tests/test_insights.py` (new)

| # | Name | Asserts |
|--:|---|---|
| **T2** | `test_a_streamed_answer_mints_exactly_one_turn` | Publish a 5-chunk streamed reply; exactly one `moxie.turn` lands. Then re-deliver the same `event_id` inside the window: still one. **The inflation bug this test exists for is Fork A's 8-second dedup, ported as a behaviour** |
| **T5** | `test_no_data_mints_nothing_at_all` | 50 turns under `logging_policy=NO_DATA`: `telemetry_packets.json` and `telemetry_daily.json` do not exist (or are byte-unchanged), and `mentor_behaviors.json` is byte-unchanged too (P-1). The turns still answer normally |
| **T6** | `test_no_aggregate_is_ever_computed_from_the_ram_buffer` | Under `NO_DATA`, ingest three robot packets so the RAM buffer is non-empty, then assert `telemetry_view` returns `history: []`, zero buckets, zero sessions — while the labelled raw event list still shows the three |
| **T9** | `test_a_session_is_derived_across_a_supervisor_restart` | Turn, restart the runtime, turn again inside the idle window → one session, not two. The hydrate-on-first-touch pattern `_telemetry_buffer` already documents |
| **T10** | `test_a_robot_cannot_forge_a_reserved_event` | Ingest a robot packet named `moxie.turn`; it is stored as `robot:moxie.turn`, the session count does not move, and the buckets do not move |
| **T11** | `test_erasure_is_never_policy_gated` | Under all three policies, `DELETE /telemetry` removes all three collections and returns `ok:true`. Under `NO_DATA`, where there was nothing to remove, it still returns `ok:true` |
| **T12** | `test_the_card_payload_says_what_it_does_not_measure` | `normalize_telemetry` carries the §4.2 sentence (or a flag the card renders it from) on every `ok:true` response, and `test_console_roundtrip.py` asserts it reaches the DOM |
| **T13** | `test_an_appliance_with_no_content_module_still_answers_q1_and_q2` | Zero `mentor_behaviors`, ten conversational turns: buckets, sessions and the week are all populated; the activity list renders its explicit empty state, not "no data" |

### 7.3 What only a real deployment can settle

- Whether `MOXIE_SESSION_IDLE_S` (600 s, chosen) matches how a child actually uses a robot. A week of
  a real household is the only evidence, and there has never been one.
- Whether `moxie.turn` at ~1 packet per answer overruns the 500-envelope ring faster than a robot's
  own traffic would. The daily roll-up is what answers "last week", so the ring is a *"just now"*
  cache — but the sizing note in `telemetry.py`:160 is explicit that no measured rate exists.
- Whether a parent reads the §4.2 card and comes away with an accurate belief, in particular about the
  refusal sentence. That is a human test with a human subject and we have neither.

---

## 8. Acceptance criteria

**P0 is accepted when all of these are true:**

1. The vocabulary is exactly four `moxie.`-prefixed names, frozen as a literal in a test (T7), and no
   fifth can be added without a test edit.
2. A minted event carries **no** `event_data`, and its stored row is identical under `NO_MEDIA` and
   `FULL` (T4).
3. Every minted event reaches disk through `_persist_telemetry`, so `NO_DATA` writes **nothing** —
   proven over 50 turns with both files byte-unchanged (T5).
4. Every number the card shows is computed from `telemetry_daily` or `mentor_behaviors`, and **none**
   from `_telemetry_buffer` (T6). Under `NO_DATA` the card shows the connection strip, the labelled
   live list, and no aggregate at all.
5. `ingest_mentor_behavior` is `LoggingPolicy`-gated (P-1), and the ⚙️ card states the scheduling cost
   beside the toggle.
6. `DELETE /telemetry` exists, removes all three collections, is **never** policy-gated, and its
   confirmation states the scheduling cost (T11).
7. The card answers Q1, Q2 and Q3, renders an explicit empty state for Q3 on an appliance with no
   content module (T13), and carries the sentence *"This card does not measure how your child feels"*
   (T12).
8. **No mood chart, no topic list, no scored field is persisted or rendered anywhere** — asserted as
   an absence: `grep` for the scored keys over the insights code path returns nothing.
9. A robot cannot forge a reserved event (T10) and cannot push one into `(other)` (T3).
10. A streamed answer mints exactly one turn, and a redelivered `event_id` mints none (T2).
11. `sim/tests/test_insights.py` is wired into a CI tier in `sim/ci/*.yml` **and** the installed copy
    under `.github/workflows/` in the same commit — otherwise
    [`test_ci_test_coverage.py`](../../../sim/tests/test_ci_test_coverage.py)'s two-directional ratchet
    fails, correctly.
12. [`config-and-telemetry-contract.md`](../config-and-telemetry-contract.md) §③ documents the reserved
    vocabulary, the `buckets` key, the no-payload rule and the erasure route.

**P1 adds:** session **durations** (a bounded `active_minutes` per day row, paired from stamps);
activity mix by *category* via `schedule.py::module_label` and the 23-module catalog; a reconciler for
a real robot's `moxie_session_id` against our derived sessions (A5); a CSV export a parent can keep;
and the `_save_memory` policy gate as its own PR.

**P2 adds:** *"what did we talk about this week"* — and **only** behind all three of its preconditions
(§4.3): the transcript gated, the transcript erasable, and topic labelling behind its own explicit
consent surface separate from `logging_policy`.

---

## 9. Effort and the file list

### P0 — **S/M**, one agent, one sitting, shippable alone

| Order | File | Change |
|--:|---|---|
| 1 | [`mqtt/moxie_sdk/telemetry.py`](../../../mqtt/moxie_sdk/telemetry.py) | `RESERVED_EVENTS` + the four names; `roll_up_packet(…, bucket_events=…, reserved=…)`; `buckets` in the row and in `_clean_rollup`; `history_view` carries it through. Pure. ~50 LOC |
| 2 | `sim/tests/test_telemetry.py` | T1, T3, T4, T7, T8 — **T3 red first** (the cap is first-come today) |
| 3 | [`mqtt/supervisor/moxie_runtime.py`](../../../mqtt/supervisor/moxie_runtime.py) | `_mint(event)` → `_persist_telemetry`; call sites in `_device_connect`, `_publish_chat` (once per answer, `event_id`-deduped), `_end_conversation`; session derivation cached in `RobotContext.extra`, hydrated from the **stored** ring; the reserved-prefix rename in `ingest_telemetry`; the `telemetry_policy` gate on `ingest_mentor_behavior` (**P-1**); `erase_telemetry` + `DELETE /telemetry`; the activity/session/bucket fields on `telemetry_view` |
| 4 | `sim/tests/test_insights.py` | **New.** T2, T5, T6, T9-T13 |
| 5 | `sim/tests/test_clock_dependence.py` | Register every new clock-reading test with its justification (both-directions ratchet) |
| 6 | `sim/ci/ci.yml` **and** `.github/workflows/*` | Wire the new test file into a tier — **template and installed copy in the same commit** |
| 7 | [`server/moxie_server/fleet.py`](../../../server/moxie_server/fleet.py) | `normalize_telemetry` carries `buckets`, `sessions`, `turns`, `activity`, and the refusal sentence; `normalize_activity` (new, pure) folds `mentor_behaviors` into rows |
| 8 | [`server/moxie_server/main.py`](../../../server/moxie_server/main.py) | `DELETE /local/robots/{id}/telemetry` proxy, same shape as the existing telemetry proxy |
| 9 | [`server/static/app.js`](../../../server/static/app.js) | The three new rows, the empty state, the refusal sentence, the ✕ with its confirmation |
| 10 | `sim/tests/test_console_roundtrip.py` | The card payload end to end, including T12's sentence |
| 11 | [`docs/architecture/config-and-telemetry-contract.md`](../config-and-telemetry-contract.md) | §③: the reserved vocabulary, `buckets`, the no-payload rule, erasure |
| 12 | [`docs/architecture/openmoxie-feature-audit.md`](../openmoxie-feature-audit.md) | Flip BEYOND #5's status in the same PR (the backlog README's house rule) |

Not in P0, deliberately: durations, per-child anything, categories, CSV export, the `moxie_session_id`
reconciler, the `_save_memory` gate, and every form of topic extraction.

### P1 — **M**
Durations · activity mix by category · the real-robot session reconciler · CSV export · the
`_save_memory` policy gate and a transcript erasure route, as their own PR.

### P2 — **L**
Q5, behind all three preconditions · a per-child split, which is blocked on BEYOND #10's identity gap
and cannot be faked with an env var.

### Risks

| # | Risk | Mitigation |
|--:|---|---|
| R1 | **A minted turn event inflates by the streaming chunk count**, silently multiplying every number by ~5. | T2, and the `event_id` dedup ported from Fork A's 8-second window (§2.6). Mint at the answer, never the chunk |
| R2 | The vocabulary grows until the card is a log again, and `MAX_DAY_EVENTS` overflow starts eating reserved names. | Four names, frozen in T7; `reserved=` in the roll-up (T3); every new name needs parent-facing words on the card |
| R3 | **A future agent reaches for the transcript to answer Q5**, and builds on the one store with no `LoggingPolicy` gate (§2.5). | §4.3's refusal, P-2, this row, and the P1 line item that fixes it. If Q5 ever ships without those three, this brief was ignored |
| R4 | `MOXIE_SESSION_IDLE_S = 600` is a guess; a real child's rhythm may make it count one long afternoon as six conversations, or six as one. | An env var, stated on the card's footer as *"a conversation is a gap of more than 10 minutes"*, and A2 in the ledger |
| R5 | Gating `mentor_behaviors` (P-1) degrades scheduling for `NO_DATA` households — a real regression in a shipped feature. | Stated beside the toggle and in the erasure confirmation. The alternative is writing a child's activity history against an explicit "no" |
| R6 | The card looks convincing and a parent reads Q1/Q2 as wellbeing. | §4.2's sentence, T12. It is an acceptance criterion, not a caption |
| R7 | Every number comes from a simulator, and the design is tuned to simulator traffic. | §0, and §7.3's three open questions. When a real robot arrives its `moxie_session_id` reconciler is already filed as P1, not discovered |

---

## 10. Assumption ledger

| # | Assumption | State | How it gets settled |
|--:|---|:--:|---|
| A1 | The scored fields are never persisted anywhere in this tree | **proven, by reading every store write** | `grep -n "self.store.append\|self.store.write" mqtt/supervisor/moxie_runtime.py` at `ff2059a` → 17 sites over 13 collections, none carrying `scored`; `_save_memory` stores `{role, content}` only; `MemoryStore` has no scored field |
| A2 | 600 s is a defensible session boundary | **unverified — chosen, not measured** | An env var. A week of a real household; there has never been one (§0) |
| A3 | Upstream OpenMoxie has no telemetry or insights layer to port | **proven** | `grep -rniE "telemetry\|analytics\|Packet\b"` over `jbeghtol/openmoxie` `site/` at `c8c2d38`, excluding generated `protos/` → no hits |
| A4 | `mentor_behaviors` is durable, timestamped and sufficient for a week's activity mix without a roll-up | **proven** | `MENTOR_BEHAVIOR_FIELDS` includes `timestamp`; cap is 500 (`MAX_MENTOR_BEHAVIORS`); `mentor_behaviors()` sorts by it |
| A5 | Our derived session and a real robot's `moxie_session_id` will disagree | **inferred, and expected** | Only a real robot settles it. Filed as a P1 reconciler rather than papered over. The field is already in the envelope and already counted by `telemetry_signals` |
| A6 | `ingest_mentor_behavior` is not `LoggingPolicy`-gated | **proven** | `moxie_runtime.py`:3870-3880 — a bare `store.append`, no policy read on the path |
| A7 | `_save_memory` is not `LoggingPolicy`-gated and is on by default | **proven** | `:324-337` has no policy check; its two callers (`:3269`, `:3857`) have none; `docker-compose.yml`:103 sets `MOXIE_MEMORY_DIR` |
| A8 | Telemetry has no erasure route today | **proven** | `do_DELETE` accepts only `/memory` (`:987-995`); `POST /memory {"erase"}` is its twin; no other delete path exists |
| A9 | `RemoteChatInput`'s affect fields are never populated | **proven** | `build_chat_response` sets `resp["input"] = {"safety": wire}` and nothing else (`wire.py`:166) |
| A10 | A robot re-filed under `robot:` cannot collide with a name a robot would legitimately send | **inferred** | No recovered `event_name` vocabulary exists at all, so no collision can be ruled *in* either. The prefix is ours, the rename is total, and T10 pins the behaviour |
| A11 | 500 envelopes still covers "just now" once we mint ~1 packet per answer | **unverified** | `telemetry.py`:160 already says no measured rate exists. `MOXIE_TELEMETRY_MAX_PACKETS` is the lever; the 35-day roll-up is what answers "last week", so the ring being short is a degradation, not a loss |
| A12 | A parent reads the refusal sentence and does not read Q1/Q2 as wellbeing | **unverified** | A human test. T12 proves the sentence *exists*, not that it *lands* |

---

## 11. Did this brief remove the decision it owed?

The audit's row owed one decision: **where the vocabulary comes from.** §3.2 answers it — a four-name,
server-owned, closed event set minted from the turn loop into the store that already exists, with
`mentor_behaviors` kept as the activity dimension — and defends it against the option that looked
strongest, which was the scored fields.

It also answers a question the row did not know it was asking: **which charts must not be drawn.** Two
of the five things the row lists by name — a mood trend and *"what did we talk about this week"* — are
refused here with their reasons and, for the second, its conditions for reconsideration.

---

## 12. What this brief is not

It is not a wellbeing dashboard. It is not a measurement of a child. It is not a reason to score a
child's speech, and §4.3 is the sentence that says so out loud. It is a card that can honestly answer
*when*, *how much* and *what she did* — on one appliance, from four counted events and a clock we
control, with a delete button that works.

---
📖 [Backlog index](README.md) · [OpenMoxie feature audit](../openmoxie-feature-audit.md) ·
[Config & telemetry contract](../config-and-telemetry-contract.md) ·
[Production hardening](production-hardening.md) · [Expressiveness](expressiveness.md) ·
[Content authoring](content-authoring.md) · [Attribution](../../../ATTRIBUTION.md)
