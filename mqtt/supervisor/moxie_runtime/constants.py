"""Module-level tunables and policy defaults shared by the runtime mixins."""
import re

from moxie_sdk.cloud_config import LoggingPolicy

CONNECT_RE = re.compile(r"connected from (.*) as (d_[a-f0-9-]+)", re.I)
DISCONNECT_RE = re.compile(r"Client (d_[a-f0-9-]+) (?:closed its connection|disconnected)", re.I)

# paho's third `connect()` argument is the keepalive, not a timeout (production-hardening.md
# §4.1). 30 s halves paho's default so a half-open socket (NAT/Wi-Fi drop) is noticed within
# ~30 s; the broker declares us dead at 1.5x keepalive.
KEEPALIVE_S = 30

# paho's reconnect ladder: 1 s doubling to 60 s. Chosen, not measured: a router reboot is
# ~30-60 s, and 120 s (paho's default) is two minutes of a child talking to nothing.
RECONNECT_MIN_DELAY_S = 1
RECONNECT_MAX_DELAY_S = 60

# Rolling window of MentorBehavior records kept per robot (recommender/FTUE need recent only).
MAX_MENTOR_BEHAVIORS = 500
# `robots/<id>/mentor_behaviors.json` — named once so ingest, read and erase cannot drift.
MENTOR_BEHAVIORS_COLLECTION = "mentor_behaviors"

# How long a brain call may run before we say *something*. The robot re-prompts after ~20 s
# of cloud silence, so this leaves room for a filler plus the real answer. 0 disables it.
DEFAULT_BRAIN_BUDGET_S = 6.0

# Fillers one turn may spend: one buys ~20 s; a slow stream may re-arm once more, then silence.
MAX_FILLERS_PER_TURN = 2

# The three LoggingPolicy defaults below govern records THIS server keeps about turns that
# already reached it — distinct from the pushed RobotCloudConfig (default NO_DATA), which
# gates what the robot uploads. A parent's one `logging_policy` field resolves all three.

# Safety journal: rows (category, time, redacted excerpt) unless NO_DATA -> counts only.
SAFETY_JOURNAL_POLICY = LoggingPolicy.NO_MEDIA

# Long-term memory: gates BOTH the module facts (`MemoryStore.writes_allowed`) and the
# conversation transcript under MOXIE_MEMORY_DIR (`_save_memory`). NO_DATA stops writes;
# reads and erase still work.
MEMORY_POLICY = LoggingPolicy.NO_MEDIA

# The activity record — telemetry_packets.json, telemetry_daily.json, mentor_behaviors.json.
# NO_MEDIA keeps Packet envelopes but withholds opaque `event_data` (untyped bytes might be
# audio); NO_DATA writes nothing and `purge_telemetry` erases what exists; FULL keeps
# payloads. See telemetry.py::storable_packet and config-and-telemetry-contract.md §3.
TELEMETRY_POLICY = LoggingPolicy.NO_MEDIA

# Seconds out of sight before Moxie greets a returning child unprompted (vision.md "The
# greeting rule"). 0 disables; MOXIE_GREET_AFTER_S overrides.
DEFAULT_GREET_AFTER_S = 300.0
