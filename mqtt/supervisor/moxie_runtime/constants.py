"""Module-level tunables and policy defaults shared by the runtime mixins."""
import re

from moxie_sdk.cloud_config import LoggingPolicy

# paho is imported lazily in _build_client() so the runtime + turn pipeline can be
# imported and integration-tested without the broker client installed.

CONNECT_RE = re.compile(r"connected from (.*) as (d_[a-f0-9-]+)", re.I)
DISCONNECT_RE = re.compile(r"Client (d_[a-f0-9-]+) (?:closed its connection|disconnected)", re.I)

# --- staying connected (docs/architecture/backlog/production-hardening.md §4.1) ---
#
# The third argument of paho's `connect(host, port, keepalive)` is the **keepalive**, not
# a timeout — the audit read it as a timeout and it is not (assumption A1). 30 s is kept
# deliberately: the broker declares us dead at 1.5× keepalive (45 s) and paho notices a
# missing PINGRESP within one keepalive, so halving paho's default 60 halves the
# worst-case detection of a *half-open* socket — the failure a NAT table or a Wi-Fi drop
# actually produces, where the connection is gone but nobody has said so.
KEEPALIVE_S = 30

# The reconnect ladder paho walks after a drop: 1 s, doubling, capped. **Chosen, not
# measured** (A14). 60 rather than paho's own 120 because a house's router reboot is
# ~30-60 s and a 120 s ceiling is up to two minutes of a child talking to nothing; rather
# than Fork A's 30 because we would rather not hammer a broker that has been down for an
# hour. No jitter: paho has none, and with a single supervisor there is no herd.
RECONNECT_MIN_DELAY_S = 1
RECONNECT_MAX_DELAY_S = 60



# How many MentorBehavior records we keep (and serve back) per robot. The history is a
# rolling window, not an archive — the recommender/FTUE checks only need recent activity.
MAX_MENTOR_BEHAVIORS = 500

# Where that history lives: `robots/<id>/mentor_behaviors.json`. Named here because it is
# now referenced from three places — the ingest, the read, and the erase — and a privacy
# erase that missed the file because someone retyped the string would be silent.
MENTOR_BEHAVIORS_COLLECTION = "mentor_behaviors"

# How long a turn's brain call may run before we say *something*. The robot re-prompts
# if the cloud stays silent for ~20 s (openmoxie-feature-audit.md:347) and a live gateway
# turn was measured at 45 s healthy / 18 s degraded (implementation-plan.md:138), so the
# default leaves room for a filler + the real answer inside one window. 0 disables it.
DEFAULT_BRAIN_BUDGET_S = 6.0

# How many filler lines one turn may spend. One buys a ~20 s window; a 45 s brain
# outlives it, so a stalled stream may re-arm exactly once more. Past that the child is
# better served by silence than by a robot that only ever says it is thinking.
MAX_FILLERS_PER_TURN = 2

# The safety journal's own LoggingPolicy default. The RobotCloudConfig we push defaults to
# `NO_DATA` (cloud_config.py), but that gate is about what the *robot uploads to us*; the
# review queue is a record our own server keeps about turns that already reached it. So the
# journal keeps rows (category + timestamp + a redacted excerpt) unless a parent explicitly
# sets data sharing to NO_DATA, which switches it to counts only.
SAFETY_JOURNAL_POLICY = LoggingPolicy.NO_MEDIA

# Long-term memory's own LoggingPolicy default, for the same reason as the safety
# journal's: the RobotCloudConfig we push defaults to NO_DATA, which is about what the
# *robot uploads*. Memory is text our own server derives from turns that already reached
# it, so it defaults to NO_MEDIA (allowed) — and a parent who explicitly sets
# `logging_policy=NO_DATA` turns writing off entirely (reads and erase still work).
#
# This constant governs **both** durable memories, because a child's parent has one
# switch and not two: the module facts in `moxie_sdk/store.py::MemoryStore` (gated in
# `MemoryStore.writes_allowed`, resolved from `memory_policy`) *and* the rolling
# conversation transcript under `MOXIE_MEMORY_DIR` (gated in `_save_memory`, resolved
# from `transcript_persists` → the same `memory_policy`). The transcript was ungated
# until the gate below landed, which made this comment's promise false on the one path
# that stores the child's words verbatim.
MEMORY_POLICY = LoggingPolicy.NO_MEDIA

# Telemetry's own LoggingPolicy default, for the same reason as the two above: what a
# robot uploads is gated on the robot by `RobotCloudConfig.data_sharing`, and a Packet
# that reached us already passed that gate. What THIS constant governs is narrower and
# stricter — whether the packet is written to disk, and with its `event_data` payload or
# without. NO_MEDIA keeps the envelope (event name + timestamps + session) and withholds
# every opaque payload, because `Packet.event_data` is `bytes` with no recovered type
# vocabulary and a store that guessed "this blob is not audio" would be a privacy
# incident, not a bug. A parent who sets `logging_policy=NO_DATA` gets nothing on disk at
# all (`storable_packet` returns None and `_persist_telemetry` writes nothing — not the
# packet, not a count, not a day row); one who sets FULL gets payloads too. See
# `moxie_sdk/telemetry.py::storable_packet` and
# `docs/architecture/config-and-telemetry-contract.md` §③.
#
# This constant governs the whole **activity record** — all THREE files in which this
# appliance writes down what the child did — because a parent has one switch and not
# three that could disagree:
#
#   robots/<id>/telemetry_packets.json   the ring of Packet envelopes
#   robots/<id>/telemetry_daily.json     the calendar-day roll-up
#   robots/<id>/mentor_behaviors.json    which activity was finished, quit or refused
#
# The third one was ungated until `ingest_mentor_behavior`'s gate landed. It belongs
# here and not with `MEMORY_POLICY` because a `MentorBehavior` is a *report the robot
# uploads* on `client-service-activity-log` — the same kind of thing as a `Packet`, and
# the thing `LoggingPolicy` is about — rather than a fact a content module chose to
# remember (that is `MemoryStore`, gated on `memory_policy`). Both constants resolve the
# parent's one `logging_policy` field; only their defaults are separate.
#
# And "nothing on disk at all" is now true retroactively as well as going forward:
# `erase_telemetry` (`DELETE /telemetry?device_id=…`) removes all three files, and
# `purge_telemetry` runs it for every NO_DATA robot at boot and on any config edit that
# could have moved the switch. Before that landed there was no erasure path at all —
# `do_DELETE` accepted only `/memory` — so flipping to NO_DATA stopped new writes and
# left the ring where it was, with nothing a parent could press.
TELEMETRY_POLICY = LoggingPolicy.NO_MEDIA

# How long a child must have been out of sight before Moxie says hello on its own when
# they walk back in front of it (`eb-found-face` after an `eb-lost-target`). Short enough
# that leaving the room and coming back is noticed, long enough that stepping out of frame
# for a moment is not. 0 turns the unprompted greeting off entirely; `MOXIE_GREET_AFTER_S`
# overrides. See docs/architecture/vision.md "The greeting rule".
DEFAULT_GREET_AFTER_S = 300.0
