# Insights that mean something — parent-console insights from telemetry

**Status:** partial — the durable `Packet` history, its privacy gates and erasure are shipped
([`mqtt/moxie_sdk/telemetry.py`](../../../mqtt/moxie_sdk/telemetry.py),
[`mqtt/supervisor/moxie_runtime/telemetry.py`](../../../mqtt/supervisor/moxie_runtime/telemetry.py),
[`server/static/js/insights.js`](../../../server/static/js/insights.js)), tested by
`sim/tests/test_telemetry*.py`, `sim/tests/test_sil_durable_telemetry.py` and
`sim/test_console_insights.mjs`. The insight layer (sessions, time-of-day, activity mix) is a proposal
(§3); nothing of it exists yet.

This is BEYOND #5 in the [OpenMoxie feature audit](../openmoxie-feature-audit.md): turn ingested telemetry
into a local parent console — sessions, activity mix, time-of-day patterns — all on-device, nothing
uploaded.

**Clean-room.** OpenMoxie (MIT) is prior art cited by path; no upstream code is copied
([`ATTRIBUTION.md`](../../../ATTRIBUTION.md)).

## 0. The ceiling

- **No physical Moxie has ever sent this server a telemetry `Packet`.** Every number a card shows would
  come from a simulator or from turns our own supervisor answered. Every cap, bucket boundary and idle
  window below is chosen, not measured.
- **Nothing on this appliance produces a `Packet` either.** `telemetry.build_packet` has no runtime
  caller; `ingest_telemetry` is reached only from the robot's `telemetry` / `analytics` / `packet*` event
  subtopics ([`moxie_runtime/turns.py`](../../../mqtt/supervisor/moxie_runtime/turns.py)), and our SIM
  publishes on none of them. The durable store is correct, tested and, on every appliance today, empty.
- **We have never scored a child.** The scored fields on the wire describe Moxie's own outgoing line
  (§2.1), and `RemoteChatInput`'s affect fields are never populated — `build_chat_response` sets
  `input` to `{"safety": …}` only ([`wire.py`](../../../mqtt/moxie_sdk/wire.py)).

## 1. What is shipped

### 1.1 The store

Two per-robot collections, written only by `_persist_telemetry`:

| Collection | Shape | Cap (env override) |
|---|---|---|
| `telemetry_packets` | ring of packet envelopes | `MAX_PACKETS = 500` (`MOXIE_TELEMETRY_MAX_PACKETS`) |
| `telemetry_daily` | one row per day: `{count, by_event, first, last}` | `MAX_ROLLUP_DAYS = 35` (`MOXIE_TELEMETRY_MAX_DAYS`); `MAX_DAY_EVENTS = 24` distinct names per day, overflow counted under `(other)` on a first-come basis |

`roll_up_packet`, `reconcile_rollup`, `history_view` and `rollup_totals` in `telemetry.py` build and read
the roll-up; the RAM buffer is hydrated from the stored ring on first touch, so a restart does not erase
the answer.

### 1.2 The `LoggingPolicy` gate

`storable_packet(pkt, policy)` is applied on the way to disk and fails closed:

| Policy | On disk |
|---|---|
| `NO_DATA` (0) | nothing — no packet, no count, no day row |
| `NO_MEDIA` (1, the effective default) | the envelope without `event_data`, plus `event_data_withheld: "NO_MEDIA"` |
| `FULL` (2) | the whole envelope, `event_data` truncated at `MAX_EVENT_DATA_CHARS = 2048` |

`event_data` is untyped bytes in the recovered proto, so under the default policy **a packet's payload
does not exist on disk**. Any meaning an insights layer needs must live in `event_name`.

Related gates, all shipped:

- `ingest_mentor_behavior` (the robot's `ActivityUpdate.mentor_behavior` reports: module, content, action,
  ended reason, timestamp) writes `mentor_behaviors` only when `telemetry_persists(device_id)`; under
  `NO_DATA` it is parsed but never stored. Tested by
  [`test_telemetry_erase_policy.py`](../../../sim/tests/test_telemetry_erase_policy.py).
- The rolling conversation transcript (`_save_memory` in
  [`moxie_runtime/memory.py`](../../../mqtt/supervisor/moxie_runtime/memory.py)) writes only when
  `transcript_persists(device_id)`, and under `NO_DATA` removes what exists. Tested by
  [`test_transcript_memory_policy.py`](../../../sim/tests/test_transcript_memory_policy.py).

### 1.3 Erasure

