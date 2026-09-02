"""
Schedule builder — the day plan a Moxie pulls at the start of every session.

Why it matters: the robot asks the cloud for a schedule before it will enter a session
at all. Answer with nothing and none of its on-board activities ever run
(`docs/architecture/openmoxie-feature-audit.md` §4.1 row 1). This module builds the
*content* of that answer; `wire.py::build_activity_response` puts it on the wire and
`supervisor/moxie_runtime.py::_on_activity` publishes it.

### The shape (recovered protos, not guessed)

`CloudQueryResponse.schedule` is field **6**, an `embodied.robotbrain.ContentSchedule`
(`docs/reverse-engineering/protocol/recovered-proto/embodied/logging/Cloud.proto`:343;
catalogued in `proto-catalog.md`:466). `ContentSchedule` itself is
`recovered-proto/embodied/robotbrain/ContentSchedule.proto`:

    restricted_modules=1 (ContentModule[])   tags=2 (TagList)
    provided_schedule=3  (Recommendation[])  config=4 (ScheduleConfig)
    end_of_session=5     (EndOfSessionConfig) chat_request=7 (Recommendation)
    wake_module=8        (Recommendation)    rewards=9 (RewardsConfig)
    mission_config=10    (MissionConfig)     hub_config=11 (HubConfig)
    alarm_module=12      (Recommendation)

and a `Recommendation` (`recovered-proto/embodied/robotbrain/RemoteChat.proto`:26-34,
`RecommendationContext.Recommendation`) is
`{module_id, content_id, entry_line, module_name, module_description, seen, skip_hub}`.

`provided_schedule` is the ordered list of activities for the session — that ordered
list *is* the day plan.

### What it does

Deterministic, given (template, history, config, clock):

1. start from a **template** — a content module's `schedules[]` entry
   (`docs/architecture/content-module-contract.md` §`schedules[]`) or `DEFAULT_TEMPLATE`;
2. drop first-time-user modules the child has already finished, so **FTUE ends**;
3. fill the rest of the day from the **on-board activity catalog** with a *scored*
   recommender (below) rather than a blind rotation;
4. interleave chat activities between them;
5. emit **only** `ContentSchedule` fields (the `generate` block and any other authoring
   key is stripped — it is server-side, not wire), plus a parallel list of
   **explanations** that never touches the wire.

### The recommender (audit §4.2 BEYOND #7)

`plan_inputs()` gathers the signals, `plan_day()` scores and orders them. Two of the
inputs are *constraints* and the rest are *weights*:

**Constraints** (applied before scoring)

* **bedtime** — a slot whose clock time falls inside the robot's configured bedtime
  window (`RobotCloudConfig.weekday_bedtime_*` / `weekend_bedtime_*`, see
  `cloud_config.build_robot_cloud_config`) is never planned into. The day is truncated
  there; the pinned spine still goes out so the robot always has something to run.
* **category variety** — a candidate whose `ModuleCategory` matches the previous pick is
  filtered out unless nothing else is left. Same semantics as the pre-recommender
  rotation (and as OpenMoxie's `ransac_select` goal), just enforced rather than sampled.

**Weights** (summed; the numbers are the constants below, chosen so each band dominates
the one under it and every factor is separately testable)

| factor | weight | signal |
|---|---|---|
| parent request | `W_PARENT_REQUEST` 4000 | `SchedulePreferences.parent_requests[]` due today, pinned to the slot nearest its `scheduled_at` |
| FTUE still running | `W_FTUE` 2000 | an onboarding module the child has not finished |
| coverage / repeat | `-W_TIER` 1000 × times seen | the "nothing repeats until the catalog is exhausted" invariant, now a weight |
| recency | `RECENCY_SAME_DAY` -300 / `RECENCY_3_DAY` -100 | do not re-offer yesterday's activity |
| completion affinity | `AFFINITY_FLOOR` 10 … `AFFINITY_MAX` 200 | `COMPLETED` ÷ (`COMPLETED` + `QUIT`/`REFUSED`). A repeatedly-abandoned module is demoted **to the floor, never to zero** — variety matters more than a losing streak |
| time-of-day fit | `TIME_FIT` -60 … +120 | the slot's clock time vs. the module's category energy (table below) |
| category spread | `-CATEGORY_REPEAT_PENALTY` 90 × prior uses | a second/third activity from a category already used today is cheaper than a fresh one |
| tiebreak | 0…31 | `blake2b(device_id|day|module_id)` — stable for a whole day, different tomorrow |

**Time-of-day mapping.** Buckets are `morning` 05:00-11:59, `afternoon` 12:00-16:59,
`evening` 17:00-20:59, `night` 21:00-04:59. Each catalog category is classified by
*energy* — the mapping is derived from the recovered `ModuleDetail.ModuleCategory` enum
(`recovered-proto/embodied/robotbrain/ContentModule.proto`:46-60 · `proto-catalog.md`:1675),
not invented per module: `MOVEMENT`/`PLAYFUL_GAME` = energetic, `CREATIVITY`/`FUN_TIDBIT`/
`PUZZLE_GAME` = neutral, `REGULATION`/`LISTENING`/`READING` = calm. Energetic scores
highest in the morning, calm highest in the evening and at night. See `TIME_FIT`.

**What telemetry actually contributes.** The recovered telemetry envelope is
`embodied.logging.Packet{model, version, recorded_at, moxie_id, moxie_session_id,
user_id, event_name, event_data}` (`device-config-and-telemetry.md` §"The telemetry
envelope"). `event_name` is a **free string** and `event_data` is opaque serialized
bytes — our RE corpus recovers **no module-scoped event vocabulary**, so there is no
"module launched" / "module exited" event to count. Completion-vs-abandonment therefore
comes from `mentor_behaviors` alone (`MentorAction.COMPLETED` vs `QUIT`/`REFUSED`).
Telemetry contributes only what the envelope really carries: a packet count and a
`recorded_at` histogram of when this robot is active, reported in the inputs summary as
context for the parent. `inputs["telemetry"]["carries_module_signal"]` says so out loud.

Not here (deliberately): an LLM-planned day. That is a later BEYOND item
(`openmoxie-feature-audit.md` §4.2 row 7); this stays a pure function.

*Credit:* the shape of the problem — a `generate` block, FTUE pruning, chats distributed
between activities, avoiding two same-category activities back to back — is OpenMoxie's
(MIT; `site/hive/mqtt/scheduler.py`, `expand_schedule`/`ftue_remove`/`ransac_select`/
`distribute_elements`). The idea is theirs; this implementation is ours and deterministic
where theirs samples at random. See `ATTRIBUTION.md`.
"""
from __future__ import annotations

