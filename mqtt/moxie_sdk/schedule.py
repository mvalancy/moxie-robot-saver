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

### What v1 does

Deterministic, given (template, mentor_behaviors, device_id, day):

1. start from a **template** — a content module's `schedules[]` entry
   (`docs/architecture/content-module-contract.md` §`schedules[]`) or `DEFAULT_TEMPLATE`;
2. drop first-time-user modules the child has already finished, so **FTUE ends**;
3. fill the rest of the day from the **on-board activity catalog**, preferring modules
   this robot has **not** completed, so **nothing repeats** until everything has run;
4. interleave chat activities between them;
5. emit **only** `ContentSchedule` fields (the `generate` block and any other authoring
   key is stripped — it is server-side, not wire).

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
import random

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

def _rotation(catalog, counts: dict, count: int, rnd) -> list:
    """Pick `count` activities: never-completed ones first (so nothing repeats until the
    catalog is exhausted), then the least-completed, shuffled deterministically, and
    ordered so no two adjacent picks share a category.

    (The variety goal is OpenMoxie's `ransac_select`; this is a deterministic greedy pass
    rather than 20 random samples, so the same inputs always produce the same day.)"""
    pool = [m for m in catalog if m.get("module_id")]
    rnd.shuffle(pool)
    pool.sort(key=lambda m: counts.get(m["module_id"], 0))   # stable: fewest-done first
    picked, last_cat = [], None
    while pool and len(picked) < count:
        idx = next((i for i, m in enumerate(pool) if m.get("category") != last_cat), 0)
        m = pool.pop(idx)
        picked.append(m)
        last_cat = m.get("category")
    return picked


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
    schedules = list(getattr(content_module, "schedules", None) or ())
    for s in schedules:
        sched = getattr(s, "schedule", None)
        if not sched:
            continue
        if not name or getattr(s, "name", "") == name:
            return dict(sched)
    return dict(DEFAULT_TEMPLATE)


def build_schedule(template: dict | None = None, *, mentor_behaviors=(),
                   device_id: str = "", day: str = "") -> dict:
    """Build one session's `ContentSchedule` (the value of `CloudQueryResponse.schedule`).

    `template` — a `schedules[]`-style dict; `None` uses `DEFAULT_TEMPLATE`.
    `mentor_behaviors` — this robot's stored MentorBehavior records (what it has done).
    `device_id` + `day` — the deterministic seed. `day` defaults to today (ISO), so the
    plan is stable for a whole day and different tomorrow; pass it explicitly to pin it.

    Returns a dict containing only `ContentSchedule` fields, with a non-empty
    `provided_schedule` whenever the template or the catalog can supply one.
    """
    template = dict(template if template is not None else DEFAULT_TEMPLATE)
    gen = template.pop("generate", None) or {}
    counts = completed_counts(mentor_behaviors)
    skips = ftue_skips(counts)

    provided = [r for r in _recommendation_list(template.get("provided_schedule"))
                if r.get("module_id") not in skips]
    scheduled = {r["module_id"] for r in provided}

    if gen:
        excluded = set(gen.get("excluded_module_ids") or ()) | skips | scheduled
        catalog = [dict(m) for m in ONBOARD_MODULES if m["module_id"] not in excluded]
        for extra in gen.get("extra_modules") or ():
            rec = _recommendation(extra)
            if rec and rec["module_id"] not in excluded:
                catalog.append({**rec, "category": (extra or {}).get("category", "USER")})
        rnd = random.Random(f"{device_id}|{day or datetime.date.today().isoformat()}")
        picks = _rotation(catalog, counts, int(gen.get("module_count", 6) or 0), rnd)
        activities = [_recommendation(m) for m in picks]
        activities = [a for a in activities if a]

        chat_modules = _recommendation_list(gen.get("chat_modules"))
        chat_count = int(gen.get("chat_count", 0) or 0)
        chats = [dict(chat_modules[i % len(chat_modules)]) for i in range(chat_count)] \
            if chat_modules else []
        provided += _interleave(activities, chats)

    template["provided_schedule"] = provided
    return _normalize_schedule(template)
