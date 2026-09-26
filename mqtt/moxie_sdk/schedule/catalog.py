"""The on-board catalog, wire field names, and every planner weight and table.

Every planner number lives here so a test can isolate one factor."""
from __future__ import annotations


# --- ContentSchedule / Recommendation field names (see the module docstring) ---
SCHEDULE_FIELDS = ("restricted_modules", "tags", "provided_schedule", "config",
                   "end_of_session", "chat_request", "wake_module", "rewards",
                   "mission_config", "hub_config", "alarm_module")

RECOMMENDATION_FIELDS = ("module_id", "content_id", "entry_line", "module_name",
                         "module_description", "seen", "skip_hub")

# Which ContentSchedule fields hold a Recommendation (normalized on the way out).
_RECOMMENDATION_FIELDS_IN_SCHEDULE = ("chat_request", "wake_module", "alarm_module")

# The on-board activity catalog — firmware modules the cloud can only *schedule* by id
# (`docs/architecture/mqtt-and-conversation.md`:526). `DM` (Daily Missions) is a daily
# fixture carried in DEFAULT_TEMPLATE, not the rotation.
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

# First-time-user experience: onboarding modules and how many COMPLETED reports mean
# "done" (WELCOME retires on any completion). The counts are OpenMoxie's field-proven
# constants (`site/hive/content/data.py`: TNT_CIDS=9, SYSTEMSCHECK_CIDS=4), not ours: our
# RE names the modules but not the counts, and the robot repeats them at random once done.
FTUE_COMPLETION_COUNTS = {"WELCOME": 1, "TNT": 9, "SYSTEMSCHECK": 4}

# "The child finished this" (`MentorAction.COMPLETED`, MentorBehavior.proto:8).
COMPLETED = "COMPLETED"

# "The child bailed out". Any other action is "offered": counts for coverage, not affinity.
ABANDONED = ("QUIT", "REFUSED")


# ---------------------------------------------------------------- the recommender ----
# Every planner number lives here so a test can isolate one factor (see the docstring table).

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

# How long one activity notionally occupies — OURS (the robot's own time-box limit is not
# recovered). Gives each slot a clock time for time-of-day fit, bedtime and parent requests.
SLOT_MINUTES = 10

# Time-of-day buckets (local wall clock). `night` wraps midnight.
TIME_BUCKETS = (("morning", 5, 12), ("afternoon", 12, 17), ("evening", 17, 21),
                ("night", 21, 5))

# `ModuleDetail.ModuleCategory` (ContentModule.proto:46-60) → the energy it asks of a child.
# The one judgement call, made per category so it stays as small as the enum.
CATEGORY_ENERGY = {
    "MOVEMENT": "energetic", "PLAYFUL_GAME": "energetic",
    "CREATIVITY": "neutral", "FUN_TIDBIT": "neutral", "PUZZLE_GAME": "neutral",
    "MISSION": "neutral", "CONVERSATION": "neutral",
    "REGULATION": "calm", "LISTENING": "calm", "READING": "calm",
}
DEFAULT_ENERGY = "neutral"     # UNASSIGNED / UTILITY / OTHER / an authored `USER` category

# Energetic early, calm late. Nothing is forbidden by time of day, only re-ranked.
TIME_FIT = {
    "morning":   {"energetic": 120, "neutral": 60,  "calm": 0},
    "afternoon": {"energetic": 60,  "neutral": 120, "calm": 60},
    "evening":   {"energetic": 0,   "neutral": 60,  "calm": 120},
    "night":     {"energetic": -60, "neutral": 0,   "calm": 120},
}

# Parent-readable labels. Only unambiguous ids are mapped (others show verbatim rather than
# an invented product name); a template's `Recommendation.module_name` always wins.
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

# Used when the caller does not pass the child's name (the planner has no ChildProfile).
DEFAULT_CHILD_NAME = "Your child"

# Default day: onboarding, Daily Missions, then a generated rotation. `generate` is an
# authoring key (content-module-contract.md §schedules[]) and never goes on the wire.
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