import datetime
import hashlib

# --- ContentSchedule / Recommendation field names (see the module docstring) ---
SCHEDULE_FIELDS = ("restricted_modules", "tags", "provided_schedule", "config",
                   "end_of_session", "chat_request", "wake_module", "rewards",
                   "mission_config", "hub_config", "alarm_module")

RECOMMENDATION_FIELDS = ("module_id", "content_id", "entry_line", "module_name",
                         "module_description", "seen", "skip_hub")

# Which ContentSchedule fields hold a Recommendation (normalized on the way out).
_RECOMMENDATION_FIELDS_IN_SCHEDULE = ("chat_request", "wake_module", "alarm_module")

# The on-board activity catalog — modules baked into the robot's firmware that the cloud
# can only *schedule* by id. Transcribed from our own protocol notes,
# `docs/architecture/mqtt-and-conversation.md`:526 ("the ~23 in `content/data.py`
# `RECOMMENDABLE_MODULES`"), with the categories used there. `DM` (Daily Missions) is
# listed separately in that same note and is carried in DEFAULT_TEMPLATE, not the
# rotation, because it is a daily fixture rather than a variety pick.
ONBOARD_MODULES = (
    {"module_id": "AFFIRM", "category": "REGULATION"},
    {"module_id": "AB", "category": "REGULATION"},
    {"module_id": "ANIMALEXERCISE", "category": "MOVEMENT"},
    {"module_id": "BODYSCAN", "category": "REGULATION"},
    {"module_id": "RDL", "category": "FUN_TIDBIT"},
    {"module_id": "BREATHINGSHAPES", "category": "REGULATION"},
    {"module_id": "COMPOSING", "category": "CREATIVITY"},
    {"module_id": "FACES", "category": "PLAYFUL_GAME"},
    {"module_id": "FF", "category": "FUN_TIDBIT"},
    {"module_id": "GUIDEDVIS", "category": "REGULATION"},
    {"module_id": "JOKE", "category": "FUN_TIDBIT"},
    {"module_id": "JUKEBOX", "category": "LISTENING"},
    {"module_id": "MENTORSAYS", "category": "PLAYFUL_GAME"},
    {"module_id": "NONSENSE", "category": "FUN_TIDBIT"},
    {"module_id": "DANCE", "category": "MOVEMENT"},
    {"module_id": "DRAW", "category": "CREATIVITY"},
    {"module_id": "STORYTELLING", "category": "CREATIVITY"},
    {"module_id": "PASSWORDGAME", "category": "PUZZLE_GAME"},
    {"module_id": "READ", "category": "READING"},
    {"module_id": "SCAVENGERHUNT", "category": "PLAYFUL_GAME"},
    {"module_id": "STORY", "category": "LISTENING"},
    {"module_id": "AUDMED", "category": "REGULATION"},
    {"module_id": "WHIMSY", "category": "FUN_TIDBIT"},
)

# First-time-user experience: the onboarding modules, and how many COMPLETED reports mean
# "done". WELCOME goes away as soon as the child completes anything.
#
# HONEST NOTE: our RE docs name the FTUE modules (`openmoxie-feature-audit.md` §1.4) but
# do **not** establish the per-module content-id counts. The robot walks TNT/SYSTEMSCHECK
# content ids in order and then starts repeating them at random, so the cloud has to stop
# scheduling them itself. The thresholds below are OpenMoxie's field-proven constants
# (`site/hive/content/data.py`: `TNT_CIDS = 9`, `SYSTEMSCHECK_CIDS = 4`) — adopted because
# a field-proven number beats a guess, and flagged here because it is not ours.
FTUE_COMPLETION_COUNTS = {"WELCOME": 1, "TNT": 9, "SYSTEMSCHECK": 4}

# The action that means "the child actually finished this"
# (`embodied.robotbrain.MentorAction.COMPLETED`, MentorBehavior.proto:8).
COMPLETED = "COMPLETED"

# The actions that mean "the child bailed out" — `MentorAction` again (MentorBehavior.proto
# :8, enum QUIT/REFUSED). Everything else (PRESENTED / SCHEDULED / SUGGESTED / REQUESTED /
# UNKNOWN) is "offered", which counts for coverage but not for affinity either way.
ABANDONED = ("QUIT", "REFUSED")


# ---------------------------------------------------------------- the recommender ----
# Every number the planner uses lives here, in one place, so a factor can be isolated in a
# test by zeroing its neighbours. See the module docstring's table for what each is for.

W_PARENT_REQUEST = 4000        # a parent asked for this, today
W_FTUE = 2000                  # onboarding that is not finished yet
W_TIER = 1000                  # × times this robot has already seen the module
MAX_TIER = 5                   # beyond 5 airings, "seen a lot" is one bucket
RECENCY_SAME_DAY = -300        # offered within the last 24 h
RECENCY_3_DAY = -100           # offered within the last 3 days
RECENCY_WINDOW_DAYS = 3
AFFINITY_FLOOR = 10            # a module the child always quits — demoted, never zeroed
AFFINITY_MAX = 200             # a module the child always finishes
AFFINITY_NEUTRAL = 100         # no history either way
CATEGORY_REPEAT_PENALTY = 90   # × times this category is already in today's plan
TIEBREAK_RANGE = 32            # < the smallest real factor step, so it only breaks ties