`erase_telemetry(device_id)` deletes `telemetry_packets`, `telemetry_daily` **and** `mentor_behaviors`,
drops the in-RAM buffer, and is **never policy-gated** — a parent can always delete. It is idempotent.
Routes: supervisor `DELETE /telemetry`, console `DELETE /local/robots/{device_id}/telemetry`
([`server/moxie_server/routes/console.py`](../../../server/moxie_server/routes/console.py)). `purge_telemetry`
erases every robot now under `NO_DATA` at startup and after config edits, except a robot whose saved
settings could not be read: it runs under `NO_DATA` without that being a parent's choice, so its
record is kept until a parent saves its settings
([production hardening §8](production-hardening.md#8-phases-and-risks)). Erasing `mentor_behaviors`
costs the schedule planner its completion history, so finished activities may be offered again.

### 1.4 The card

The console's 📈 Insights card ([`insights.js`](../../../server/static/js/insights.js), fed by
`GET /local/robots/{device_id}/telemetry` and `fleet.normalize_telemetry`) shows a zero-filled week
(`weekBars`), per-event counts, the newest envelopes, the real retention window and lifetime totals, and
a two-click erase. Under `NO_DATA` it says nothing is being saved and that the list shows only what
arrived since the supervisor started — it never draws an empty week as if it were a quiet one. When
the store still holds history (a lifetime total above zero, as for a robot whose saved settings could
not be read), it says instead that nothing new is saved and the history stored before is kept until
it is erased (`noDataNote`). The
connection strip (from [`conn_telemetry.py`](../../../mqtt/moxie_sdk/conn_telemetry.py)) is shown under
every policy, because it carries only topics, reason codes and durations — nothing about the child.

Tests: [`test_telemetry.py`](../../../sim/tests/test_telemetry.py),
[`test_telemetry_runtime.py`](../../../sim/tests/test_telemetry_runtime.py),
[`test_sil_durable_telemetry.py`](../../../sim/tests/test_sil_durable_telemetry.py),
[`test_telemetry_rollup_repair.py`](../../../sim/tests/test_telemetry_rollup_repair.py),
[`test_telemetry_erase_policy.py`](../../../sim/tests/test_telemetry_erase_policy.py),
[`test_console_roundtrip.py`](../../../sim/tests/test_console_roundtrip.py),
[`sim/test_console_insights.mjs`](../../../sim/test_console_insights.mjs) (real browser, with mutation
teeth), and [`sim/tools/telemetry_rollup_mutation_check.py`](../../../sim/tools/telemetry_rollup_mutation_check.py).

## 2. Why the insight half is blocked on vocabulary

The only thing the store can group by is `Packet.event_name`, a free string for which our corpus recovers
**no** vocabulary (`schedule.telemetry_signals` returns `carries_module_signal: False` for this reason). A
parent-facing chart over free strings is a chart of noise. Three candidate sources:

| | (a) `mentor_behaviors` | (b) events we mint ourselves | (c) the scored fields |
|---|---|---|---|
| Recovered? | yes — the robot's own report; but `action` / `ended_reason` spellings are not recovered and are kept verbatim | invented, but closed and server-owned | yes — mood, dialog act, emotion, signal catalogs |
| Persisted today? | yes (policy-gated) | no | **no** — computed in `_stage`, put on the wire, dropped |
| Covers free conversation? | no — silent unless a content module runs | yes | yes |
| Describes the child? | what she did | when and how much she used it | **no** — it scores Moxie's own line (§2.1) |

### 2.1 The scored fields describe Moxie, not the child

`_stage` scores **Moxie's outgoing line**: `dialog_act` comes from `performance.classify`, a rule cascade
over that line ([`expressiveness.md`](expressiveness.md) §2). A "mood trend" drawn from them would be our
rule engine's opinion of our language model's phrasing, labelled with a child's name. That chart does not
ship.

**Choice: (b), with (a) kept as the activity dimension.** A small, closed, server-owned event vocabulary
minted from the turn loop into the store that already exists.

## 3. Proposal

### 3.1 Parent questions

| # | Question | Answer |
|--:|---|---|
| Q1 | When does she use it? | yes — turns bucketed by time of day |
| Q2 | How much — every day, or one Saturday? | yes — the existing week, plus conversation and turn counts |
| Q3 | What did she do? | partly — `mentor_behaviors`, scheduled activities only; an explicit empty state otherwise |
| Q4 | Is she enjoying it? | **refused** — no child-side affect signal exists anywhere in the system |
| Q5 | What did we talk about this week? | **refused for now** — needs topic inference over the child's words; reconsider only with its own explicit consent surface separate from `LoggingPolicy` |

The card must carry the sentence *"This card does not measure how your child feels."* so Q1/Q2 are not
read as a wellbeing signal. Every number is **per robot**, never a child's name (the appliance has one
`ChildProfile` from an environment variable).

