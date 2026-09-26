"""Schedule builder — the day plan a Moxie pulls at the start of every session.

The robot will not enter a session without a schedule; answer with nothing and none of
its on-board activities run (`docs/architecture/openmoxie-feature-audit.md` §4.1 row 1).
This module builds the *content*; `wire.py::build_activity_response` encodes it and the
supervisor's `_on_activity` publishes it.

### The shape (recovered protos)

`CloudQueryResponse.schedule` (field 6) is an `embodied.robotbrain.ContentSchedule`
(`recovered-proto/embodied/robotbrain/ContentSchedule.proto`; `proto-catalog.md`:466):

    restricted_modules=1 (ContentModule[])   tags=2 (TagList)
    provided_schedule=3  (Recommendation[])  config=4 (ScheduleConfig)
    end_of_session=5     (EndOfSessionConfig) chat_request=7 (Recommendation)
    wake_module=8        (Recommendation)    rewards=9 (RewardsConfig)
    mission_config=10    (MissionConfig)     hub_config=11 (HubConfig)
    alarm_module=12      (Recommendation)

A `Recommendation` (`RemoteChat.proto`:26-34) is `{module_id, content_id, entry_line,
module_name, module_description, seen, skip_hub}`. `provided_schedule`, in order, *is*
the day plan.

### What it does

Deterministic, given (template, history, config, clock): start from a **template** (a
content module's `schedules[]` entry or `DEFAULT_TEMPLATE`); drop onboarding (FTUE)
modules the child has finished; fill the rest from the **on-board catalog** with a
*scored* recommender; interleave chats; emit **only** `ContentSchedule` fields (authoring
keys like `generate` are stripped), plus parallel **explanations** that never hit the wire.

### The recommender (audit §4.2 BEYOND #7)

`plan_inputs()` gathers signals, `plan_day()` scores and orders them.

**Constraints**: **bedtime** — no slot is planned inside the robot's bedtime window (the
day is truncated; the spine still goes out). **Category variety** — a candidate matching
the previous pick's `ModuleCategory` is skipped unless nothing else is left.

**Weights** (summed; each band dominates the one below, each factor testable alone):

| factor | weight | signal |
|---|---|---|
| parent request | `W_PARENT_REQUEST` 4000 | `SchedulePreferences.parent_requests[]` due today, pinned to the slot nearest `scheduled_at` and held out of earlier slots |
| FTUE still running | `W_FTUE` 2000 | an unfinished onboarding module |
| coverage / repeat | `-W_TIER` 1000 × times seen | nothing repeats until the catalog is exhausted |
| recency | `RECENCY_SAME_DAY` -300 / `RECENCY_3_DAY` -100 | do not re-offer yesterday's activity |
| completion affinity | `AFFINITY_FLOOR` 10 … `AFFINITY_MAX` 200 | COMPLETED ÷ (COMPLETED + QUIT/REFUSED); demoted to the floor, never zero |
| time-of-day fit | `TIME_FIT` -60 … +120 | slot time vs. category energy |
| category spread | `-CATEGORY_REPEAT_PENALTY` 90 × prior uses | prefer a fresh category |
| tiebreak | 0…31 | `blake2b(device_id|day|module_id)` — stable per day |

**Time of day.** Buckets: morning 05-12, afternoon 12-17, evening 17-21, night 21-05.
Energy is assigned per recovered `ModuleCategory` (`ContentModule.proto`:46-60), not per
module: MOVEMENT/PLAYFUL_GAME energetic; CREATIVITY/FUN_TIDBIT/PUZZLE_GAME neutral;
REGULATION/LISTENING/READING calm.

**Telemetry.** `Packet.event_name` is a free string and `event_data` opaque bytes; no
module-scoped event vocabulary is recovered, so completion comes from `mentor_behaviors`
alone. Telemetry only contributes context (counts, an activity-time histogram), and
`inputs["telemetry"]["carries_module_signal"]` is False.

Not here: an LLM-planned day (a later BEYOND item); this stays a pure function.

*Credit:* the problem's shape — a `generate` block, FTUE pruning, chats between
activities, no same-category back-to-back — is OpenMoxie's (MIT;
`site/hive/mqtt/scheduler.py`). This implementation is ours and deterministic where
theirs samples at random. See `ATTRIBUTION.md`.

Layout: `catalog` (wire field names, the on-board catalog, every weight and table),
`normalize` (template/schedule field filtering + validation), `signals` (clock helpers
and the history/bedtime/request/telemetry inputs), `planner` (scoring, explanations,
`plan_day`/`plan`/`build_schedule`). Import from `moxie_sdk.schedule` only.
"""
from __future__ import annotations

from .catalog import (ABANDONED, AFFINITY_FLOOR, AFFINITY_MAX, AFFINITY_NEUTRAL,
    CATEGORY_ENERGY, CATEGORY_REPEAT_PENALTY, COMPLETED, DEFAULT_CHILD_NAME,
    DEFAULT_ENERGY, DEFAULT_TEMPLATE, FTUE_COMPLETION_COUNTS, MAX_TIER, MODULE_LABELS,
    ONBOARD_MODULES, RECENCY_3_DAY, RECENCY_SAME_DAY, RECENCY_WINDOW_DAYS,
    RECOMMENDATION_FIELDS, SCHEDULE_FIELDS, SLOT_MINUTES, TIEBREAK_RANGE, TIME_BUCKETS,
    TIME_FIT, W_FTUE, W_PARENT_REQUEST, W_TIER)  # noqa: F401
from .normalize import validate_schedule  # noqa: F401
from .signals import (bedtime_window, category_energy, completed_counts, ftue_skips,
    in_bedtime, module_history, module_label, parent_requests_due, plan_inputs,
    select_template, telemetry_signals, time_bucket)  # noqa: F401
from .planner import (build_schedule, clock_label, explain, plan, plan_day,
    schedule_template, score_module)  # noqa: F401