# How long one activity notionally occupies. `CSData.module_started_ts` exists precisely so
# the robot can time-box an activity (`offline-and-brain-state.md`:78 "for time-in-activity
# limits") but our corpus does not recover the limit itself, so this is OURS: a round ten
# minutes, used to give each slot in the plan a clock time (for time-of-day fit, bedtime
# truncation, and landing a parent request at the hour they asked for).
SLOT_MINUTES = 10

# Time-of-day buckets (local wall clock). `night` wraps midnight.
TIME_BUCKETS = (("morning", 5, 12), ("afternoon", 12, 17), ("evening", 17, 21),
                ("night", 21, 5))

# `ModuleDetail.ModuleCategory` (ContentModule.proto:46-60 · proto-catalog.md:1675) → the
# energy a category asks of a child. This is the only judgement call in the mapping and it
# is made per *category*, not per module, so it stays as small and auditable as the enum.
CATEGORY_ENERGY = {
    "MOVEMENT": "energetic", "PLAYFUL_GAME": "energetic",
    "CREATIVITY": "neutral", "FUN_TIDBIT": "neutral", "PUZZLE_GAME": "neutral",
    "MISSION": "neutral", "CONVERSATION": "neutral",
    "REGULATION": "calm", "LISTENING": "calm", "READING": "calm",
}
DEFAULT_ENERGY = "neutral"     # UNASSIGNED / UTILITY / OTHER / an authored `USER` category

# Energetic early, calm late. Nothing is ever forbidden by time of day — a child who wants
# to dance at 8 pm still can, it is just no longer the top pick.
TIME_FIT = {
    "morning":   {"energetic": 120, "neutral": 60,  "calm": 0},
    "afternoon": {"energetic": 60,  "neutral": 120, "calm": 60},
    "evening":   {"energetic": 0,   "neutral": 60,  "calm": 120},
    "night":     {"energetic": -60, "neutral": 0,   "calm": 120},
}

# Plain-English labels for the explanation lines. Only ids that are unambiguously an
# English word or phrase are mapped; anything else keeps its id verbatim rather than have
# us invent an Embodied product name. A `Recommendation.module_name` on the template
# always wins over this table.
MODULE_LABELS = {
    "AFFIRM": "Affirmations", "ANIMALEXERCISE": "Animal exercise",
    "AUDMED": "Guided meditation", "BODYSCAN": "Body scan",
    "BREATHINGSHAPES": "Breathing shapes", "COMPOSING": "Composing",
    "DANCE": "Dance", "DM": "Daily Missions", "DRAW": "Drawing",
    "FACES": "Faces", "FREE_CHAT": "Free chat", "GUIDEDVIS": "Guided visualization",
    "JOKE": "Jokes", "JUKEBOX": "Jukebox", "MENTORSAYS": "Mentor Says",
    "NONSENSE": "Nonsense", "PASSWORDGAME": "Password game", "READ": "Reading",
    "SCAVENGERHUNT": "Scavenger hunt", "STORY": "Story", "STORYTELLING": "Storytelling",
    "SYSTEMSCHECK": "Systems Check", "WELCOME": "Welcome", "WHIMSY": "Whimsy",
}

# The default when the caller does not tell us the child's name (the planner is pure and
# has no access to the ChildProfile).
DEFAULT_CHILD_NAME = "Your child"

# Our default day: onboarding first, then Daily Missions, then a generated rotation.
# `generate` is an authoring key (content-module-contract.md §schedules[]), consumed and
# stripped here — it never goes on the wire.
DEFAULT_TEMPLATE = {
    "provided_schedule": [
        {"module_id": "WELCOME"},
        {"module_id": "TNT"},
        {"module_id": "SYSTEMSCHECK"},
        {"module_id": "DM"},
    ],
    "generate": {
        "chat_count": 2,
        "module_count": 6,
        "chat_modules": [{"module_id": "FREE_CHAT", "content_id": "default"}],
        "extra_modules": [],
        "excluded_module_ids": [],
    },
    "chat_request": {"module_id": "FREE_CHAT", "content_id": "default"},
}


# ---------------------------------------------------------------- mentor behaviors ----

def completed_counts(mentor_behaviors) -> dict:
    """`{module_id: COMPLETED count}` from a robot's stored MentorBehavior records.

    Records with any other `action` (PRESENTED / QUIT / REFUSED / …, MentorBehavior.proto
    `MentorAction`) are counted as *offered*, not done, so a refused activity can come
    back around."""
    counts: dict = {}
    for mbh in mentor_behaviors or ():
        if not isinstance(mbh, dict):
            continue
        if str(mbh.get("action", "")).upper() != COMPLETED:
            continue
        mid = mbh.get("module_id")
        if mid:
            counts[mid] = counts.get(mid, 0) + 1
    return counts


def ftue_skips(counts: dict) -> set:
    """Which onboarding modules this robot is done with (drop them from the day)."""
    skips = {m for m, need in FTUE_COMPLETION_COUNTS.items()
             if m != "WELCOME" and counts.get(m, 0) >= need}
    if skips or any(v > 0 for v in counts.values()):
        skips.add("WELCOME")            # anything completed at all retires the welcome
    return skips


# --------------------------------------------------------------------- normalizing ----

def _recommendation(item) -> dict | None:
    """Keep only `Recommendation` fields (RemoteChat.proto:26-34).

    Authoring templates carry extra keys — OpenMoxie's generated entries even ship a
    `category` on the wire — and unknown keys are at best ignored by a protobuf JSON
    parser. We strip instead of hoping."""
    if not isinstance(item, dict):
        return None
    rec = {k: item[k] for k in RECOMMENDATION_FIELDS if k in item and item[k] not in (None, "")}
    return rec or None


def _recommendation_list(items) -> list:
    return [r for r in (_recommendation(i) for i in (items or ())) if r]