### 3.2 Four reserved events

| `event_name` | Minted at | Answers |
|---|---|---|
| `moxie.robot.connect` | `_device_connect` | was the robot on at all |
| `moxie.session.start` | the first turn after `MOXIE_SESSION_IDLE_S` (proposed 600 s) of silence | Q2 |
| `moxie.session.end` | `_end_conversation`, or lazily at the next turn once the idle window passed — stamped with the time it describes | Q2 |
| `moxie.turn` | once per published **answer** (not per streamed chunk), deduped on `event_id` | Q1, Q2 |

Rules:

1. **Closed:** `RESERVED_EVENTS` frozen as a literal in a test.
2. **No payload:** minted with empty `event_data`, so rows are identical under `NO_MEDIA` and `FULL`;
   every quantity is a count or a difference of `recorded_at` stamps.
3. **Through the gate:** minted events go through `_persist_telemetry`, so `NO_DATA` writes nothing.
4. **Aggregates from disk only:** every number comes from `telemetry_daily` or `mentor_behaviors`, never
   from the RAM buffer (which, under `NO_DATA`, holds packets the disk refused).
5. **Unforgeable:** a robot packet named `moxie.*` is re-filed as `robot:<name>` on ingest, and reserved
   names never overflow into `(other)` (today's cap is first-come).
6. **No new inference** over a child's words: no classifier, no model call.

No activity events are minted: `mentor_behaviors` already holds that dimension, and minting would mean
inventing enums for spellings we have not recovered.

### 3.3 Day-row change

`roll_up_packet` gains `bucket_events=(moxie.turn,)` and adds one key per day:
`"buckets": {"morning", "afternoon", "evening", "night"}`, computed with the existing
`schedule.time_bucket`. Only turns are bucketed, so robot heartbeats cannot move "when does she use it".
`_clean_rollup` reads an old row without the key as zeros.

### 3.4 Tests to write

Pure (`test_telemetry.py`): buckets count only the named events; a reserved name never overflows into
`(other)` (red today); minted rows identical under `NO_MEDIA`/`FULL`; the vocabulary is exactly four names;
old rows read as zeros. Runtime (new `test_insights.py`): a streamed answer mints one turn and a redelivered
`event_id` none; `NO_DATA` mints nothing; no aggregate from the RAM buffer; sessions survive a restart;
robots cannot forge reserved events; an appliance with no content module still answers Q1/Q2. New
clock-reading tests register in [`test_clock_dependence.py`](../../../sim/tests/test_clock_dependence.py);
a new test file must be wired into `sim/ci/ci.yml` and `.github/workflows/ci.yml` in the same commit
([`test_ci_test_coverage.py`](../../../sim/tests/test_ci_test_coverage.py)).

### 3.5 Never

No upload, export to a third party, cross-household comparison, retention beyond the caps shown on the
card, or number that survives an erase.

### 3.6 Open questions

- Is 600 s the right session boundary? Only a real household's week can say.
- Does ~1 packet per answer overrun the 500-envelope ring? The daily roll-up answers "last week", so a
  short ring degrades "just now" only.
- Our derived sessions will disagree with a real robot's `moxie_session_id`; a reconciler is follow-up
  work once a robot sends packets.
- Later phases: per-day active minutes, activity mix by category (`schedule.module_label`), a CSV export
  a parent keeps, and a per-child split (blocked on multi-child identity).

## 4. Prior art

- **OpenMoxie** has no telemetry or insights layer. Its `MentorBehavior` model (`site/hive/models.py`)
  carries the same seven fields as our `MENTOR_BEHAVIOR_FIELDS`, ordered newest first.
- **Fork A** (`Noonster77/openmoxie`) `site/hive/mqtt/conversation_log.py` is a transcript logger, not
  insights. Two ideas are credited: an 8-second dedup window (a report can arrive twice — the model for
  `moxie.turn`'s `event_id` dedup) and rewriting/unlinking a day's file after a parent deletes (deletion
  that actually deletes).

---
📖 [Backlog index](README.md) · [OpenMoxie feature audit](../openmoxie-feature-audit.md) ·
[Config & telemetry contract](../config-and-telemetry-contract.md) ·
[Production hardening](production-hardening.md) · [Expressiveness](expressiveness.md) ·
[Content authoring](content-authoring.md) · [Attribution](../../../ATTRIBUTION.md)