def _normalize_schedule(sched: dict) -> dict:
    """Emit only ContentSchedule fields, with every Recommendation-typed value cleaned."""
    out = {k: v for k, v in sched.items() if k in SCHEDULE_FIELDS}
    out["provided_schedule"] = _recommendation_list(out.get("provided_schedule"))
    for key in _RECOMMENDATION_FIELDS_IN_SCHEDULE:
        if key in out:
            rec = _recommendation(out[key])
            if rec:
                out[key] = rec
            else:
                del out[key]
    hub = out.get("hub_config")
    if isinstance(hub, dict):                    # ContentSchedule.HubConfig{hubs, skipped_modules}
        cleaned = {"hubs": _recommendation_list(hub.get("hubs"))}
        if hub.get("skipped_modules"):
            cleaned["skipped_modules"] = [str(m) for m in hub["skipped_modules"]]
        out["hub_config"] = cleaned
    cfg = out.get("config")
    if isinstance(cfg, dict):                    # ScheduleConfig{day_one_schedule, promoted_content, …}
        cfg = dict(cfg)
        for key in ("day_one_schedule", "promoted_content"):
            if key in cfg:
                cfg[key] = _recommendation_list(cfg[key])
        out["config"] = cfg
    eos = out.get("end_of_session")
    if isinstance(eos, dict):                    # EndOfSessionConfig{chat_module, end_module, chat_count}
        eos = dict(eos)
        for key in ("chat_module", "end_module"):
            rec = _recommendation(eos.get(key))
            if rec:
                eos[key] = rec
            else:
                eos.pop(key, None)
        out["end_of_session"] = eos
    return out


def validate_schedule(sched) -> list:
    """Return a list of problems with a built schedule ("" = valid). Used by tests and
    callable by an author-facing tool; checks only what the recovered protos establish."""
    problems = []
    if not isinstance(sched, dict):
        return ["schedule is not an object"]
    for key in sched:
        if key not in SCHEDULE_FIELDS:
            problems.append(f"unknown ContentSchedule field {key!r}")
    plan = sched.get("provided_schedule")
    if not isinstance(plan, list) or not plan:
        problems.append("provided_schedule is empty — the robot has nothing to run")
        return problems
    for i, rec in enumerate(plan):
        if not isinstance(rec, dict):
            problems.append(f"provided_schedule[{i}] is not a Recommendation object")
            continue
        if not rec.get("module_id"):
            problems.append(f"provided_schedule[{i}] has no module_id")
        for key in rec:
            if key not in RECOMMENDATION_FIELDS:
                problems.append(f"provided_schedule[{i}] has unknown field {key!r}")
    return problems


# ------------------------------------------------------------------------ the plan ----

def _iso(dt) -> str:
    return dt.replace(microsecond=0).isoformat()


def _parse_iso(value):
    if isinstance(value, datetime.datetime):
        return value
    return datetime.datetime.fromisoformat(str(value))


def time_bucket(dt) -> str:
    """Which part of the day a clock time falls in (`TIME_BUCKETS`)."""
    hour = dt.hour
    for name, start, end in TIME_BUCKETS:
        if start <= end:
            if start <= hour < end:
                return name
        elif hour >= start or hour < end:
            return name
    return "night"


def category_energy(category) -> str:
    """`ModuleCategory` → energy class (`CATEGORY_ENERGY`, `DEFAULT_ENERGY`)."""
    return CATEGORY_ENERGY.get(str(category or "").upper(), DEFAULT_ENERGY)


def module_label(rec) -> str:
    """A parent-readable name for one entry. `Recommendation.module_name` (a real proto
    field, RemoteChat.proto:26-34) wins; then `MODULE_LABELS`; then the id verbatim."""
    if isinstance(rec, dict):
        if rec.get("module_name"):
            return str(rec["module_name"])
        mid = str(rec.get("module_id") or "")
    else:
        mid = str(rec or "")
    return MODULE_LABELS.get(mid, mid)


def _tiebreak(device_id: str, day: str, module_id: str) -> int:
    """A stable 0…`TIEBREAK_RANGE`-1 jitter. `blake2b` (not `hash()`) so the same day
    plans identically under any `PYTHONHASHSEED`, in any process, on any machine."""
    digest = hashlib.blake2b(f"{device_id}|{day}|{module_id}".encode(),
                             digest_size=8).digest()
    return int.from_bytes(digest, "big") % TIEBREAK_RANGE


# ------------------------------------------------------------------- input signals ----

def module_history(mentor_behaviors) -> dict:
    """`{module_id: {seen, completed, abandoned, last_ts, last_action}}` from a robot's
    stored MentorBehavior records — the one signal our RE corpus really carries about
    what a child finishes vs. walks out of (MentorBehavior.proto `MentorAction`).

    `last_ts` is whatever the robot stamped (`MentorBehavior.timestamp`); device clocks
    lie, so the planner only ever asks "how many days ago" and tolerates None.
    """
    out: dict = {}
    for mbh in mentor_behaviors or ():
        if not isinstance(mbh, dict):
            continue
        mid = mbh.get("module_id")
        if not mid:
            continue
        action = str(mbh.get("action", "")).upper()
        rec = out.setdefault(mid, {"seen": 0, "completed": 0, "abandoned": 0,
                                   "last_ts": None, "last_action": ""})
        rec["seen"] += 1
        if action == COMPLETED:
            rec["completed"] += 1
        elif action in ABANDONED:
            rec["abandoned"] += 1
        ts = mbh.get("timestamp")
        if isinstance(ts, (int, float)) and not isinstance(ts, bool):
            if rec["last_ts"] is None or ts > rec["last_ts"]:
                rec["last_ts"] = ts
                rec["last_action"] = action
    return out


def _age_days(last_ts, now) -> float | None:
    """How long ago `MentorBehavior.timestamp` was, in days. The field is a `uint64` with
    no stated unit in the recovered proto, and both our runtime tests and OpenMoxie's
    robots stamp it in **milliseconds**, so a value that is plainly milliseconds is
    divided down (same rule as `cloud_config._scheduled_at`). None when unstamped."""
    if last_ts is None:
        return None
    try:
        seconds = float(last_ts)
    except (TypeError, ValueError):
        return None
    if seconds >= 1e11:                       # milliseconds, not seconds
        seconds /= 1000.0
    return (now.timestamp() - seconds) / 86400.0


def bedtime_window(effective_config, now) -> dict:
    """The bedtime this robot is under right now, from the effective config overrides
    (`weekday_bedtime` / `weekend_bedtime` = `["HH:MM","HH:MM"]`, the parent-facing
    spelling `cloud_config.sanitize_config_overrides` produces; the builder turns them
    into `RobotCloudConfig.{weekday,weekend}_bedtime_starts_at/ends_at`).

    Mon-Fri uses `weekday_bedtime`, Sat/Sun `weekend_bedtime`. Returns
    `{"enabled": False}` when the parent has not set one.
    """
    cfg = effective_config if isinstance(effective_config, dict) else {}
    kind = "weekday" if now.weekday() < 5 else "weekend"
    value = cfg.get(f"{kind}_bedtime")
    if not value:                             # fall back to the wire spelling if present
        starts = cfg.get(f"{kind}_bedtime_starts_at")
        ends = cfg.get(f"{kind}_bedtime_ends_at")
        value = [starts, ends] if starts and ends else None
    if not (isinstance(value, (list, tuple)) and len(value) == 2 and all(value)):
        return {"enabled": False, "kind": kind}
    return {"enabled": True, "kind": kind,
            "starts_at": str(value[0]), "ends_at": str(value[1])}


def _hhmm(value) -> int:
    """"HH:MM" → minutes past midnight."""
    h, _, m = str(value).partition(":")
    return int(h) * 60 + int(m)


def in_bedtime(dt, window) -> bool:
    """Is this clock time inside the bedtime window? Windows wrap midnight."""
    if not (window or {}).get("enabled"):
        return False
    try:
        start, end = _hhmm(window["starts_at"]), _hhmm(window["ends_at"])
    except (KeyError, ValueError):
        return False
    minute = dt.hour * 60 + dt.minute
    if start == end:
        return False
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end            # 20:00 → 07:00


def parent_requests_due(effective_config, now, *, slot_count, first_slot_index=0,
                        window=None) -> list:
    """The parent's `SchedulePreferences.parent_requests[]` that fall on *today*, each
    resolved to the plan slot nearest the time they asked for.

    Shape (`cloud_config.normalize_schedule_preferences`):
    `{"parent_requests": [{"module_id": …, "scheduled_at": <epoch seconds>}]}`.
    A request earlier than "now" lands in the first slot; one later than the plan lands
    in the last; one that falls inside bedtime is clamped back to the last slot before
    bedtime (bedtime is absolute — see the module docstring). Two requests never share a
    slot: the earlier `scheduled_at` keeps it.
    """
    cfg = effective_config if isinstance(effective_config, dict) else {}
    prefs = cfg.get("schedule_preferences") or {}
    items = prefs.get("parent_requests") if isinstance(prefs, dict) else None
    out, taken = [], set()
    for item in sorted([i for i in (items or ()) if isinstance(i, dict)],
                       key=lambda i: i.get("scheduled_at") or 0):
        mid = str(item.get("module_id") or "").strip().upper()
        raw = item.get("scheduled_at")
        if not mid or not isinstance(raw, (int, float)) or isinstance(raw, bool):
            continue
        try:
            when = datetime.datetime.fromtimestamp(float(raw))
        except (OverflowError, OSError, ValueError):
            continue
        entry = {"module_id": mid, "scheduled_at": int(raw),
                 "at": when.strftime("%H:%M"), "due_today": when.date() == now.date(),
                 "slot": None}
        if entry["due_today"] and slot_count > 0:
            offset = (when - now).total_seconds() / 60.0 / SLOT_MINUTES
            slot = int(round(offset)) - first_slot_index
            slot = max(0, min(slot_count - 1, slot))
            if window and window.get("enabled"):
                while slot > 0 and in_bedtime(
                        now + datetime.timedelta(
                            minutes=(first_slot_index + slot) * SLOT_MINUTES), window):
                    slot -= 1
                    entry.setdefault("reason_codes", []).append("bedtime_clamped")
            while slot in taken and slot + 1 < slot_count:
                slot += 1
            if slot not in taken:
                taken.add(slot)
                entry["slot"] = slot
        out.append(entry)
    return out


def telemetry_signals(telemetry_summary, packets=()) -> dict:
    """What the recovered telemetry envelope can honestly tell a planner.

    `Packet{model, version, recorded_at, moxie_id, moxie_session_id, user_id, event_name,
    event_data}` (device-config-and-telemetry.md §"The telemetry envelope"). `event_name`
    is a free string and `event_data` opaque bytes: **our RE corpus recovers no
    module-scoped event vocabulary**, so nothing here says "the child launched STORY and
    quit after 40 s". `carries_module_signal` is therefore False and completion affinity
    comes from `mentor_behaviors`. What is real: how many packets, which event names, and
    a `recorded_at` histogram of when this robot is awake — reported for the parent.
    """
    summary = telemetry_summary if isinstance(telemetry_summary, dict) else {}
    by_event = summary.get("by_event") if isinstance(summary.get("by_event"), dict) else {}
    items = [p for p in (packets or ()) if isinstance(p, dict)]
    if not items:
        items = [p for p in (summary.get("latest") or ()) if isinstance(p, dict)]
    hours: dict = {}
    sessions = set()
    for pkt in items:
        ts = pkt.get("recorded_at")
        if isinstance(ts, (int, float)) and not isinstance(ts, bool):
            try:
                bucket = time_bucket(datetime.datetime.fromtimestamp(float(ts)))
            except (OverflowError, OSError, ValueError):
                continue
            hours[bucket] = hours.get(bucket, 0) + 1
        if pkt.get("moxie_session_id"):
            sessions.add(pkt["moxie_session_id"])
    return {"count": int(summary.get("count") or len(items)),
            "by_event": dict(by_event), "sessions": len(sessions),
            "active_buckets": hours, "carries_module_signal": False,
            "note": "Packet.event_name is a free string in the recovered proto; no "
                    "module launch/exit vocabulary is established, so completion "
                    "affinity comes from mentor_behaviors only."}


# -------------------------------------------------------------------------- inputs ----

def _schedules_entries(content_schedules) -> list:
    """Normalize a content module's `schedules[]` — dicts *or* `ContentModule` schedule
    objects — into `[{"name": str, "schedule": dict}]`."""
    out = []
    for item in content_schedules or ():
        if isinstance(item, dict):
            name, sched = item.get("name", ""), item.get("schedule")
        else:
            name, sched = getattr(item, "name", ""), getattr(item, "schedule", None)
        if isinstance(sched, dict) and sched:
            out.append({"name": str(name or ""), "schedule": sched})
    return out


def select_template(content_schedules, *, bucket: str = "", name: str = "") -> dict:
    """Pick the `schedules[]` entry to plan from.

    Order: an explicit `name`; then an entry named after the current time-of-day bucket
    (`morning`/`afternoon`/`evening`/`night`) so a module can ship a wind-down day; then
    the first entry; then `DEFAULT_TEMPLATE`. Read-only — nothing is mutated.
    """
    entries = _schedules_entries(content_schedules)
    if not entries:
        return dict(DEFAULT_TEMPLATE)
    if name:
        for e in entries:
            if e["name"] == name:
                return dict(e["schedule"])
        return dict(DEFAULT_TEMPLATE)
    if bucket:
        for e in entries:
            if e["name"].lower() == bucket:
                return dict(e["schedule"])
    return dict(entries[0]["schedule"])


def plan_inputs(device_id, now=None, *, mentor_behaviors=(), telemetry_summary=None,
                effective_config=None, content_schedules=None, catalog=None,
                template=None, day: str = "", child_name: str = "",
                telemetry_packets=()) -> dict:
    """Gather every signal `plan_day` is allowed to see, as a JSON-safe dict.

    Pure: no store, no clock of its own (pass `now`), no MQTT. The returned object is
    exactly what `GET /schedule` shows a parent as the "inputs summary", so it doubles as
    the audit trail for a plan.
    """
    now = _parse_iso(now) if now is not None else datetime.datetime.now()
    day = day or now.date().isoformat()
    bucket = time_bucket(now)
    if template is None:
        template = select_template(content_schedules, bucket=bucket)
    template = dict(template)
    gen = template.get("generate") or {}
    history = module_history(mentor_behaviors)
    counts = completed_counts(mentor_behaviors)
    skips = ftue_skips(counts)
    window = bedtime_window(effective_config, now)

    prefix = [r for r in _recommendation_list(template.get("provided_schedule"))
              if r.get("module_id") not in skips]
    module_count = int(gen.get("module_count", 0) or 0)
    slots = []
    for i in range(module_count):
        at = now + datetime.timedelta(minutes=(len(prefix) + i) * SLOT_MINUTES)
        slots.append({"index": i, "at": at.strftime("%H:%M"),
                      "bucket": time_bucket(at), "in_bedtime": in_bedtime(at, window)})
    requests = parent_requests_due(effective_config, now, slot_count=module_count,
                                   first_slot_index=len(prefix), window=window)

    base = list(catalog if catalog is not None else ONBOARD_MODULES)
    return {
        "device_id": str(device_id or ""), "day": day, "now": _iso(now),
        "bucket": bucket, "slot_minutes": SLOT_MINUTES,
        "child_name": str(child_name or "") or DEFAULT_CHILD_NAME,
        "template": template,
        "catalog": [dict(m) for m in base],
        "history": history, "completed_counts": counts,
        "ftue_skips": sorted(skips),
        "bedtime": window, "slots": slots, "parent_requests": requests,
        "telemetry": telemetry_signals(telemetry_summary, telemetry_packets),
    }


# ------------------------------------------------------------------------- scoring ----

def score_module(module, *, inputs, slot, used_categories=(), now=None) -> tuple:
    """Score one candidate for one slot. Returns `(score, factors, reason_codes)`.

    Every term is named in `factors` so `GET /schedule` can show a parent the arithmetic
    and a test can isolate one factor by zeroing its neighbours.
    """
    mid = module.get("module_id", "")
    category = str(module.get("category") or "UNASSIGNED").upper()
    now = now or _parse_iso(inputs["now"])
    hist = (inputs.get("history") or {}).get(mid) or {}
    factors, codes = {}, []

    if mid in FTUE_COMPLETION_COUNTS and mid not in set(inputs.get("ftue_skips") or ()):
        factors["ftue"] = W_FTUE
        codes.append("ftue")

    seen = int(hist.get("seen") or 0)
    factors["coverage"] = -W_TIER * min(seen, MAX_TIER)
    if seen == 0:
        codes.append("unseen")

    age = _age_days(hist.get("last_ts"), now)
    if age is None:
        factors["recency"] = 0
    elif age < 1:
        factors["recency"] = RECENCY_SAME_DAY
        codes.append("just_played")
    elif age < RECENCY_WINDOW_DAYS:
        factors["recency"] = RECENCY_3_DAY
        codes.append("played_recently")
    else:
        factors["recency"] = 0
        if seen:
            codes.append("rested")

    completed, abandoned = int(hist.get("completed") or 0), int(hist.get("abandoned") or 0)
    if completed or abandoned:
        rate = completed / float(completed + abandoned)
        factors["affinity"] = AFFINITY_FLOOR + int(round((AFFINITY_MAX - AFFINITY_FLOOR)
                                                         * rate))
        codes.append("finishes" if rate >= 0.5 else "abandons")
    else:
        factors["affinity"] = AFFINITY_NEUTRAL

    at = now + datetime.timedelta(minutes=slot * SLOT_MINUTES)
    energy = category_energy(category)
    factors["time_of_day"] = TIME_FIT.get(time_bucket(at), {}).get(energy, 0)
    if factors["time_of_day"] >= max(TIME_FIT.get(time_bucket(at), {}).values() or [0]):
        codes.append("time_of_day")

    repeats = list(used_categories).count(category)
    factors["category_spread"] = -CATEGORY_REPEAT_PENALTY * repeats
    factors["tiebreak"] = _tiebreak(inputs.get("device_id", ""), inputs.get("day", ""), mid)
    return sum(factors.values()), factors, codes


# -------------------------------------------------------------------- explanations ----

def clock_label(value) -> str:
    """"16:00" → "4:00 pm" — the way a parent reads a time. Built by hand rather than with
    `%-I`, which is not portable."""
    try:
        hour, _, minute = str(value).partition(":")
        h, m = int(hour), int(minute)
    except ValueError:
        return str(value)
    suffix = "am" if h < 12 else "pm"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {suffix}"


def _sentence(text: str) -> str:
    """A line a parent reads: first letter capitalized (the child's nickname may not be —
    `ChildProfile.nickname` defaults to "friend")."""
    return text[:1].upper() + text[1:] if text else text


def explain(rec, *, reason_codes, inputs, hist=None, at=None, requested_at="") -> str:
    """One short, parent-readable sentence for one entry in the day."""
    child = inputs.get("child_name") or DEFAULT_CHILD_NAME
    label = module_label(rec)
    hist = hist or {}
    when = f" in the {time_bucket(at)} slot" if at is not None else ""
    if "parent_request" in reason_codes:
        asked = clock_label(requested_at) if requested_at else "today"
        drift = None
        if at is not None and requested_at:
            try:
                drift = (at.hour * 60 + at.minute) - _hhmm(requested_at)
            except ValueError:
                drift = None
        if drift is not None and abs(drift) > SLOT_MINUTES:
            side = "starts later than that" if drift > 0 else "ends before that"
            return _sentence(
                f"Requested by a parent for {asked} — this session {side}, so {label} "
                f"is queued at {clock_label(at.strftime('%H:%M'))} instead.")
        return _sentence(f"Requested by a parent for {asked} — {label} is pinned to "
                         f"that slot.")
    if "ftue" in reason_codes:
        return _sentence(f"{label} is part of Moxie's first-week onboarding, "
                         f"which is still running.")
    if "fixture" in reason_codes:
        return _sentence(f"{label} is a daily fixture — it runs every day.")
    if "chat" in reason_codes:
        return _sentence(f"A free chat, so {child} gets a breather between activities.")
    if "finishes" in reason_codes:
        done = int(hist.get("completed") or 0)
        times = "once" if done == 1 else f"{done} times"
        return _sentence(f"{child} finished {label} {times} — "
                         f"scheduling it{when or ' again today'}.")
    if "abandons" in reason_codes:
        left = int(hist.get("abandoned") or 0)
        times = "once" if left == 1 else f"{left} times"
        return _sentence(f"{child} has left {label} early {times} — kept in the "
                         f"rotation for variety, but no longer a top pick.")
    if "unseen" in reason_codes:
        return _sentence(f"{child} has not tried {label} yet — new for today{when}.")
    if "rested" in reason_codes:
        return _sentence(f"{label} has had a rest since it last came up — "
                         f"bringing it back{when}.")
    return _sentence(f"{label} fits{when or ' today'}.")


# --------------------------------------------------------------------- the planner ----

def _interleave(activities: list, chats: list) -> list:
    """Spread `chats` evenly between `activities` (a chat should never bookend the day)."""
    if not chats:
        return list(activities)
    if not activities:
        return list(chats)
    out = list(activities)
    gap = max(1, len(activities) // (len(chats) + 1))
    offset = 0
    for chat in chats:
        pos = min(len(out), offset + gap)
        out.insert(pos, chat)
        offset = pos + 1
    return out


def schedule_template(content_module=None, name: str = "") -> dict:
    """The authoring template to plan from: a `schedules[]` entry of a loaded content
    module (`moxie_sdk.content.module.ContentModule`), by `name` or the first one.
    Falls back to `DEFAULT_TEMPLATE`. Read-only — the module is never mutated."""
    return select_template(getattr(content_module, "schedules", None), name=name)


def plan_day(inputs: dict) -> tuple:
    """`inputs` (from `plan_inputs`) → `(ContentSchedule, explanations)`.

    Pure and deterministic: same inputs, same bytes, in any process. The schedule is
    byte-compatible with what the pre-recommender builder emitted — only `ContentSchedule`
    fields, only `Recommendation` keys inside them. `explanations` is a parallel list
    `[{module_id, slot, at, reason_codes, line, score, factors}]`, one per entry in
    `provided_schedule`, and never goes on the wire.
    """
    now = _parse_iso(inputs["now"])
    template = dict(inputs.get("template") or DEFAULT_TEMPLATE)
    gen = template.pop("generate", None) or {}
    skips = set(inputs.get("ftue_skips") or ())
    history = inputs.get("history") or {}
    window = inputs.get("bedtime") or {}
    requests = inputs.get("parent_requests") or []
    pinned = {r["slot"]: r for r in requests if r.get("slot") is not None}
    requested_ids = {r["module_id"]: r for r in requests if r.get("due_today")}

    # 1. the pinned spine, exactly as before: authored order, FTUE pruned.
    entries = []                                  # [(Recommendation, explanation)]
    for rec in _recommendation_list(template.get("provided_schedule")):
        mid = rec["module_id"]
        if mid in skips:
            continue
        codes = ["ftue"] if mid in FTUE_COMPLETION_COUNTS else ["fixture"]
        req = requested_ids.get(mid)
        if req:
            codes.insert(0, "parent_request")
        entries.append((rec, {"module_id": mid, "slot": None, "at": None,
                              "reason_codes": codes,
                              "line": explain(rec, reason_codes=codes, inputs=inputs,
                                              hist=history.get(mid),
                                              requested_at=(req or {}).get("at", "")),
                              "score": None, "factors": {}}))
    prefix_len = len(entries)
    scheduled = {r["module_id"] for r, _ in entries}

    # 2. the scored fill.
    if gen:
        excluded = set(gen.get("excluded_module_ids") or ()) | skips | scheduled
        pool = {m["module_id"]: dict(m) for m in (inputs.get("catalog") or ())
                if m.get("module_id") and m["module_id"] not in excluded}
        for extra in gen.get("extra_modules") or ():
            rec = _recommendation(extra)
            if rec and rec["module_id"] not in excluded:
                pool[rec["module_id"]] = {**rec,
                                          "category": (extra or {}).get("category", "USER")}
        # A parent request outranks the template's own exclusions: if they asked for it
        # today and it is not already in the spine, it goes back in the pool.
        for mid, req in requested_ids.items():
            if req.get("slot") is not None and mid not in pool and mid not in scheduled:
                base = next((dict(m) for m in (inputs.get("catalog") or ())
                             if m.get("module_id") == mid), None)
                if base:
                    pool[mid] = base

        activities = []
        used_categories: list = []
        last_category = None
        for slot in range(int(gen.get("module_count", 0) or 0)):
            at = now + datetime.timedelta(minutes=(prefix_len + slot) * SLOT_MINUTES)
            if in_bedtime(at, window):
                break                              # never plan into bedtime
            pin = pinned.get(slot)
            chosen = None
            if pin and pin["module_id"] in pool:
                chosen = pool[pin["module_id"]]
                score, factors, codes = score_module(
                    chosen, inputs=inputs, slot=prefix_len + slot,
                    used_categories=used_categories, now=now)
                factors["parent_request"] = W_PARENT_REQUEST
                score += W_PARENT_REQUEST
                codes = ["parent_request"] + [c for c in codes if c != "parent_request"]
            else:
                candidates = [m for m in pool.values()
                              if str(m.get("category") or "") != last_category]
                if not candidates:
                    candidates = list(pool.values())
                best = None
                for m in candidates:
                    s, f, c = score_module(m, inputs=inputs, slot=prefix_len + slot,
                                           used_categories=used_categories, now=now)
                    key = (s, m["module_id"])
                    if best is None or key > best[0]:
                        best = (key, m, s, f, c)
                if best is None:
                    break
                _, chosen, score, factors, codes = best
                if len(candidates) < len(pool):
                    codes = codes + ["variety"]
            pool.pop(chosen["module_id"], None)
            rec = _recommendation(chosen)
            if not rec:
                continue
            category = str(chosen.get("category") or "")
            used_categories.append(category.upper())
            last_category = category
            req = requested_ids.get(rec["module_id"]) if "parent_request" in codes else None
            activities.append((rec, {
                "module_id": rec["module_id"], "slot": prefix_len + slot,
                "at": at.strftime("%H:%M"), "reason_codes": codes,
                "line": explain(rec, reason_codes=codes, inputs=inputs,
                                hist=history.get(rec["module_id"]), at=at,
                                requested_at=(req or {}).get("at", "")),
                "score": score, "factors": factors}))

        chat_modules = _recommendation_list(gen.get("chat_modules"))
        chat_count = int(gen.get("chat_count", 0) or 0)
        chats = []
        for i in range(chat_count if chat_modules else 0):
            rec = dict(chat_modules[i % len(chat_modules)])
            chats.append((rec, {"module_id": rec["module_id"], "slot": None, "at": None,
                                "reason_codes": ["chat"],
                                "line": explain(rec, reason_codes=["chat"], inputs=inputs),
                                "score": None, "factors": {}}))
        entries += _interleave(activities, chats)

    template["provided_schedule"] = [rec for rec, _ in entries]
    return _normalize_schedule(template), [expl for _, expl in entries]


def plan(device_id: str = "", *, template=None, mentor_behaviors=(), day: str = "",
         now=None, effective_config=None, telemetry_summary=None, telemetry_packets=(),
         content_schedules=None, catalog=None, child_name: str = "") -> tuple:
    """`plan_inputs` + `plan_day` in one call → `(schedule, explanations, inputs)`.
    This is what the runtime uses; `build_schedule` is the schedule-only shorthand."""
    inputs = plan_inputs(device_id, now, mentor_behaviors=mentor_behaviors,
                         telemetry_summary=telemetry_summary,
                         telemetry_packets=telemetry_packets,
                         effective_config=effective_config,
                         content_schedules=content_schedules, catalog=catalog,
                         template=template, day=day, child_name=child_name)
    sched, explanations = plan_day(inputs)
    gen = (inputs.get("template") or {}).get("generate") or {}
    wanted = int(gen.get("module_count", 0) or 0)
    got = sum(1 for e in explanations if e.get("slot") is not None)
    inputs["planned"] = {
        "activities": got, "requested": wanted,
        "dropped_for_bedtime": max(0, wanted - got) if inputs.get(
            "bedtime", {}).get("enabled") else 0,
        "entries": len(explanations),
    }
    return sched, explanations, inputs


def build_schedule(template: dict | None = None, *, mentor_behaviors=(),
                   device_id: str = "", day: str = "", now=None,
                   effective_config=None, telemetry_summary=None,
                   telemetry_packets=(), catalog=None, child_name: str = "") -> dict:
    """Build one session's `ContentSchedule` (the value of `CloudQueryResponse.schedule`).

    `template` — a `schedules[]`-style dict; `None` uses `DEFAULT_TEMPLATE`.
    `mentor_behaviors` — this robot's stored MentorBehavior records (what it has done).
    `device_id` + `day` — the deterministic seed. `day` defaults to `now`'s date, so the
    plan is stable for a whole day and different tomorrow; pass it explicitly to pin it.
    `now` — the clock the plan is laid out against (slot times, time-of-day fit, bedtime,
    "is this parent request due today"); defaults to the local wall clock.
    `effective_config` — the robot's merged config, for `schedule_preferences` and the
    bedtime windows. `telemetry_summary` — `telemetry.summarize_events` output.

    Returns a dict containing only `ContentSchedule` fields, with a non-empty
    `provided_schedule` whenever the template or the catalog can supply one. Use `plan()`
    when the explanations are wanted too.
    """
    sched, _, _ = plan(device_id, template=template, mentor_behaviors=mentor_behaviors,
                       day=day, now=now, effective_config=effective_config,
                       telemetry_summary=telemetry_summary,
                       telemetry_packets=telemetry_packets, catalog=catalog,
                       child_name=child_name)
    return sched
